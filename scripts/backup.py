"""Back up the RONB Clips data folder and its database.

SQLite is copied through SQLite itself, so the backup is complete even while RONB Clips is running. PostgreSQL is
dumped with pg_dump (in the db container by default). The speech model and temporary files are left out: they take
a lot of room and can be fetched again.
"""

import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

try:  # imported as scripts.backup by the tests, or run directly from the scripts folder
    from . import pg_tools
except ImportError:
    import pg_tools  # type: ignore[no-redef]

SKIP = {"models", "tmp"}  # re-downloaded or thrown away on the next start
# Plain SQL keeps the database.sql file readable. Clean statements make a restore into a freshly recreated database
# deterministic; --if-exists keeps notices out of the log when the object is not there. No owner or privileges means
# the dump can be put back under a different role.
DUMP_FLAGS = ["--format=plain", "--clean", "--if-exists", "--no-owner", "--no-privileges"]


def data_folder() -> Path:
    return Path(os.environ.get("RONBCLIPS_DATA") or "data")


def sqlite_file(data: Path) -> Path:
    url = os.environ.get("DATABASE_URL") or ""
    if url.startswith("sqlite"):
        named = url.split("///", 1)[-1]
        if named:
            return Path(named)
    return data / "ronbclips.db"


def copy_files(data: Path, target: Path, database_name: str = ""):
    """Copy everything except the database, which needs a consistent copy of its own."""
    for entry in data.iterdir():
        if entry.name in SKIP or entry.name in (database_name, database_name + "-wal", database_name + "-shm"):
            continue
        if entry.name.endswith(("-wal", "-shm")):
            continue
        if entry.resolve() == target.resolve():
            continue  # never copy the backup into itself
        to = target / entry.name
        if entry.is_dir():
            shutil.copytree(entry, to, dirs_exist_ok=True)
        elif entry.is_file():
            shutil.copy2(entry, to)


def dump_postgres(url: str, saved: Path):
    """Write a complete SQL dump to a temporary file, then put it in place only if pg_dump succeeded."""
    argv, parts = pg_tools.command("dump", url, DUMP_FLAGS)
    print(f"Dumping PostgreSQL with: {pg_tools.redact(argv, parts['password'])}", flush=True)
    saved.parent.mkdir(parents=True, exist_ok=True)
    tmp = saved.with_name(saved.name + ".tmp")
    try:
        with tmp.open("wb") as out:
            subprocess.run(argv, stdout=out, stderr=subprocess.PIPE, check=True, env=pg_tools.child_env(parts["password"]))
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(saved)


def main():
    pg_tools.load_env()  # the scripts read the same .env as the app
    target = Path(sys.argv[1] if len(sys.argv) > 1 else f"ronbclips-backup-{time.strftime('%Y%m%d-%H%M%S')}")
    target.mkdir(parents=True, exist_ok=True)
    data = data_folder()
    url = os.environ.get("DATABASE_URL") or ""

    if url and not url.startswith("sqlite"):
        copy_files(data, target)
        saved = target / "database.sql"
        dump_postgres(url, saved)
        print(f"Backed up {data} and the database to {saved}")
        return

    db_path = sqlite_file(data)
    if not db_path.is_file():
        raise SystemExit(f"Database not found: {db_path}")

    copy_files(data, target, db_path.name)
    with sqlite3.connect(db_path) as source, sqlite3.connect(target / db_path.name) as saved:
        source.backup(saved)
    print(target)


if __name__ == "__main__":
    main()
