import base64
import copy
from datetime import datetime, timezone
import json
from pathlib import PurePosixPath
import tempfile
import unittest

from packarr.api import Sonarr, Transmission
from packarr.engine import Engine
from packarr.planner import is_missing, plan, validate_release
from packarr.settings import Held, Settings, Target, Unavailable
from packarr.state import State
from packarr.torrent import read_metainfo


def encode(value):
    if isinstance(value, int):
        return b"i" + str(value).encode() + b"e"
    if isinstance(value, bytes):
        return str(len(value)).encode() + b":" + value
    if isinstance(value, list):
        return b"l" + b"".join(encode(v) for v in value) + b"e"
    return b"d" + b"".join(encode(k) + encode(v) for k, v in sorted(value.items())) + b"e"


def metainfo(numbers=(1, 2, 3, 4), *, paths=None):
    paths = paths or [[f"South.Park.S29E{n:02d}.2160p.mkv".encode()] for n in numbers]
    files = [{b"length": 100, b"path": p} for p in paths]
    return encode({b"info": {b"name": b"South.Park.S29.2160p", b"files": files,
                              b"piece length": 100, b"pieces": b"x" * len(files) * 20}})


TARGET = Target(85, 29, 12, "https://rutracker.org/forum/viewtopic.php?t=6918073")
QUALITY = {"quality": {"id": 18, "name": "WEBDL-2160p"}, "revision": {"version": 1, "real": 0}}


def catalog():
    return [{"id": 7053 + n, "seasonNumber": 29, "episodeNumber": n, "monitored": True,
             "hasFile": n < 4, "episodeFileId": 34035 + n if n < 4 else 0,
             "airDateUtc": "2020-01-01T00:00:00Z" if n <= 4 else "2099-01-01T00:00:00Z"}
            for n in range(1, 7)]


def parse_title(title):
    number = int(title.split("S29E")[1][:2])
    return {"series": {"id": 85}, "parsedEpisodeInfo": {"seasonNumber": 29, "episodeNumbers": [number]}}


def series():
    return {"id": 85, "monitored": True, "seasons": [{"seasonNumber": 29, "monitored": True}]}


def release():
    return {"mappedSeriesId": 85, "indexerId": 12, "seasonNumber": 29, "seeders": 100,
            "guid": TARGET.topic_url, "downloadAllowed": True, "quality": QUALITY,
            "rejections": ["Existing file on disk has a equal or higher Custom Format score: 0"]}


class FakeSonarr:
    def __init__(self):
        self.catalog = catalog()
        self.mutations = []
        self.fail_import = False
        self.finished = False
        self.queue = []

    def call(self, path, *, params=None, data=None, method=None):
        if data is not None or method:
            self.mutations.append((path, params, data, method))
        if path == "episode":
            return copy.deepcopy(self.catalog)
        if path == "series/85":
            return series()
        if path == "indexer/12":
            return {"fields": [{"name": "baseUrl", "value": "https://indexer.test/"}]}
        if path == "release":
            return [release()]
        if path == "parse":
            return parse_title(params["title"])
        if path == "manualimport":
            return [{"path": params["folder"], "series": {"id": 85}, "episodes": [{"id": 7057}],
                     "quality": QUALITY, "languages": [{"id": 11, "name": "Russian"}], "rejections": []}]
        if path == "command":
            if self.fail_import:
                raise Held("http_request_failed")
            return {"id": 123, "status": "queued"}
        if path == "command/123":
            return {"id": 123, "status": "completed" if self.finished else "started"}
        if path == "queue":
            return {"records": self.queue}
        if path.startswith("queue/") and method == "DELETE":
            self.queue = []
            return None
        raise AssertionError((path, params, data, method))

    def metainfo(self, release, indexer):
        return metainfo()


