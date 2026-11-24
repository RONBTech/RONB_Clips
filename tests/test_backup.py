"""Backups can be made and put back, for both kinds of database."""

import os
import shutil
import sqlite3
import sys
import tempfile
import urllib.parse
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, ".")

import sqlalchemy as sa  # noqa: E402

from scripts import backup, restore  # noqa: E402

fails = []


def check(name, ok):
    print(("PASS " if ok else "FAIL ") + name)
    if not ok:
        fails.append(name)


def _url_to(url: str, database: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, f"/{database}", "", ""))


def _engine(url: str):
    return sa.create_engine(url, isolation_level="AUTOCOMMIT", poolclass=sa.pool.NullPool, future=True)


def _recreate(admin_url: str, name: str, owner: str):
    """Make an empty database that the test role can actually write to.

    PostgreSQL 15 and newer only give the database owner rights on the public schema. Creating the database as the
    admin role without an OWNER clause would leave the test role unable to make the marker table.
    """
    engine = _engine(admin_url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{name}"')
            connection.exec_driver_sql(f'CREATE DATABASE "{name}" OWNER "{owner}"')
    finally:
        engine.dispose()


root = Path(tempfile.mkdtemp(prefix="pc-backup-test-"))
data = root / "data"
data.mkdir()
sqlite3.connect(data / "ronbclips.db").execute("create table clips (id integer primary key)").connection.close()
(data / "settings.json").write_text('{"tour_done": true}', encoding="utf-8")
(data / "models").mkdir()
(data / "models" / "big.onnx").write_bytes(b"not copied")
os.environ["RONBCLIPS_DATA"] = str(data)
os.environ["DATABASE_URL"] = ""

saved = root / "saved"
with patch.object(sys, "argv", ["backup.py", str(saved)]):
    backup.main()
check("SQLite backup keeps the database", (saved / "ronbclips.db").is_file())
check("SQLite backup keeps settings", (saved / "settings.json").read_text(encoding="utf-8") == '{"tour_done": true}')
check("SQLite backup leaves the model out", not (saved / "models").exists())

put_back = root / "restored"
with patch.object(sys, "argv", ["restore.py", str(saved), str(put_back)]):
    restore.main()
check("restore puts the database back", (put_back / "ronbclips.db").is_file())
with sqlite3.connect(put_back / "ronbclips.db") as c:
    check("the restored database can be opened", c.execute("select count(*) from clips").fetchone() == (0,))

os.environ["DATABASE_URL"] = "postgresql://ronbclips:secret@db:5432/ronbclips"
pg_backup = root / "pg-backup"
with patch.object(sys, "argv", ["backup.py", str(pg_backup)]), patch.object(backup.subprocess, "run") as run:
    backup.main()
argv = run.call_args.args[0]
check("PostgreSQL backup uses pg_dump", run.call_count == 1 and any("pg_dump" in str(x) for x in argv))
check("PostgreSQL backup asks for safe clean handling", "--clean" in argv and "--if-exists" in argv)
check("PostgreSQL backup keeps the data folder", (pg_backup / "settings.json").is_file())
(pg_backup / "database.sql").write_text("-- a dump", encoding="utf-8")
with patch.object(sys, "argv", ["restore.py", str(pg_backup), str(root / "pg-data")]), patch.object(restore.subprocess, "run") as run:
    restore.main()
argv = run.call_args.args[0]
check("PostgreSQL restore uses psql", run.call_count == 1 and any("psql" in str(x) for x in argv))
check("PostgreSQL restore stops on the first error", "ON_ERROR_STOP=1" in argv and "--single-transaction" in argv)

# A real dump and restore is only possible where the PostgreSQL helper supplied a throwaway cluster. The SQLite
# round trip above always runs, so the ordinary suite stays self-contained.
source_url = os.environ.get("RONBCLIPS_TEST_BACKUP_SOURCE_URL", "")
target_url = os.environ.get("RONBCLIPS_TEST_BACKUP_TARGET_URL", "")
admin_url = os.environ.get("RONBCLIPS_TEST_PG_ADMIN_URL", "")
bin_dir = os.environ.get("RONBCLIPS_TEST_PG_BIN", "")
if source_url.startswith("postgresql") and target_url.startswith("postgresql") and admin_url and bin_dir:
    os.environ["RONBCLIPS_PG_DUMP_BIN"] = bin_dir
    os.environ["RONBCLIPS_PG_RESTORE_BIN"] = bin_dir
    owner = urllib.parse.unquote(urllib.parse.urlsplit(source_url).username or "")
    _recreate(admin_url, "ronbclips_backup_source", owner)
    _recreate(admin_url, "ronbclips_backup_target", owner)

    source = _engine(source_url)
    try:
        with source.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE restore_check (id integer primary key, marker text)")
            connection.exec_driver_sql("INSERT INTO restore_check (id, marker) VALUES (1, 'round-trip')")
    finally:
        source.dispose()

    real_backup = root / "real-pg-backup"
    os.environ["DATABASE_URL"] = source_url
    backup.dump_postgres(source_url, real_backup / "database.sql")
    check("the real dump contains the marker", "round-trip" in (real_backup / "database.sql").read_text(encoding="utf-8"))

    os.environ["DATABASE_URL"] = target_url
    restore.restore_postgres(real_backup / "database.sql", target_url)
    target = _engine(target_url)
    try:
        with target.connect() as connection:
            restored = connection.exec_driver_sql("SELECT marker FROM restore_check WHERE id = 1").scalar()
        check("a real PostgreSQL restore brings the rows back", restored == "round-trip")
    finally:
        target.dispose()

    # A broken dump must fail loudly and leave nothing half-created behind.
    _recreate(admin_url, "ronbclips_backup_target", owner)
    bad_sql = root / "bad.sql"
    bad_sql.write_text("CREATE TABLE broken (id integer);\nINSERT INTO broken VALUES (1);\nthis is not sql;\n", encoding="utf-8")
    failed_loudly = False
    try:
        restore.restore_postgres(bad_sql, target_url)
    except Exception:  # noqa: BLE001 - subprocess.CalledProcessError is what we want
        failed_loudly = True
    check("a broken restore exits with an error", failed_loudly)
    target = _engine(target_url)
    try:
        with target.connect() as connection:
            tables = set(sa.inspect(target).get_table_names())
        check("a broken restore rolls back completely", "broken" not in tables)
    finally:
        target.dispose()

    for name in ("ronbclips_backup_source", "ronbclips_backup_target"):
        engine = _engine(admin_url)
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{name}"')
        finally:
            engine.dispose()
else:
    print("PASS real PostgreSQL restore skipped (no test cluster)")

shutil.rmtree(root, ignore_errors=True)
print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
