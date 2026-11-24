"""The database: notifications and the record of AI use.

PostgreSQL on a server (DATABASE_URL=postgresql+psycopg://...). With no DATABASE_URL it uses a SQLite file,
which is fine for one machine."""

import time

import sqlalchemy as sa
from sqlalchemy import event

from . import config

_SQLITE = config.DATABASE_URL.startswith("sqlite")
engine = sa.create_engine(
    config.DATABASE_URL,
    pool_pre_ping=not _SQLITE,
    future=True,
    pool_size=20,
    max_overflow=40,
    **({"connect_args": {"timeout": 30}} if _SQLITE else {}),
)

# A separate connection for health checks: a stuck database must not use up the pool that real requests need, and
# a slow SQLite lock must not keep the Docker health check waiting for half a minute.
_probe = sa.create_engine(
    config.DATABASE_URL,
    poolclass=sa.pool.NullPool,
    future=True,
    **({"connect_args": {"timeout": 2}} if _SQLITE else {"connect_args": {"connect_timeout": 2}}),
)


def ping() -> dict:
    """Check the database with SELECT 1. Return only the backend and an error class name, never the address or key."""
    started = time.perf_counter()
    try:
        with _probe.connect() as connection:
            connection.execute(sa.text("SELECT 1"))
        return {"ok": True, "backend": _probe.dialect.name, "latency_ms": round((time.perf_counter() - started) * 1000, 1), "error": ""}
    except Exception as e:  # noqa: BLE001 - monitoring should get a clean degraded answer, not a traceback
        return {
            "ok": False,
            "backend": _probe.dialect.name,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "error": type(e).__name__,
        }


if _SQLITE:

    @event.listens_for(engine, "connect")
    def _fast_sqlite(conn, _record):
        # WAL: people reading are never held up by someone writing. NORMAL: safe against crashes of the app,
        # without waiting for the disk on every single write.
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()


meta = sa.MetaData()
# The indexes on (user, time) are added by migrations.py, so old databases get them as well.

usage = sa.Table(  # one row per AI call
    "usage",
    meta,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("user_id", sa.String(40), nullable=False),
    sa.Column("t", sa.Float, nullable=False),
    sa.Column("tokens_in", sa.Integer, nullable=False, server_default="0"),  # text sent to the AI
    sa.Column("tokens_out", sa.Integer, nullable=False, server_default="0"),  # text it wrote back
    sa.Column("purpose", sa.String(60), nullable=False),
    sa.Column("creator", sa.String(80)),
    sa.Column("place", sa.String(200)),
)


feedback = sa.Table(  # bug reports and ideas
    "feedback",
    meta,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("user_id", sa.String(40)),
    sa.Column("t", sa.Float, nullable=False, index=True),
    sa.Column("kind", sa.String(10), nullable=False),  # bug | idea | other
    sa.Column("message", sa.Text, nullable=False),
    sa.Column("page", sa.String(200), nullable=False, server_default=""),  # where in the app they were
    sa.Column("context", sa.String(1000), nullable=False, server_default=""),  # e.g. the error they were looking at
    sa.Column("status", sa.String(10), nullable=False, server_default="new", index=True),  # new | seen | done
    sa.Column("updated_at", sa.Float, nullable=False),
)
notices = sa.Table(  # the bell: things worth telling the local owner
    "notices",
    meta,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("user_id", sa.String(40), nullable=False),
    sa.Column("t", sa.Float, nullable=False),
    sa.Column("kind", sa.String(20), nullable=False),  # live | clips | post | feedback
    sa.Column("title", sa.String(160), nullable=False),
    sa.Column("body", sa.String(400), nullable=False, server_default=""),
    sa.Column("link", sa.String(200), nullable=False, server_default=""),
    sa.Column("ref", sa.String(80)),  # set when it must only be sent once
    sa.Column("read_at", sa.Float),
)


def init():
    meta.create_all(engine)
    _keep_private()
    from . import migrations

    migrations.init()


def _keep_private():
    """The database holds usage and local app state, so keep it private. Best effort on Windows."""
    if not _SQLITE:
        return
    import os

    path = config.DATABASE_URL.split("///", 1)[-1]
    for p in (path, path + "-wal", path + "-shm"):
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass


def now() -> float:
    return time.time()
