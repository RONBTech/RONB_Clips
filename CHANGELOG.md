# Changelog

Changes are recorded here for each release. Version entries follow Semantic Versioning. The first stable `1.0.0` release has not been published yet; `pyproject.toml` currently identifies the preview package as `0.1.0`.

## Unreleased

### Added

- Architecture and request-flow diagrams, generated API documentation, contributor and troubleshooting guides.
- Database upgrade notes that call out the destructive removal in migration 3.
- CI checks for environment-example drift and frontend assets.
- Stable-version support and compatibility policy.

## 0.1.0 - 2026-11-24 (preview)

This first preview establishes the RONB Clips project and release line. It is not a stable 1.x release.

### Added

- Source and Docker deployment for processing uploaded videos and supported live streams into captioned clips.
- Optional AI clip selection, creator workspaces, connected posting destinations, and local-owner operation without sign-in.
- SQLite and PostgreSQL storage, database migrations, and backup/restore scripts.
- Architecture, API, upgrade, troubleshooting, security, contributor, and compatibility documentation.
