"""Small explicit database migration runner for SQLite and PostgreSQL."""

import sqlalchemy as sa

from . import db


def _drop_old_tables(connection):
    """Remove the tables left behind by the old sign-in and admin features.

    PostgreSQL will not drop ``users`` while the old foreign keys point at it, so let it drop those
    constraints with the table. SQLite has no such restriction and does not understand CASCADE.
    """
    cascade = " CASCADE" if connection.dialect.name == "postgresql" else ""
    for name in ("email_tokens", "email_ok", "sessions", "admin_log", "users"):
        connection.execute(sa.text(f"DROP TABLE IF EXISTS {name}{cascade}"))


MIGRATIONS = (
    (
        1,
        "Create usage and notice indexes",
        (
            "CREATE INDEX IF NOT EXISTS usage_user_t ON usage (user_id, t)",
            "CREATE INDEX IF NOT EXISTS notices_user_t ON notices (user_id, t)",
        ),
    ),
    (
        2,
        "Remove the older copies of those indexes",
        (
            "DROP INDEX IF EXISTS usage_user_t_idx",
            "DROP INDEX IF EXISTS notices_user_t_idx",
        ),
    ),
    (
        3,
        "Remove the old account and admin tables",
        _drop_old_tables,
    ),
)


def init():
    with db.engine.begin() as connection:
        connection.execute(
            sa.text(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version INTEGER PRIMARY KEY, name VARCHAR(200) NOT NULL, applied_at REAL NOT NULL)"
            )
        )
        applied = {row[0] for row in connection.execute(sa.text("SELECT version FROM schema_migrations"))}
        import time

        for version, name, statements in MIGRATIONS:
            if version in applied:
                continue
            if callable(statements):
                statements(connection)
            else:
                for statement in statements:
                    connection.execute(sa.text(statement))
            connection.execute(
                sa.text("INSERT INTO schema_migrations (version, name, applied_at) VALUES (:version, :name, :applied_at)"),
                {"version": version, "name": name, "applied_at": time.time()},
            )
