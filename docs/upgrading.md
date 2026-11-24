# Upgrading and database migrations

## Before upgrading

1. Check the target release notes in [CHANGELOG.md](../CHANGELOG.md) and its compatibility notes in [SUPPORT.md](../SUPPORT.md).
2. Make a backup of the full data folder and database. For example, stop the app and run `python scripts/backup.py`.
3. Keep a copy of `.env` and `storage.json` outside the checkout. They can contain secrets and external storage paths; do not commit them.
4. Upgrade the code, install the pinned dependencies, then restart the app. For a source install: `git pull` followed by `python -m pip install -r requirements.txt`. For Docker: rebuild the image with `docker compose up -d --build`.
5. Check `/readyz`, the dashboard, and the server logs before removing the backup.

The app applies pending database migrations during startup. Migration versions are recorded in `schema_migrations`; applying an already-recorded version is skipped. Keep the database backup until the upgraded app has been checked.

## Migration history

| Version | Change | Upgrade impact |
|---|---|---|
| 1 | Add indexes for usage and notices | Adds indexes; application data is retained. |
| 2 | Remove older duplicate index names | Drops obsolete indexes; application data is retained. |
| 3 | Remove legacy sign-in and admin tables | **Destructive:** drops `email_tokens`, `email_ok`, `sessions`, `admin_log`, and `users`. |

### Migration 3 warning

Migration 3 permanently deletes the old account and admin tables listed above. It does not copy their rows into the current local-owner data model. This matches the current product, which has no RONB Clips login or admin account system. If an older installation still needs records from those tables, export them before starting the upgraded app. Keep a verified backup so the old database can be restored if necessary.

The migration runs in the database transaction used by startup. PostgreSQL drops dependent constraints with the legacy `users` table; SQLite uses its native table removal. Neither path preserves rows from the deleted tables.

## Rollback

Stop the newer app before restoring. Restore both the database and matching data-folder backup, then check out the previous application version and install its dependencies. Do not run an older app against a database already changed by a newer migration unless that older version explicitly supports the newer schema.

See [README.md](../README.md#backups-and-recovery) for backup and restore commands.
