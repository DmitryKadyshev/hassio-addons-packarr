# Packarr configuration

Packarr supplements Sonarr with selective downloads from rolling torrent packs
throughout its library.
It does not replace Sonarr's normal search or upgrade settings.

Configure Sonarr's URL and API key, Transmission's full RPC URL and credentials.
Packarr automatically obtains the monitored library, missing episodes, queue, and
indexers from Sonarr. No manual `targets`, series IDs, seasons, or topic URLs are needed.
If Sonarr connects to Prowlarr, those connections also supply the download endpoints;
a separate Prowlarr URL/key is unnecessary.

```yaml
sonarr_url: http://homeassistant.local:8989
sonarr_api_key: YOUR_SONARR_KEY
transmission_url: http://transmission.local:9091/transmission/rpc
transmission_username: YOUR_USERNAME
transmission_password: YOUR_PASSWORD
transmission_download_dir: /downloads/packarr
sonarr_download_dir: /share/downloads/packarr
dry_run: true
poll_interval: 900
min_seeders: 1
max_pack_bytes: 68719476736
```

The two download directories refer to the **same storage**, as seen by Transmission
and Sonarr respectively. A separate directory per torrent hash is created under them.
Packarr does not need a media-directory mount: Sonarr performs the file import through its API.

Start with `dry_run: true`. Logs show selected episode numbers and bytes, excluded
file counts, and reasons a release was held. In this mode no torrent is added,
started, stopped, or imported; the only downloads are small `.torrent` metadata files.

Once the plan is correct, set `dry_run: false` and restart the app to enable operation.
Restart after any option change. The first cycle runs at startup; discovery repeats
every `poll_interval` seconds. Active jobs are checked every 30 seconds.

All pages of Sonarr's missing list and queue are read. Only monitored, aired episodes
without files and without a queue entry are searched. Complete series/seasons are
skipped. Individual episode results are left to Sonarr's normal downloader.
Only automatically enabled Torznab torrent indexers with matching series tags are
eligible; Sonarr's returned release priority is preserved.

To bound indexer load, one cycle searches up to 10 eligible seasons and inspects up
to 10 suitable torrents per season. Rotating cursors are persisted, so later seasons
and candidates are visited on subsequent cycles rather than being permanently skipped.
A held or active Packarr job blocks another submission for that season.

`targets` from version 0.1 are ignored on migration and removed from the app schema.
The `--inspect` command-line flag is retained only as a dry-run compatibility alias;
it no longer searches complete seasons. An empty Sonarr key leaves discovery idle.

The series and individual episodes must be monitored, with a known air date in the past.
Sonarr can monitor an individual episode even when the season's bulk flag is off;
Packarr follows that episode flag.
Only existing-file cutoff / equal-Custom-Format rejection messages are bypassed.
Other Sonarr rejections (quality, language/CF minimum, seeds, blocklist, etc.) remain blocking.

Jobs with uncertain API outcomes, altered selection, unexpected file inventories,
ambiguous import decisions, or missing source tasks are held for review. Held jobs
block new submissions for that season. No automatic retry of an uncertain import occurs.
Do not delete the state database to force a retry: inspect the client and Sonarr first.

Packarr never deletes torrents or source media, and imports with `copy` mode to retain
seeding data. Completion means Sonarr reports all selected episodes as having files.
Existing episodes are excluded and are rechecked immediately before import; avoid
manually importing the same episode concurrently with Packarr's import command.

Supported: v1 `.torrent` files, one catalog season, one episode per video file.
Sonarr must return exactly one matching catalog episode ID per video; its native
mapping is used for standard, scene, daily, and absolute numbering. Missing or
ambiguous mappings are held instead of guessed from a filename. Subtitles/artwork are excluded. Archives,
executables, symlinks, ambiguous/mixed seasons, specials and v2/hybrid torrents are held.
Only downloads from the corresponding Sonarr indexer's exact HTTP origin are accepted; redirects
are held. Use a direct Prowlarr/Torznab download endpoint.

Persistent state is in `/data/packarr`; include it in backups. Keep credentials in
the app's options, never in Git. Stopping Packarr stops new decisions; already-started
Transmission tasks continue. Turning on dry-run also stops import and recovery mutations.