class FakeTransmission:
    def __init__(self):
        self.torrent = None
        self.calls = []
        self.fail_add_after_accept = False

    def get(self, info_hash):
        return copy.deepcopy(self.torrent)

    def call(self, method, arguments):
        self.calls.append((method, copy.deepcopy(arguments)))
        if method == "torrent-add":
            parsed = read_metainfo(base64.b64decode(arguments["metainfo"]))
            self.torrent = {"hashString": parsed.info_hash, "downloadDir": arguments["download-dir"],
                            "labels": arguments["labels"], "metadataPercentComplete": 1, "error": 0, "status": 0,
                            "files": [{"name": f.name, "length": f.length, "bytesCompleted": 0} for f in parsed.files],
                            "fileStats": [{"wanted": f.index in arguments["files-wanted"]} for f in parsed.files]}
            if self.fail_add_after_accept:
                raise Held("http_request_failed")
            return {}
        if method == "torrent-start":
            self.torrent["status"] = 4
            return {}
        if method == "torrent-stop":
            self.torrent["status"] = 0
            return {}
        raise AssertionError(method)


class PlannerTests(unittest.TestCase):
    def make_plan(self, **kwargs):
        return plan(read_metainfo(metainfo()), TARGET, series(), kwargs.pop("episodes", catalog()),
                    kwargs.pop("parse_file", parse_title), max_bytes=kwargs.pop("max_bytes", 10000), **kwargs)

    def test_only_missing_fourth_episode_is_selected(self):
        p = self.make_plan()
        self.assertEqual([w["episode_number"] for w in p["wanted"]], [4])
        self.assertEqual(p["unwanted"], [0, 1, 2])
        self.assertEqual(p["selected_bytes"], 100)

    def test_already_present_episode_is_not_selected_even_if_unmonitored(self):
        e = catalog()
        e[3]["hasFile"] = True
        self.assertEqual(self.make_plan(episodes=e)["wanted"], [])

    def test_unmonitored_or_future_episode_not_selected(self):
        e = catalog()
        e[3]["monitored"] = False
        self.assertEqual(self.make_plan(episodes=e)["wanted"], [])
        e[3]["monitored"] = True
        e[3]["airDateUtc"] = "2099-01-01T00:00:00Z"
        self.assertEqual(self.make_plan(episodes=e)["wanted"], [])

    def test_unknown_air_date_is_not_eligible(self):
        e = catalog()[3]
        e.pop("airDateUtc")
        self.assertFalse(is_missing(e))

    def test_wrong_file_series_is_rejected(self):
        def wrong(title):
            p = parse_title(title)
            p["series"]["id"] = 86
            return p
        with self.assertRaisesRegex(Held, "ambiguous_file"):
            self.make_plan(parse_file=wrong)

    def test_multi_episode_video_is_rejected(self):
        def ambiguous(title):
            p = parse_title(title)
            p["parsedEpisodeInfo"]["episodeNumbers"] = [1, 2]
            return p
        with self.assertRaisesRegex(Held, "single_episode"):
            self.make_plan(parse_file=ambiguous)

    def test_duplicate_episode_is_rejected(self):
        with self.assertRaisesRegex(Held, "duplicate_episode"):
            self.make_plan(parse_file=lambda _: parse_title("South.Park.S29E01.mkv"))

    def test_size_limit(self):
        with self.assertRaisesRegex(Held, "size_limit"):
            self.make_plan(max_bytes=399)

    def test_other_rejections_not_bypassed(self):
        r = release()
        r["rejections"].append("Not enough seeders")
        with self.assertRaisesRegex(Held, "other_rejections"):
            validate_release(r, TARGET, Settings())

    def test_topic_and_series_must_match(self):
        for key, value in [("guid", TARGET.topic_url + "0"), ("mappedSeriesId", 84), ("seasonNumber", 28)]:
            r = release()
            r[key] = value
            with self.assertRaises(Held):
                validate_release(r, TARGET, Settings())

    def test_dry_run_requires_real_boolean(self):
        with self.assertRaises(Held):
            Settings(dry_run="false")


