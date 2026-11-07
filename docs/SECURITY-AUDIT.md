# Credential audit — 2026-11-07

Scope: every reachable published branch/tag and the complete pre-release Git history
through commit `e4b864a486a6df9ca1737d6842073ad303f37a81` (3 commits, 35 unique text blobs).

- Gitleaks 8.30.1 scanned the complete history with full output redaction: **0 findings**.
- Independently compared those blobs with 4 actual operational credentials from
  the running Home Assistant setup: Sonarr, Transmission, and configured indexers.
  Raw, URL-encoded, JSON-escaped, and base64 representations were checked: **0 matches**.
- Actual values were read and compared only on the user's Home Assistant machine.
  No credential values are stored in this report, CI, the source repository, or images.
- Configuration examples contain placeholders; app defaults contain empty credentials.
- CI repeats the complete-history Gitleaks scan before any container is published.
  Registry authentication uses the ephemeral GitHub Actions `GITHUB_TOKEN`, passed
  only to the registry login action, never to the Docker build or source files.
- Docker copies only app source. Runtime options and SQLite/torrent state remain
  in Home Assistant's private app data, outside the build context and Git.

This records the inspected history, not a guarantee about future commits. A new
finding blocks CI publication. Never commit runtime options, private environment
files, torrent metainfo, state databases, or credentials.
