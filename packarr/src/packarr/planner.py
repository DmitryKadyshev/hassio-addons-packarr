from datetime import datetime, timezone
from pathlib import PurePosixPath
from urllib.parse import parse_qsl, urlsplit

from .settings import Held, Settings, Target
from .torrent import SAFE_EXTRA, VIDEO, Torrent

DISK_REJECTIONS = (
    "Existing file on disk has a equal or higher Custom Format score:",
    "Existing file meets cutoff:",
    "Existing file on disk meets quality cutoff:",
    "Existing file on disk meets Custom Format cutoff:",
)


def topic_key(url):
    p = urlsplit(url)
    # Ignore scheme and query ordering, but preserve the exact tracker topic identity.
    return p.netloc.lower(), p.path.rstrip("/"), tuple(sorted(parse_qsl(p.query)))


def is_missing(episode, now=None):
    if episode.get("hasFile") or episode.get("episodeFileId", 0) or not episode.get("monitored"):
        return False
    try:
        aired = datetime.fromisoformat(episode["airDateUtc"].replace("Z", "+00:00"))
        return aired <= (now or datetime.now(timezone.utc))
    except (KeyError, ValueError, TypeError):
        return False


def validate_release(release, target: Target, settings: Settings):
    if release.get("mappedSeriesId") != target.series_id or release.get("indexerId") != target.indexer_id:
        raise Held("wrong_release_identity")
    if release.get("seasonNumber") != target.season or not release.get("downloadAllowed"):
        raise Held("wrong_release_season_or_download_disallowed")
    if not any(topic_key(release.get(k, "")) == topic_key(target.topic_url) for k in ("guid", "commentUrl", "infoUrl")):
        raise Held("wrong_tracker_topic")
    if release.get("seeders", 0) < settings.min_seeders:
        raise Held("not_enough_seeders")
    if any(not r.startswith(DISK_REJECTIONS) for r in release.get("rejections", [])):
        raise Held("release_has_other_rejections")
    if not release.get("quality", {}).get("quality", {}).get("id"):
        raise Held("unknown_release_quality")


def plan(torrent: Torrent, target: Target, series: dict, episodes: list, parse_file, *, max_bytes: int):
    if not series.get("monitored") or series.get("id") != target.series_id:
        raise Held("series_not_monitored_or_wrong_identity")
    if series.get("useSceneNumbering") or series.get("seriesType", "standard") != "standard":
        raise Held("only_standard_catalog_numbering_supported")
    if not any(s["seasonNumber"] == target.season and s.get("monitored") for s in series.get("seasons", [])):
        raise Held("season_not_monitored")
    if torrent.total_bytes > max_bytes:
        raise Held("pack_exceeds_size_limit")
    catalog = {e["episodeNumber"]: e for e in episodes if e["seasonNumber"] == target.season}
    wanted = []
    represented = set()
    for file in torrent.files:
        if file.extension not in VIDEO:
            if file.extension not in SAFE_EXTRA:
                raise Held("unsupported_file_in_pack")
            continue
        parsed = parse_file(PurePosixPath(file.name).name)
        info = parsed.get("parsedEpisodeInfo") or {}
        numbers = info.get("episodeNumbers", [])
        if (parsed.get("series") or {}).get("id") != target.series_id or info.get("seasonNumber") != target.season:
            raise Held("ambiguous_file_series_or_season")
        if len(numbers) != 1 or info.get("fullSeason") or info.get("isMultiSeason") or info.get("special"):
            raise Held("only_single_episode_video_files_supported")
        number = numbers[0]
        if number not in catalog or number in represented or file.length < 1:
            raise Held("unknown_or_duplicate_episode_in_pack")
        represented.add(number)
        episode = catalog[number]
        if is_missing(episode):
            wanted.append({"index": file.index, "name": file.name, "length": file.length,
                           "episode_id": episode["id"], "episode_number": number})
    if not represented:
        raise Held("no_episode_files_in_pack")
    return {"hash": torrent.info_hash, "series_id": target.series_id, "season": target.season,
            "wanted": wanted, "unwanted": [f.index for f in torrent.files if f.index not in {w["index"] for w in wanted}],
            "files": [{"name": f.name, "length": f.length} for f in torrent.files],
            "selected_bytes": sum(w["length"] for w in wanted), "total_bytes": torrent.total_bytes}
