from datetime import datetime, timezone
from pathlib import PurePosixPath

from .settings import Held, Settings, SearchScope
from .torrent import SAFE_EXTRA, VIDEO, Torrent

DISK_REJECTIONS = (
    "Existing file on disk has a equal or higher Custom Format score:",
    "Existing file meets cutoff:",
    "Existing file on disk meets quality cutoff:",
    "Existing file on disk meets Custom Format cutoff:",
)


def is_missing(episode, now=None):
    if episode.get("hasFile") or episode.get("episodeFileId", 0) or not episode.get("monitored"):
        return False
    try:
        aired = datetime.fromisoformat(episode["airDateUtc"].replace("Z", "+00:00"))
        return aired <= (now or datetime.now(timezone.utc))
    except (KeyError, ValueError, TypeError):
        return False


def validate_release(release, scope: SearchScope, settings: Settings, indexer: dict, series: dict):
    if release.get("mappedSeriesId") != scope.series_id or release.get("indexerId") != indexer.get("id"):
        raise Held("wrong_release_identity")
    season = release.get("mappedSeasonNumber") or release.get("seasonNumber")
    if season != scope.season or not release.get("downloadAllowed"):
        raise Held("wrong_release_season_or_download_disallowed")
    if not indexer.get("enableAutomaticSearch") or indexer.get("implementation") != "Torznab" or indexer.get("protocol") != "torrent":
        raise Held("indexer_not_enabled_for_automatic_torrent_search")
    if indexer.get("tags") and not set(indexer["tags"]).intersection(series.get("tags", [])):
        raise Held("indexer_tags_do_not_match_series")
    if not release.get("fullSeason") and max(len(set(release.get(key) or [])) for key in ("episodeNumbers", "absoluteEpisodeNumbers", "mappedEpisodeNumbers")) < 2:
        raise Held("not_a_multi_episode_pack")
    if (release.get("seeders") or 0) < settings.min_seeders:
        raise Held("not_enough_seeders")
    if any(not r.startswith(DISK_REJECTIONS) for r in release.get("rejections", [])):
        raise Held("release_has_other_rejections")
    if not release.get("quality", {}).get("quality", {}).get("id"):
        raise Held("unknown_release_quality")


def plan(torrent: Torrent, scope: SearchScope, series: dict, episodes: list, parse_file, *, max_bytes: int, queued_episode_ids=frozenset()):
    if not series.get("monitored") or series.get("id") != scope.series_id:
        raise Held("series_not_monitored_or_wrong_identity")
    # Episode monitoring is authoritative: Sonarr permits individual monitored
    # episodes in a season whose bulk monitoring flag is off.
    if not any(s["seasonNumber"] == scope.season for s in series.get("seasons", [])):
        raise Held("season_not_in_catalog")
    if torrent.total_bytes > max_bytes:
        raise Held("pack_exceeds_size_limit")
    catalog = {e["id"]: e for e in episodes if e["seasonNumber"] == scope.season}
    wanted = []
    represented = set()
    for file in torrent.files:
        if file.extension not in VIDEO:
            if file.extension not in SAFE_EXTRA:
                raise Held("unsupported_file_in_pack")
            continue
        parsed = parse_file(PurePosixPath(file.name).name) or {}
        info = parsed.get("parsedEpisodeInfo") or {}
        numbers = info.get("episodeNumbers", [])
        if (parsed.get("series") or {}).get("id") != scope.series_id:
            raise Held("ambiguous_file_series_or_season")
        matched = parsed.get("episodes") or []
        if len(matched) != 1 or len(numbers) > 1 or len(info.get("absoluteEpisodeNumbers") or []) > 1 or info.get("fullSeason") or info.get("isMultiSeason") or info.get("special"):
            raise Held("only_single_episode_video_files_supported")
        mapped = matched[0]
        episode_id = mapped.get("id")
        if mapped.get("seriesId") != scope.series_id or mapped.get("seasonNumber") != scope.season:
            raise Held("ambiguous_file_series_or_season")
        if episode_id not in catalog or episode_id in represented or file.length < 1:
            raise Held("unknown_or_duplicate_episode_in_pack")
        represented.add(episode_id)
        episode = catalog[episode_id]
        if is_missing(episode) and episode["id"] not in queued_episode_ids:
            wanted.append({"index": file.index, "name": file.name, "length": file.length,
                           "episode_id": episode["id"], "episode_number": episode["episodeNumber"]})
    if not represented:
        raise Held("no_episode_files_in_pack")
    return {"hash": torrent.info_hash, "series_id": scope.series_id, "season": scope.season,
            "wanted": wanted, "unwanted": [f.index for f in torrent.files if f.index not in {w["index"] for w in wanted}],
            "files": [{"name": f.name, "length": f.length} for f in torrent.files],
            "selected_bytes": sum(w["length"] for w in wanted), "total_bytes": torrent.total_bytes}
