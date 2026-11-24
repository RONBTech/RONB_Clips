"""Shared setup for the PostgreSQL backup and restore scripts.

The app reads its settings from the environment or a .env file. These scripts do the same, and build the pg_dump/psql
command as a list so Windows paths with spaces work. Passwords go in PGPASSWORD for the child process, never in the
command line or in printed output.
"""

import json
import os
import shlex
import shutil
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env():
    """Use .env values that were not already set in the environment, just like studio.config."""
    f = ROOT / ".env"
    if not f.exists():
        return
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def url_parts(url: str) -> dict:
    """Return the host, port, user, password and database from a SQLAlchemy PostgreSQL URL."""
    parts = urllib.parse.urlsplit(url)
    return {
        "host": parts.hostname or "",
        "port": str(parts.port or "5432"),
        "user": urllib.parse.unquote(parts.username or ""),
        "password": urllib.parse.unquote(parts.password or ""),
        "database": urllib.parse.unquote(parts.path.lstrip("/")),
    }


def _override_argv(value: str) -> list[str]:
    """Accept a JSON array (best for Windows paths) or a plain command string for older setups."""
    value = value.strip()
    if value.startswith("["):
        rows = json.loads(value)
        if isinstance(rows, list) and all(isinstance(x, str) for x in rows):
            return rows
    return [x.strip("\"'") for x in shlex.split(value, posix=False)]


def command(kind: str, url: str, flags: list[str]) -> tuple[list[str], dict]:
    """Build (argv, url parts) for pg_dump or psql.

    Kind is "dump" or "restore". A *_BIN setting points at a local executable (or its folder); otherwise the command
    runs inside the db container with `docker compose exec`. The legacy *_PG_DUMP / *_PG_RESTORE settings still win
    when set, so existing deployments keep working.
    """
    parts = url_parts(url)
    exe = "pg_dump" if kind == "dump" else "psql"
    override = os.environ.get(f"RONBCLIPS_PG_{kind.upper()}") or ""
    local = os.environ.get(f"RONBCLIPS_PG_{kind.upper()}_BIN") or ""

    if override:
        argv = [*_override_argv(override), *flags]
    elif local:
        path = Path(local)
        if path.is_dir():
            path = path / (exe + (".exe" if os.name == "nt" else ""))
        argv = [str(path), "-h", parts["host"], "-p", parts["port"], "-U", parts["user"], "-d", parts["database"], *flags]
    else:
        argv = ["docker", "compose", "exec", "-T", "db", exe, "-U", parts["user"], parts["database"], *flags]
    return argv, parts


def redact(argv: list[str], secret: str = "") -> str:
    """Show the command for logs without showing a password from a URL or an old-style override."""
    shown = []
    mask = secret if len(secret) >= 3 else ""
    for arg in argv:
        if "://" in arg and "@" in arg:
            scheme, rest = arg.split("://", 1)
            userinfo, tail = rest.split("@", 1)
            if ":" in userinfo:
                userinfo = userinfo.split(":", 1)[0] + ":****"
            arg = f"{scheme}://{userinfo}@{tail}"
        if arg.startswith("-W") and len(arg) > 2:
            arg = "-W****"
        if mask and mask in arg:
            arg = arg.replace(mask, "****")
        shown.append(arg)
    return " ".join(shlex.quote(x) for x in shown)


def child_env(password: str) -> dict:
    """Give the child process the password without putting it in argv."""
    env = os.environ.copy()
    if password:
        env["PGPASSWORD"] = password
    else:
        env.pop("PGPASSWORD", None)
    return env


def find_bin(bin_dir: str, exe: str) -> str:
    """Find pg_dump/psql in an explicit folder, or on PATH."""
    name = exe + (".exe" if os.name == "nt" else "")
    if bin_dir:
        path = Path(bin_dir) / name
        return str(path) if path.is_file() else ""
    return shutil.which(name) or ""
