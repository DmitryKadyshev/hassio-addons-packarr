# Packarr configuration

Packarr supplements Sonarr with selective downloads from a specific rolling torrent topic.
It does not replace Sonarr's normal search or upgrade settings.

Configure Sonarr's URL and API key, Transmission's full RPC URL and credentials,
then add explicit `targets`. IDs are available from the respective Sonarr API endpoints.
One target binds a series, season, indexer, and exact tracker topic.

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
targets:
  - series_id: 85
    season: 29
    indexer_id: 12
    topic_url: https://rutracker.org/forum/viewtopic.php?t=6918073
```

The two download directories refer to the **same storage**, as seen by Transmission
and Sonarr respectively. A separate directory per torrent hash is created under them.
Packarr does not need a media-directory mount: Sonarr performs the file import through its API.

Start with `dry_run: true`. Logs show selected episode numbers and bytes, excluded
file counts, and reasons a release was held. In this mode no torrent is added,
started, stopped, or imported; the only downloads are small `.torrent` metadata files.

Once the plan is correct, set `dry_run: false` to enable operation. The first cycle
runs at startup; target searches repeat every `poll_interval` seconds. Active jobs
are checked every 30 seconds. `targets: []` leaves the service idle.

The series, season, and episodes must be monitored, with a known air date in the past.
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

Supported: v1 `.torrent` files, one catalog season, one episode per video file, plain
episode numbering resolvable by Sonarr. Subtitles/artwork are excluded. Archives,
executables, symlinks, ambiguous/mixed seasons, specials and v2/hybrid torrents are held.
Only downloads from the configured indexer's exact HTTP origin are accepted; redirects
are held. Use a direct Prowlarr/Torznab download endpoint.

Persistent state is in `/data/packarr`; include it in backups. Keep credentials in
the app's options, never in Git. Stopping Packarr stops new decisions; already-started
Transmission tasks continue. Turning on dry-run also stops import and recovery mutations.
