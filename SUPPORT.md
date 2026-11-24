# Supported versions and compatibility

This policy describes support for stable releases. A stable release is a `1.x.y` version; versions below `1.0.0` are previews and may change behavior or data formats without the stable-release guarantees below.

## Support window

- The newest stable minor release receives feature work, fixes and security updates.
- The previous stable minor release receives security fixes for six months after it is superseded.
- Older minor releases and all preview (`0.x`) releases are unsupported. Upgrade to a supported release before requesting a fix.
- A major release may require a migration or change supported interfaces. Migration and rollback notes are published with that release.
- Security fixes are prioritized for supported releases. See [SECURITY.md](SECURITY.md) for private reporting.

## Compatibility matrix

| Component | Supported range | CI coverage / notes |
|---|---|---|
| Python | 3.11–3.13 | CI currently runs Python 3.11. |
| Operating system | Windows, macOS and Linux for source installs | Linux is exercised in CI; native speech and ffmpeg packages are platform-specific. |
| Container | Linux Docker image for `amd64` and `arm64` | Release workflow publishes and smoke-tests both platforms. Use the image and deployment files from the same release. |
| Database | SQLite for one-machine use; PostgreSQL for hosted deployments | CI exercises both backends when its PostgreSQL service is available. |
| Browser | Current Chromium-based browser; current Firefox and Safari are intended to work | Automated browser coverage currently runs Chromium. |
| ASR accelerator | CPU by default; optional NVIDIA CUDA worker | CUDA runtime, driver and model must be compatible; see `deploy/gpu/`. |

The optional GPU worker is supported on NVIDIA CUDA hosts using `linux/amd64`. ARM64 GPU hosts are not supported:
the CUDA worker dependencies in `deploy/gpu/Dockerfile` target x86-64. ARM64 support applies to the regular CPU image.

The Python version list describes supported versions, not a promise that every combination of OS, database, browser, and GPU is tested on every release. Report compatibility problems with the exact versions and platform.
