# Changelog

## 0.3.0

- Add Packarr branding and Home Assistant icon/logo assets.
- Publish tested native amd64 and aarch64 images plus a combined GHCR manifest.
- Configure Home Assistant to pull the versioned image instead of building locally.
- Gate publication on a full-history credential scan and release/platform checks.
- Pin CI actions and the credential scanner; document the credential audit.

## 0.2.0

- Replace manual `targets` with automatic discovery across the monitored Sonarr library.
- Read all missing/queue pages; respect indexer tags and automatic-search settings.
- Skip native queued episodes and recheck the queue before submitting a torrent.
- Rotate bounded season/candidate searches persistently for large libraries.
- Use existing Sonarr/Prowlarr indexer connections without extra API credentials.
- Use Sonarr's resolved episode IDs for catalog, scene, and absolute numbering.
- Preserve existing jobs and credentials; ignore legacy target restrictions.

## 0.1.0

- Initial scoped rolling-pack controller for Sonarr v4 and Transmission RPC.
- Dry-run inspection, verified v1 torrent inventory, selection before start.
- Persistent ownership, restart reconciliation, native Sonarr copy imports.
