# Packarr

Home Assistant app for downloading **only missing episodes** from rolling torrent
packs, using Sonarr and Transmission. For example: when a topic grows from `S29E1-4`
to `S29E1-5`, download episode 5 while excluding episodes 1–4.

## How it works

1. Read monitored, aired, missing episodes from an explicitly selected Sonarr season.
2. Search through Sonarr and match the configured tracker topic and indexer.
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
Packarr is included there as the `hassio-addons-packarr` Git submodule. It builds
locally for `aarch64` and `amd64` and requires no privileged permissions or Docker socket.

See [configuration and operational limits](packarr/DOCS.md). Start in dry-run,
check plans, then enable downloads. A configured target is required; an empty target
list is idle. Storage paths must resolve to the same files in Sonarr and Transmission.

## Run without Home Assistant

Python 3.12+; no third-party runtime dependencies:

```sh
PYTHONPATH=packarr/src python3 -m packarr --config /private/options.json --once
PYTHONPATH=packarr/src python3 -m packarr --config /private/options.json --once --inspect
```

`--inspect` requires dry-run and checks the actual torrent even when all episodes
are already present. It does not pretend that an existing episode is missing.

```sh
docker build -t packarr:local packarr
docker run --rm -v /private/packarr:/data packarr:local
```

## Development

```sh
PYTHONPATH=packarr/src python3 -m unittest discover -s tests -v
```

Tests cover selection of E04 from E01–E04, no repeat after import, restart recovery,
uncertain submissions, ownership, path traversal, mixed/ambiguous episodes, and dry-run.

This is an initial release for scoped rolling topics, not a general-purpose torrent
manager. It does not upgrade existing episodes or support multi-season/archived packs.

Background: Sonarr issues [6378](https://github.com/Sonarr/Sonarr/issues/6378),
[7877](https://github.com/Sonarr/Sonarr/issues/7877), and
[9234](https://github.com/Sonarr/Sonarr/issues/9234).