class MetainfoTests(unittest.TestCase):
    def test_hash_and_inventory_are_stable(self):
        t = read_metainfo(metainfo())
        self.assertEqual(len(t.info_hash), 40)
        self.assertEqual(t.total_bytes, 400)
        self.assertEqual(len(t.files), 4)

    def test_rejects_traversal_absolute_and_windows_paths(self):
        for part in [b"..", b"/tmp", b"x\\y", b"C:foo", b"\x00bad"]:
            with self.subTest(part=part), self.assertRaises(Held):
                read_metainfo(metainfo(paths=[[part, b"episode.mkv"]]))

    def test_truncated_trailing_and_duplicate_keys(self):
        for raw in [metainfo()[:-1], metainfo() + b"x", b"d4:infod1:ai1e1:ai2eee", b"i-0e", b"9999:x"]:
            with self.subTest(raw=raw[:10]), self.assertRaises(Held):
                read_metainfo(raw)

    def test_symlink_rejected(self):
        raw = encode({b"info": {b"name": b"pack", b"files": [{b"path": [b"x.mkv"], b"length": 0, b"attr": b"l"}],
                                b"piece length": 100, b"pieces": b""}})
        with self.assertRaisesRegex(Held, "unsafe_torrent_file"):
            read_metainfo(raw)

    def test_incorrect_piece_count_rejected(self):
        raw = encode({b"info": {b"name": b"x.mkv", b"length": 100, b"piece length": 100, b"pieces": b""}})
        with self.assertRaisesRegex(Held, "piece_count"):
            read_metainfo(raw)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(dry_run=False, sonarr_api_key="secret", targets=[TARGET], state_dir=self.temp.name,
                                 transmission_download_dir="/downloads/packarr", sonarr_download_dir="/share/downloads/packarr")
        self.sonarr = FakeSonarr()
        self.client = FakeTransmission()
        self.state = State(self.temp.name)
        self.engine = Engine(self.settings, sonarr=self.sonarr, transmission=self.client, state=self.state)
        self.info_hash = read_metainfo(metainfo()).info_hash

    def tearDown(self):
        self.state.close()
        self.temp.cleanup()

    def complete_download(self):
        for f in self.client.torrent["files"]:
            if "E04" in f["name"]:
                f["bytesCompleted"] = f["length"]
        self.client.torrent["status"] = 6

    def test_end_to_end_only_fourth_downloaded_and_imported(self):
        self.engine.discover()
        self.assertEqual([m for m, _ in self.client.calls], ["torrent-add", "torrent-start"])
        args = self.client.calls[0][1]
        self.assertTrue(args["paused"])
        self.assertEqual(args["files-wanted"], [3])
        self.assertEqual(args["files-unwanted"], [0, 1, 2])
        self.complete_download()
        self.engine.reconcile()
        commands = [m[2] for m in self.sonarr.mutations if m[0] == "command"]
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0]["files"][0]["episodeIds"], [7057])
        self.assertEqual(commands[0]["importMode"], "copy")
        self.sonarr.catalog[3]["hasFile"] = True
        self.engine.reconcile()
        self.engine.discover()
        self.assertEqual(self.state.get(self.info_hash)["stage"], "imported")
        self.assertEqual(sum(m == "torrent-add" for m, _ in self.client.calls), 1)
        self.assertEqual(len([m for m in self.sonarr.mutations if m[0] == "command"]), 1)

    def test_dry_run_inspects_without_any_mutation(self):
        self.settings.dry_run = True
        self.engine.discover(inspect=True)
        self.engine.reconcile()
        self.assertEqual(self.client.calls, [])
        self.assertEqual(self.sonarr.mutations, [])
        self.assertEqual(self.state.jobs(), [])

    def test_restart_after_add_before_start_reuses_owned_torrent(self):
        p = plan(read_metainfo(metainfo()), TARGET, series(), catalog(), parse_title, max_bytes=10000)
        p["download_dir"] = "/downloads/packarr/" + p["hash"]
        p["sonarr_dir"] = "/share/downloads/packarr/" + p["hash"]
        p["owner_label"] = "packarr-" + self.state.owner_id
        self.state.add(p, metainfo())
        self.client.call("torrent-add", {"metainfo": base64.b64encode(metainfo()).decode(), "download-dir": p["download_dir"],
                                        "paused": True, "labels": ["packarr", p["owner_label"]], "files-wanted": [3]})
        restarted = Engine(self.settings, sonarr=self.sonarr, transmission=self.client, state=self.state)
        restarted.reconcile()
        self.assertEqual([m for m, _ in self.client.calls], ["torrent-add", "torrent-start"])

    def test_import_timeout_is_not_replayed(self):
        self.engine.discover()
        self.complete_download()
        self.sonarr.fail_import = True
        self.engine.reconcile()
        self.engine.reconcile()
        self.engine.discover()
        self.assertEqual(self.state.get(self.info_hash)["stage"], "held")
        self.assertEqual(len([m for m in self.sonarr.mutations if m[0] == "command"]), 1)

    def test_prepared_import_after_restart_is_not_replayed(self):
        self.engine.discover()
        self.state.update(self.info_hash, "import_prepared")
        self.engine.reconcile()
        self.assertEqual(self.state.get(self.info_hash)["stage"], "held")
        self.assertEqual(self.sonarr.mutations, [])

    def test_existing_unowned_hash_never_adopted(self):
        self.client.torrent = {"hashString": self.info_hash, "downloadDir": "/other", "labels": []}
        self.engine.discover()
        self.assertEqual(self.client.calls, [])
        self.assertEqual(self.state.jobs(), [])

    def test_changed_selection_pauses_without_import(self):
        self.engine.discover()
        self.client.torrent["fileStats"][0]["wanted"] = True
        self.engine.reconcile()
        self.assertEqual(self.state.get(self.info_hash)["stage"], "held")
        self.assertEqual(self.client.torrent["status"], 0)
        self.assertEqual(self.sonarr.mutations, [])

    def test_ownership_change_does_not_stop_foreign_task(self):
        self.engine.discover()
        self.client.torrent["downloadDir"] = "/someone-else"
        before = len(self.client.calls)
        self.engine.reconcile()
        self.assertEqual(len(self.client.calls), before)
        self.assertEqual(self.state.get(self.info_hash)["stage"], "held")

    def test_import_only_when_selected_file_complete(self):
        self.engine.discover()
        self.engine.reconcile()
        self.assertEqual(self.sonarr.mutations, [])

    def test_temporary_read_failure_does_not_hold_or_replay_job(self):
        self.engine.discover()
        original = self.client.get
        def unavailable(_):
            raise Unavailable("transmission_read_unavailable")
        self.client.get = unavailable
        self.engine.reconcile()
        self.assertEqual(self.state.get(self.info_hash)["stage"], "downloading")
        self.client.get = original
        self.engine.reconcile()
        self.assertEqual(sum(m == "torrent-add" for m, _ in self.client.calls), 1)

    def test_episode_received_elsewhere_not_imported_again(self):
        self.engine.discover()
        self.sonarr.catalog[3]["hasFile"] = True
        self.engine.reconcile()
        self.assertEqual(self.sonarr.mutations, [])
        self.assertEqual(self.client.torrent["status"], 0)

    def test_lost_import_response_eventually_reconciles_without_retry(self):
        self.engine.discover()
        self.complete_download()
        self.sonarr.fail_import = True
        self.engine.reconcile()
        self.sonarr.catalog[3]["hasFile"] = True
        self.engine.reconcile()
        self.assertEqual(self.state.get(self.info_hash)["stage"], "imported")
        self.assertEqual(len([m for m in self.sonarr.mutations if m[0] == "command"]), 1)

    def test_add_timeout_holds_and_does_not_submit_duplicate(self):
        self.client.fail_add_after_accept = True
        self.engine.discover()
        self.engine.discover()
        self.assertEqual(sum(m == "torrent-add" for m, _ in self.client.calls), 1)
        self.assertEqual(self.state.get(self.info_hash)["stage"], "held")


class ApiSafetyTests(unittest.TestCase):
    def test_dry_run_blocks_all_download_and_import_mutations(self):
        settings = Settings()
        with self.assertRaisesRegex(Held, "dry_run"):
            Sonarr(settings).call("command", data={"name": "ManualImport"})
        with self.assertRaisesRegex(Held, "dry_run"):
            Sonarr(settings).call("queue/1", method="DELETE")
        for method in ["torrent-add", "torrent-start", "torrent-set", "torrent-stop", "torrent-remove"]:
            with self.subTest(method=method), self.assertRaisesRegex(Held, "dry_run"):
                Transmission(settings).call(method)

    def test_metainfo_origin_cannot_receive_sonarr_credentials(self):
        with self.assertRaisesRegex(Held, "origin"):
            Sonarr(Settings()).metainfo({"downloadUrl": "http://other.invalid/download"},
                                       {"fields": [{"name": "baseUrl", "value": "http://indexer.invalid/"}]})


if __name__ == "__main__":
    unittest.main()
