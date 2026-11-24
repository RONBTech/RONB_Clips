# Contributing

Thanks for helping improve RONB Clips. Keep changes focused, explain the user impact in plain language, and include a way to reproduce bug fixes.

## Before you start

- Check open issues and pull requests for existing work on the same problem.
- For a substantial change, open an issue or discussion first so the scope is clear.
- For security problems, follow [SECURITY.md](SECURITY.md); do not report them in a public issue.

## Development setup

RONB Clips needs Python 3.11 or newer and `ffmpeg` on `PATH`. Use a short checkout path on Windows (for example `C:\ronbclips`) because native speech libraries can fail when paths are too long.

```bash
python -m venv .venv
python -m pip install -r requirements-dev.txt
python -m playwright install chromium
python -m studio.server
```

For details about data, environment settings, architecture and migrations, see [README.md](README.md), [docs/architecture.md](docs/architecture.md), and [docs/upgrading.md](docs/upgrading.md).

## Make and check a change

- Keep Python formatting and types compatible with the repository's Ruff and Pyright checks.
- Keep the browser app dependency-free at runtime; it has no frontend build step.
- Do not commit `.env`, keys, user data, source videos, rendered clips or database files.
- Treat video titles, captions, chat and transcripts as untrusted input.
- Use the existing database migration runner for schema changes. A migration must work on SQLite and PostgreSQL unless the change explicitly documents otherwise.
- Add or update focused tests for behavior changes. Run `python tests/run.py` after changes to `studio/`.
- After changing frontend assets, run `python scripts/update_asset_manifest.py`, then run
  `python scripts/frontend_checks.py` and `python scripts/e2e_browser.py`.
- Before opening a PR, run the checks relevant to the change and report any check that could not run.

## Pull requests

Use the pull-request template. Include the problem, the behavior changed, how you checked it, and screenshots for visible UI changes. Keep unrelated refactors out of a fix. A maintainer may ask for revisions before merging.

## Community expectations

Be patient and respectful. Critique code and ideas, not people. Harassment, discrimination, threats, and sharing private information are not acceptable. Maintainers may edit or close contributions that do not meet these expectations.

## License

By contributing, you agree that your contribution is offered under the repository's MIT license. Keep any third-party code and media under a compatible license and include required attribution.
