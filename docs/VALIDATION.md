# Initial validation — 2026-11-07

- 31 unit and workflow tests passed on local Python 3.12 and in the amd64 image.
- Built the app for amd64 and on Home Assistant OS, aarch64 (Raspberry Pi).
- Connected read-only to Sonarr 4.0.20.3014 and Transmission 4.0.6 / RPC 17.
- Inspected the real South Park S29E1–4 torrent through its configured Sonarr indexer.
- Real library: E01–E04 present. Result: **zero files selected**, all 4 excluded.
- Read-only replay: marked E04 missing only in an in-memory copy of the API response.
  Result: **only E04 selected**, 2,229,209,925 bytes out of 8,918,401,318 bytes.
  E01–E03 excluded. No Sonarr records were changed and no torrent task was submitted.
- Separately added a synthetic, isolated torrent to the real Transmission **paused**.
  Verified wanted flags `[false, false, false, true]`, zero downloaded media bytes,
  and stopped state after initial file verification. Removed only that temporary
  task with `delete-local-data=false`; existing torrents were untouched.

The regression replay is not a real download/import test against the media library.
The complete mutation workflow is covered with isolated fake clients, including
loss of responses, restarts, ownership changes, and import reconciliation.
