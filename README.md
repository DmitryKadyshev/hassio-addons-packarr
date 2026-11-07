# Packarr

<p><img src="packarr/logo@2x.png" alt="Packarr" width="360"></p>

Home Assistant app for downloading **only missing episodes** from rolling torrent
packs, using Sonarr and Transmission. For example: when a topic grows from `S29E1-4`
to `S29E1-5`, download episode 5 while excluding episodes 1–4.

## How it works

1. Automatically read all monitored, aired, missing episodes from Sonarr, across all pages.
2. Group them by series/season and search using Sonarr’s configured torrent indexers,
   respecting automatic-search settings, series tags, priorities, and the download queue.
3. Inspect the actual `.torrent` inventory; ask Sonarr to identify each video file.
4. Add an owned, isolated Transmission task **paused**, with old episodes excluded.
5. Start only after checking the client's inventory, ownership, and wanted-file flags.
6. When selected files are complete, ask Sonarr to assess them and import approved
   files with its native importer in **copy** mode.
7. Confirm files in Sonarr and retain source data for seeding.

Dry-run is the default. A SQLite ledger and a single-worker lock prevent duplicate
submissions. Uncertain operations are held rather than replayed. No torrent or media
deletion is implemented. No API keys, torrent contents, or credentials appear in logs.

## Install in Home Assistant

Use the [DK add-on repository](https://github.com/DmitryKadyshev/hassio-addons).
Packarr is included there as the `hassio-addons-packarr` Git submodule. Home Assistant
pulls the ready-made image `ghcr.io/dmitrykadyshev/addon-packarr:<version>`; it does
not build on the device. One multi-platform tag serves `amd64` (x86-64) and `aarch64`
(ARM64). No privileged permissions or Docker socket are needed.

[Container package](https://github.com/users/DmitryKadyshev/packages/container/package/addon-packarr)
· [CI and publication](https://github.com/DmitryKadyshev/hassio-addons-packarr/actions)
· [Credential audit](docs/SECURITY-AUDIT.md)

CI scans the complete Git history, runs unit tests, builds and tests each native
architecture, pushes the architecture images to GHCR, and publishes a verified
combined manifest. Release tags match `packarr/config.yaml`; commit tags and `latest`
are also retained. Fork/PR builds do not publish images. The first GHCR package must
be made public in Package settings to allow installations without registry credentials.

See [configuration and operational limits](packarr/DOCS.md). Start in dry-run,
check plans, then enable downloads. No series IDs, tracker topics, or `targets` list
are needed. Prowlarr is used through Sonarr’s existing indexer connections; no separate
Prowlarr URL or API key is required. Storage paths must resolve to the same files in
Sonarr and Transmission.

## Run without Home Assistant

Python 3.12+; no third-party runtime dependencies:

```sh
PYTHONPATH=packarr/src python3 -m packarr --config /private/options.json --once
```

`--once` runs one automatic discovery/reconciliation cycle. Complete seasons and
individual episodes already queued in Sonarr are skipped. Large libraries are
processed in rotating batches; progress survives a restart.

```sh
docker build -t packarr:local packarr
docker run --rm -v /private/packarr:/data packarr:local
```

## Development

```sh
PYTHONPATH=packarr/src python3 -m unittest discover -s tests -v
```

Tests cover automatic discovery across series and paginated results, indexer tags,
queue races, fair search budgets, selection of E04 from E01–E04, no repeat after import, restart recovery,
uncertain submissions, ownership, path traversal, mixed/ambiguous episodes, and dry-run.

This is a rolling-pack companion for the Sonarr library, not a general-purpose torrent
manager. It does not upgrade existing episodes or support multi-season/archived packs.

Background: Sonarr issues [6378](https://github.com/Sonarr/Sonarr/issues/6378),
[7877](https://github.com/Sonarr/Sonarr/issues/7877), and
[9234](https://github.com/Sonarr/Sonarr/issues/9234).
