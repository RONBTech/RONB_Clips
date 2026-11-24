"""Restore a RONB Clips backup: a whole data folder, or just one SQLite database file.

Stop RONB Clips before restoring, so nothing writes to the files while they are replaced. A PostgreSQL backup is a
folder with database.sql in it; that file is fed to psql (in the db container by default).

PostgreSQL target: recreate the database first, or make sure it is empty. Restoring into a database that already has
RONB Clips tables is not supported. The dump contains --clean statements, and psql is told to stop at the first error
and undo the whole restore if anything fails.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

try:  # imported as scripts.restore by the tests, or run directly from the scripts folder
    from . import pg_tools
except ImportError:
    import pg_tools  # type: ignore[no-redef]

RESTORE_FLAGS = ["-v", "ON_ERROR_STOP=1", "--single-transaction"]


def restore_postgres(sql_file: Path, url: str):
    argv, parts = pg_tools.command("restore", url, RESTORE_FLAGS)
    print(f"Restoring PostgreSQL with: {pg_tools.redact(argv, parts['password'])}", flush=True)
    with sql_file.open("rb") as source:
        subprocess.run(argv, stdin=source, check=True, env=pg_tools.child_env(parts["password"]))


def copy_files(backup: Path, destination: Path):
    destination.mkdir(parents=True, exist_ok=True)
    for entry in backup.iterdir():
        to = destination / entry.name
        if entry.is_dir():
            shutil.copytree(entry, to, dirs_exist_ok=True)
        elif entry.is_file():
            shutil.copy2(entry, to)


def main():
    pg_tools.load_env()
    if len(sys.argv) != 3:
        raise SystemExit("Usage: python scripts/restore.py <backup folder or database file> <destination folder or file>")
    backup, destination = Path(sys.argv[1]), Path(sys.argv[2])
    if not backup.exists():
        raise SystemExit(f"Backup not found: {backup}")
    url = os.environ.get("DATABASE_URL") or ""
    postgres = bool(url) and not url.startswith("sqlite")

    if backup.is_dir():
        if postgres:
            sql = backup / "database.sql"
            if not sql.is_file():
                raise SystemExit(f"No database.sql in the backup folder: {backup}")
            # Restore the database first: a failed dump must not leave a half-updated data folder behind.
            restore_postgres(sql, url)
        copy_files(backup, destination)
        print(f"Restored {backup} to {destination}")
        return

    if postgres:
        if backup.suffix != ".sql":
            raise SystemExit(f"A PostgreSQL backup is a .sql file: {backup}")
        restore_postgres(backup, url)
        print(f"Restored {backup} to PostgreSQL")
        return

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backup, destination)
    print(f"Restored {backup} to {destination}")


if __name__ == "__main__":
    main()
