import base64
import json
import logging
from pathlib import PurePosixPath
import time

from .api import Sonarr, Transmission
from .planner import is_missing, plan, validate_release
from .settings import Held, Settings, Unavailable
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

    def episodes(self, series_id):
        return self.sonarr.call("episode", params={"seriesId": series_id})

    def discover(self, *, inspect=False):
        if inspect and not self.settings.dry_run:
            raise Held("inspection_requires_dry_run")
        parse_cache = {}

        def parse_file(title):
            if title not in parse_cache:
                parse_cache[title] = self.sonarr.call("parse", params={"title": title})
            return parse_cache[title]

        for target in self.settings.targets:
            try:
                series = self.sonarr.call(f"series/{target.series_id}")
                episodes = self.episodes(target.series_id)
                missing = [e for e in episodes if e["seasonNumber"] == target.season and is_missing(e)]
                if not missing and not inspect:
                    event("no_missing_episodes", series_id=target.series_id, season=target.season)
                    continue
                indexer = self.sonarr.call(f"indexer/{target.indexer_id}")
                releases = self.sonarr.call("release", params={"seriesId": target.series_id, "seasonNumber": target.season})
                candidates = sorted(releases, key=lambda r: (r.get("qualityWeight", 0), r.get("customFormatScore", 0), r.get("seeders", 0)), reverse=True)
                submitted = False
                for release in candidates:
                    try:
                        validate_release(release, target, self.settings)
                        raw = self.sonarr.metainfo(release, indexer)
                        torrent = read_metainfo(raw)
                        result = plan(torrent, target, series, episodes, parse_file, max_bytes=self.settings.max_pack_bytes)
                        event("plan", series_id=target.series_id, season=target.season, dry_run=self.settings.dry_run,
                              episodes=[w["episode_number"] for w in result["wanted"]],
                              selected_bytes=result["selected_bytes"], total_bytes=result["total_bytes"], excluded_files=len(result["unwanted"]))
                        if not result["wanted"] or self.settings.dry_run:
                            break
                        if self.state.get(result["hash"]):
                            event("already_recorded", series_id=target.series_id)
                            break
                        # One active job per season, including uncertain or held operations.
                        if any(j["stage"] != "imported" and j["plan"]["series_id"] == target.series_id and j["plan"]["season"] == target.season for j in self.state.jobs()):
                            raise Held("season_already_has_active_or_held_job")
                        if self.transmission.get(result["hash"]) is not None:
                            raise Held("torrent_already_exists_not_adopted")
                        result["download_dir"] = str(PurePosixPath(self.settings.transmission_download_dir) / result["hash"])
                        result["sonarr_dir"] = str(PurePosixPath(self.settings.sonarr_download_dir) / result["hash"])
                        result["owner_label"] = "packarr-" + self.state.owner_id
                        self.state.add(result, raw)
                        submitted = True
                        break
                    except Held as exc:
                        event("candidate_held", series_id=target.series_id, reason=str(exc))
                if not candidates:
                    event("no_candidates", series_id=target.series_id)
                if submitted:
                    self.reconcile()
            except Held as exc:
                event("target_held", series_id=target.series_id, reason=str(exc))

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
        rows = self.sonarr.call("queue", params={"pageSize": 1000, "includeUnknownSeriesItems": "true"})["records"]
        rows = [r for r in rows if r.get("downloadId", "").lower() == job["hash"]]
        if rows and all(r.get("status") == "completed" and r.get("sizeleft") == 0 for r in rows):
            self.sonarr.call(f"queue/{rows[0]['id']}", method="DELETE", params={
                "removeFromClient": "false", "blocklist": "false", "skipRedownload": "true", "changeCategory": "false"})

    def status(self):
        return {"dry_run": self.settings.dry_run, "updated": time.time(), "targets": len(self.settings.targets),
                "jobs": [{"series_id": j["plan"]["series_id"], "season": j["plan"]["season"],
                          "episodes": [w["episode_number"] for w in j["plan"]["wanted"]],
                          "stage": j["stage"], "reason": j["reason"]} for j in self.state.jobs()]}
