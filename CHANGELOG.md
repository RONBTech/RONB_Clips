# Changelog

Changes are recorded here for each release. Version entries follow Semantic Versioning. The first stable `1.0.0` release has not been published yet; `pyproject.toml` currently identifies the preview package as `0.1.0`.

## Unreleased

No unreleased changes.

## 0.1.1 - 2026-11-24 (preview)

### Added

- Multi-architecture CPU images for `linux/amd64` and `linux/arm64`, with channel tags for preview, staging and stable releases.
- A Docker Compose smoke test and ARM64 image boot test under QEMU.
- A browser assertion for keyboard tab order.

### Changed

- Pinned the installed system packages in both Dockerfiles and the CI runner.
- Run PostgreSQL test clients from the same digest-pinned image as the database service.
- Corrected the PostgreSQL service health-check argument so the CI container starts reliably.
- Include the package build backend and its dependencies in the generated Python constraints lock.
- Cache pinned system-package downloads between CI runs.

## 0.1.0 - 2026-11-24 (preview)

This first preview establishes the RONB Clips project and release line. It is not a stable 1.x release.

### Added

- Source and Docker deployment for processing uploaded videos and supported live streams into captioned clips.
- Optional AI clip selection, creator workspaces, connected posting destinations, and local-owner operation without sign-in.
- SQLite and PostgreSQL storage, database migrations, and backup/restore scripts.
- Architecture, API, upgrade, troubleshooting, security, contributor, and compatibility documentation.
