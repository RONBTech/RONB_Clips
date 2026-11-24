# Security policy

## Reporting a vulnerability

Do not report security vulnerabilities in a public issue, discussion, or pull request. Use GitHub's **Report a vulnerability** feature on the repository's Security tab to send a private advisory to the maintainers.

Include the affected version or commit, the impact, a clear reproduction, and any suggested fix. Do not include real keys, personal data, or other people's video files. Please allow maintainers reasonable time to investigate and prepare a fix before sharing details publicly.

If private vulnerability reporting is unavailable, contact the repository owner through the contact information on their GitHub profile and ask for a secure reporting channel. Do not send secrets or exploit payloads in an unsolicited public message.

## Supported versions

Security fixes are prioritized for versions covered by [SUPPORT.md](SUPPORT.md). Unsupported versions should be upgraded before requesting a fix. See [docs/upgrading.md](docs/upgrading.md) for backup and migration guidance.

## Deployment notes

RONB Clips has no sign-in: anyone who can reach the app can act as its owner. Keep it on a trusted machine or behind a correctly configured HTTPS reverse proxy and private access controls. Never publish `.env`, OAuth credentials, AI keys, database credentials, or user data in an issue or log attachment.
