import base64
import json
import logging
from pathlib import PurePosixPath
import time

from .api import Sonarr, Transmission
from .planner import is_missing, plan, validate_release
from .settings import Held, Settings, SearchScope, Unavailable
from .state import State
from .torrent import read_metainfo

LOG = logging.getLogger("packarr")


def event(name, **fields):
    LOG.info(json.dumps({"event": name, **fields}, ensure_ascii=False))


class Engine:
    def __init__(self, settings: Settings, *, sonarr=None, transmission=None, state=None):
        self.settings = settings
        self.sonarr = sonarr or Sonarr(settings)
        self.transmission = transmission or Transmission(settings)
        self.state = state or State(settings.state_dir)
        self.discovery = {"mode": "automatic", "monitored_series": 0, "missing_episodes": 0, "eligible_seasons": 0, "searched_seasons": 0}

    def episodes(self, series_id):
        return self.sonarr.call("episode", params={"seriesId": series_id})

    PAGE_SIZE = 250
    SEARCHES_PER_CYCLE = 10
    METAINFO_PER_SEARCH = 10

    def records(self, path, **params):
        """Read every page; refuse an incomplete or repeating inventory."""
        rows = []
        seen = set()
        for page in range(1, 4001):
            response = self.sonarr.call(path, params={**params, "page": page, "pageSize": self.PAGE_SIZE})
            current = response.get("records", [])
            total = response.get("totalRecords", len(current))
            if not isinstance(current, list) or type(total) is not int or total < 0:
                raise Held("invalid_paged_sonarr_inventory")
            if not current and len(rows) < total:
                raise Held("incomplete_paged_sonarr_inventory")
            for row in current:
                if row["id"] in seen:
                    raise Held("sonarr_inventory_changed_during_pagination")
                seen.add(row["id"])
                rows.append(row)
            if page * self.PAGE_SIZE >= total:
                return rows
        raise Held("sonarr_inventory_page_limit")

    def queued_episodes(self):
        return {r["episodeId"] for r in self.records("queue", includeUnknownSeriesItems="false")
                if r.get("episodeId") and r.get("trackedDownloadState") not in {"imported", "ignored"}}

    def discover(self, *, inspect=False):
        # Kept as a dry-run CLI compatibility alias; complete seasons are never searched.
        if inspect and not self.settings.dry_run:
            raise Held("inspection_requires_dry_run")
        if not self.settings.sonarr_api_key:
            event("configuration_required", reason="sonarr_api_key_required")
            return
        try:
            all_series = self.sonarr.call("series")
            monitored = {s["id"]: s for s in all_series if s.get("monitored")}
            missing = self.records("wanted/missing", monitored="true", sortKey="episodes.airDateUtc", sortDirection="descending")
            queued = self.queued_episodes()
            active = {(j["plan"]["series_id"], j["plan"]["season"]) for j in self.state.jobs() if j["stage"] != "imported"}
            scopes = set()
            eligible = 0
            for episode in missing:
                sid = episode["seriesId"]
                season = episode["seasonNumber"]
                if sid not in monitored or season < 1 or not is_missing(episode):
                    continue
                eligible += 1
                if episode["id"] not in queued and (sid, season) not in active:
                    scopes.add((sid, season))
            indexers = {i["id"]: i for i in self.sonarr.call("indexer")
                        if i.get("enableAutomaticSearch") and i.get("implementation") == "Torznab" and i.get("protocol") == "torrent"}
            ordered = sorted(scopes)
            cursor = int(self.state.metadata("scope_cursor", "0"))
            offset = cursor % len(ordered) if ordered else 0
            batch = (ordered[offset:] + ordered[:offset])[:self.SEARCHES_PER_CYCLE]
            self.discovery = {"mode": "automatic", "series": len(all_series), "monitored_series": len(monitored),
                              "missing_episodes": eligible, "eligible_seasons": len(ordered), "searched_seasons": 0,
                              "queued_episodes": len(queued), "automatic_indexers": len(indexers), "last_scan": time.time()}
            event("automatic_scan", **self.discovery, dry_run=self.settings.dry_run)
            parse_cache = {}

            def parse_file(title):
                if title not in parse_cache:
                    parse_cache[title] = self.sonarr.call("parse", params={"title": title})
                return parse_cache[title]

            for sid, season in batch:
                scope = SearchScope(sid, season)
                try:
                    self.search_scope(scope, monitored[sid], indexers, queued, parse_file)
                except Held as exc:
                    event("search_held", series_id=sid, season=season, reason=str(exc))
                self.reconcile()
            if ordered:
                self.state.set_metadata("scope_cursor", (offset + len(batch)) % len(ordered))
            event("automatic_scan_complete", **self.discovery, dry_run=self.settings.dry_run)
        except Held as exc:
            event("discovery_waiting_for_service", reason=str(exc))

    def search_scope(self, scope, series, indexers, queued, parse_file):
        allowed = {key: i for key, i in indexers.items()
                   if not i.get("tags") or set(i["tags"]).intersection(series.get("tags", []))}
        if not allowed:
            raise Held("no_automatic_torrent_indexers_for_series")
        episodes = self.episodes(scope.series_id)
        numbers = {e["episodeNumber"] for e in episodes if e["seasonNumber"] == scope.season and is_missing(e) and e["id"] not in queued}
        if not numbers:
            return
        # Sonarr's interactive API already returns prioritized results. Keep that order.
        self.discovery["searched_seasons"] += 1
        candidates = self.sonarr.call("release", params={"seriesId": scope.series_id, "seasonNumber": scope.season})
        cursor_key = f"candidate_cursor:{scope.series_id}:{scope.season}"
        offset = int(self.state.metadata(cursor_key, "0")) % len(candidates) if candidates else 0
        inspected = 0
        selected = False
        for step in range(len(candidates)):
            position = (offset + step) % len(candidates)
            release = candidates[position]
            indexer = allowed.get(release.get("indexerId"))
            if indexer is None:
                continue
            try:
                validate_release(release, scope, self.settings, indexer, series)
                mapped_numbers = release.get("mappedEpisodeNumbers") or release.get("episodeNumbers") or []
                if (series.get("seriesType", "standard") == "standard" and not series.get("useSceneNumbering")
                        and not release.get("fullSeason") and not numbers.intersection(mapped_numbers)):
                    continue
                if inspected >= self.METAINFO_PER_SEARCH:
                    event("metainfo_budget_reached", series_id=scope.series_id, season=scope.season)
                    break
                inspected += 1
                self.state.set_metadata(cursor_key, (position + 1) % len(candidates))
                raw = self.sonarr.metainfo(release, indexer)
                torrent = read_metainfo(raw)
                result = plan(torrent, scope, series, episodes, parse_file,
                              max_bytes=self.settings.max_pack_bytes, queued_episode_ids=queued)
                if not result["wanted"]:
                    continue
                if self.state.get(result["hash"]):
                    event("already_recorded", series_id=scope.series_id)
                    continue
                if not self.settings.dry_run:
                    if self.transmission.get(result["hash"]) is not None:
                        event("existing_torrent_not_adopted", series_id=scope.series_id)
                        continue
                    # The native downloader may have grabbed an episode during the search.
                    result = plan(torrent, scope, self.sonarr.call(f"series/{scope.series_id}"),
                                  self.episodes(scope.series_id), parse_file,
                                  max_bytes=self.settings.max_pack_bytes, queued_episode_ids=self.queued_episodes())
                    if not result["wanted"]:
                        continue
                event("plan", series_id=scope.series_id, season=scope.season, indexer_id=indexer["id"],
                      dry_run=self.settings.dry_run, episodes=[w["episode_number"] for w in result["wanted"]],
                      selected_bytes=result["selected_bytes"], total_bytes=result["total_bytes"], excluded_files=len(result["unwanted"]))
                selected = True
                self.state.set_metadata(cursor_key, 0)
                if self.settings.dry_run:
                    break
                result["download_dir"] = str(PurePosixPath(self.settings.transmission_download_dir) / result["hash"])
                result["sonarr_dir"] = str(PurePosixPath(self.settings.sonarr_download_dir) / result["hash"])
                result["owner_label"] = "packarr-" + self.state.owner_id
                self.state.add(result, raw)
                break
            except Held as exc:
                event("candidate_held", series_id=scope.series_id, season=scope.season, reason=str(exc))
        if not selected:
            event("no_suitable_pack", series_id=scope.series_id, season=scope.season, inspected=inspected)

    @staticmethod
    def owned(torrent, job):
        p = job["plan"]
        return (torrent.get("hashString", "").lower() == job["hash"] and
                str(PurePosixPath(torrent.get("downloadDir", ""))) == p["download_dir"] and
                "packarr" in torrent.get("labels", []) and p["owner_label"] in torrent.get("labels", []))

    def validate_torrent(self, torrent, job):
        p = job["plan"]
        if not self.owned(torrent, job):
            raise Held("torrent_ownership_mismatch")
        if torrent.get("metadataPercentComplete") != 1 or torrent.get("error", 0):
            raise Held("torrent_metadata_or_client_error")
        files = torrent.get("files", [])
        stats = torrent.get("fileStats", [])
        if len(files) != len(p["files"]) or len(stats) != len(files):
            raise Held("torrent_inventory_changed")
        wanted = {w["index"] for w in p["wanted"]}
        for index, (actual, expected, status) in enumerate(zip(files, p["files"], stats)):
            if actual["name"] != expected["name"] or actual["length"] != expected["length"]:
                raise Held("torrent_inventory_changed")
            if bool(status["wanted"]) != (index in wanted):
                raise Held("torrent_file_selection_changed")

    def missing_for_job(self, job):
        by_id = {e["id"]: e for e in self.episodes(job["plan"]["series_id"])}
        expected = job["plan"]["wanted"]
        if any(w["episode_id"] not in by_id for w in expected):
            raise Held("catalog_episode_removed")
        return [w for w in expected if not by_id[w["episode_id"]].get("hasFile")]

    def reconcile(self):
        if self.settings.dry_run:
            return
        for job in self.state.jobs():
            if job["stage"] == "imported":
                continue
            try:
                if job["stage"] == "held":
                    # An uncertain import may have succeeded after the response was lost.
                    if not self.missing_for_job(job):
                        self.state.update(job["hash"], "imported")
                        self.clear_completed_queue(job)
                        event("held_job_import_confirmed", series_id=job["plan"]["series_id"])
                    continue
                self.advance(job)
            except Unavailable as exc:
                event("job_waiting_for_service", series_id=job["plan"]["series_id"], reason=str(exc))
            except Held as exc:
                # Never mutate somebody else's task, even when a hash collides.
                try:
                    current = self.transmission.get(job["hash"])
                    if current and self.owned(current, job):
                        self.transmission.call("torrent-stop", {"ids": [job["hash"]]})
                except Held:
                    pass
                self.state.update(job["hash"], "held", reason=str(exc))
                event("job_held", series_id=job["plan"]["series_id"], reason=str(exc))

    def advance(self, job):
        p = job["plan"]
        missing = self.missing_for_job(job)
        if not missing:
            current = self.transmission.get(job["hash"])
            if current and self.owned(current, job) and any(
                    current.get("files", [])[w["index"]].get("bytesCompleted", 0) < w["length"]
                    for w in p["wanted"] if w["index"] < len(current.get("files", []))):
                self.transmission.call("torrent-stop", {"ids": [job["hash"]]})
            self.state.update(job["hash"], "imported")
            event("import_confirmed", series_id=p["series_id"], episodes=[w["episode_number"] for w in p["wanted"]])
            self.clear_completed_queue(job)
            return
        if len(missing) != len(p["wanted"]) and job["stage"] not in {"importing", "import_prepared"}:
            raise Held("episode_received_elsewhere_during_download")
        if job["stage"] == "import_prepared":
            raise Held("uncertain_import_requires_review")
        if job["stage"] == "importing":
            command = self.sonarr.call(f"command/{job['command_id']}")
            if command.get("status") in {"failed", "aborted", "cancelled", "completed"}:
                # Recheck after the command response to avoid racing a successful import.
                if not self.missing_for_job(job):
                    self.state.update(job["hash"], "imported")
                    self.clear_completed_queue(job)
                    event("import_confirmed", series_id=p["series_id"])
                    return
                raise Held("import_finished_without_all_expected_files")
            return
        current = self.transmission.get(job["hash"])
        if current is None:
            if job["stage"] != "prepared":
                raise Held("managed_torrent_disappeared")
            self.transmission.call("torrent-add", {
                "metainfo": base64.b64encode(job["metainfo"]).decode(), "paused": True,
                "download-dir": p["download_dir"], "labels": ["packarr", p["owner_label"]],
                "files-unwanted": p["unwanted"], "files-wanted": [w["index"] for w in p["wanted"]]})
            current = self.transmission.get(job["hash"])
            if current is None:
                raise Held("torrent_add_not_confirmed")
        self.validate_torrent(current, job)
        if job["stage"] == "prepared":
            # Persist the intent before starting. A restart reconciles the same owned hash.
            self.state.update(job["hash"], "starting")
            self.transmission.call("torrent-start", {"ids": [job["hash"]]})
            self.state.update(job["hash"], "downloading")
            event("download_started", series_id=p["series_id"], episodes=[w["episode_number"] for w in p["wanted"]], selected_bytes=p["selected_bytes"])
            return
        if job["stage"] == "starting":
            self.transmission.call("torrent-start", {"ids": [job["hash"]]})
            self.state.update(job["hash"], "downloading")
            return
        if current.get("status") in {1, 2}:
            return
        if not all(current["files"][w["index"]]["bytesCompleted"] == w["length"] for w in p["wanted"]):
            return
        files = []
        for wanted in missing:
            path = str(PurePosixPath(p["sonarr_dir"]) / wanted["name"])
            suggestions = self.sonarr.call("manualimport", params={"folder": path, "filterExistingFiles": "true"})
            matches = [s for s in suggestions if s.get("path") == path and
                       (s.get("series") or {}).get("id") == p["series_id"]
                       and [e["id"] for e in s.get("episodes", [])] == [wanted["episode_id"]]]
            if len(matches) != 1 or matches[0].get("rejections"):
                raise Held("sonarr_import_not_unambiguously_approved")
            suggestion = matches[0]
            if not suggestion.get("quality", {}).get("quality", {}).get("id"):
                raise Held("unknown_import_quality")
            files.append({"path": path, "seriesId": p["series_id"], "episodeIds": [wanted["episode_id"]],
                          "quality": suggestion["quality"], "languages": suggestion["languages"],
                          "releaseGroup": suggestion.get("releaseGroup", ""), "indexerFlags": suggestion.get("indexerFlags", 0),
                          "releaseType": "singleEpisode"})
        if len(self.missing_for_job(job)) != len(missing):
            raise Held("episode_received_elsewhere_before_import")
        self.state.update(job["hash"], "import_prepared")
        command = self.sonarr.call("command", data={"name": "ManualImport", "importMode": "copy", "files": files})
        if not command.get("id"):
            raise Held("import_command_not_confirmed")
        self.state.update(job["hash"], "importing", command_id=command["id"])
        event("import_started", series_id=p["series_id"], files=len(files))

    def clear_completed_queue(self, job):
        # Only hide this exact completed task; never delete data or blocklist a topic.
        current = self.transmission.get(job["hash"])
        if current is None or not self.owned(current, job):
            return
        rows = self.records("queue", includeUnknownSeriesItems="true")
        rows = [r for r in rows if r.get("downloadId", "").lower() == job["hash"]]
        if rows and all(r.get("status") == "completed" and r.get("sizeleft") == 0 for r in rows):
            self.sonarr.call(f"queue/{rows[0]['id']}", method="DELETE", params={
                "removeFromClient": "false", "blocklist": "false", "skipRedownload": "true", "changeCategory": "false"})

    def status(self):
        return {"dry_run": self.settings.dry_run, "updated": time.time(), "discovery": dict(self.discovery),
                "jobs": [{"series_id": j["plan"]["series_id"], "season": j["plan"]["season"],
                          "episodes": [w["episode_number"] for w in j["plan"]["wanted"]],
                          "stage": j["stage"], "reason": j["reason"]} for j in self.state.jobs()]}
