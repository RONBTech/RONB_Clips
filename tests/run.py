"""Run the test suite against a throwaway copy of the app.

    python tests/run.py

Starts RONB Clips on a spare port with an empty, temporary data folder, runs the tests against it, then stops it and
deletes the folder. Your real data and your running server are never touched. No AI key is needed.

    RONBCLIPS_TEST_DATABASE_URL=postgresql://... python tests/run.py   # run against PostgreSQL too
    RONBCLIPS_COVERAGE=1 python tests/run.py                           # collect coverage and check the threshold

The tests cover security, providers, uploads, pages, the queue and the modules behind them, and one of them renders a
real clip."""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from urllib.parse import parse_qsl, quote_plus, urlencode, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("RONBCLIPS_TEST_PORT", "8799"))
BASE = f"http://127.0.0.1:{PORT}"


def _admin_engine(base: str):
    """A connection for making and removing the throwaway schemas the suites run in."""
    import sqlalchemy as sa

    parts = urlsplit(base)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.pop("options", None)  # the admin connection must see the public schema, not a test schema
    url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))
    return sa.create_engine(url, isolation_level="AUTOCOMMIT", poolclass=sa.pool.NullPool)


def _schema_url(base: str, name: str) -> str:
    """Give one test suite its own schema, so its rows cannot leak into another suite."""
    engine = _admin_engine(base)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
            connection.exec_driver_sql(f'CREATE SCHEMA "{name}"')
    finally:
        engine.dispose()
    parts = urlsplit(base)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.pop("options", None)
    query["options"] = f"-csearch_path={name}"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query, quote_via=quote_plus), ""))


def _drop_schemas(base: str, names: list[str]):
    engine = _admin_engine(base)
    try:
        with engine.begin() as connection:
            for name in names:
                connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
    finally:
        engine.dispose()


def main() -> int:
    data = Path(tempfile.mkdtemp(prefix="ronbclips-test-"))
    env = {
        **os.environ,
        "RONBCLIPS_DATA": str(data),
        "RONBCLIPS_PORT": str(PORT),
        "RONBCLIPS_PUBLIC_URL": BASE,
        "RONBCLIPS_HOST": "127.0.0.1",
        "RONBCLIPS_AUTO_RELOAD": "0",
        # SQLite by default; set RONBCLIPS_TEST_DATABASE_URL to run the same suites against PostgreSQL.
        "DATABASE_URL": os.environ.get("RONBCLIPS_TEST_DATABASE_URL", ""),
        "RONBCLIPS_ACCOUNTS": "0",
        "RONBCLIPS_FETCH_MODEL": "0",
        "RONBCLIPS_UPDATE_CHECK": "0",
        # nothing outside this machine is contacted: no AI, no email, no Google sign-in
        "GOOGLE_CLIENT_ID": "",
        "GOOGLE_CLIENT_SECRET": "",
        "GOOGLE_CLIENT_FILE": "",
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",  # the caption tests print Devanagari text, even on a Windows console
        "RONBCLIPS_AI_KEY": "",
        "RONBCLIPS_AI_KEY_FILE": "",
        "RONBCLIPS_AI_MODELS": "",
        "RONBCLIPS_UPLOAD_PART_MB": "5",
        "RONBCLIPS_MAX_UPLOAD_MB": "64",
    }
    pg_base = os.environ.get("RONBCLIPS_TEST_DATABASE_URL", "")
    if not pg_base.startswith(("postgresql", "postgres")):
        pg_base = ""
    log = data / "server.log"
    coverage_on = os.environ.get("RONBCLIPS_COVERAGE") == "1"
    if coverage_on:
        env["COVERAGE_FILE"] = str(ROOT / ".coverage")
        (ROOT / ".coverage").unlink(missing_ok=True)
        runner = [sys.executable, "-m", "coverage", "run", "--append", "--source=studio"]
    else:
        runner = [sys.executable]
    failed = []
    schemas: list[str] = []
    with log.open("w", encoding="utf-8") as out:
        server = subprocess.Popen([sys.executable, "-m", "studio.server"], cwd=ROOT, env=env, stdout=out, stderr=subprocess.STDOUT)
    try:
        for _ in range(90):
            try:
                urllib.request.urlopen(BASE + "/healthz", timeout=2)
                break
            except Exception:  # noqa: BLE001
                if server.poll() is not None:
                    print(log.read_text(encoding="utf-8", errors="replace")[-2000:])
                    return 1
                time.sleep(1)
        else:
            print("The test server did not start.")
            return 1
        runs = [
            ("restart and orphan recovery", ["tests/test_recovery.py"]),
            ("database backup and restore", ["tests/test_backup.py"]),
            ("live recording recovery", ["tests/test_live_recover.py"]),
            ("a real server restart recovers work", ["tests/test_server_restart.py"]),
            ("graceful shutdown drains the queue", ["tests/test_shutdown_drain.py"]),
            ("queue under load", ["tests/test_load_queue.py", str(ROOT)]),
            ("security headers and request ids", ["tests/test_security.py"]),
            ("rate limits, unsafe urls and caching", ["tests/test_abuse.py"]),
            ("oauth state, secrets and signed links", ["tests/test_connect.py"]),
            ("chunked and direct uploads", ["tests/test_uploads.py"]),
            ("clip and video deletion", ["tests/test_delete.py"]),
            ("every API route answers", ["tests/test_routes.py"]),
            ("AI services", ["tests/test_ai.py"]),
            ("AI retry delays and fallbacks", ["tests/test_retry.py"]),
            ("automatic hashtags", ["tests/test_hashtags.py"]),
            ("rewrite and save clip post text", ["tests/test_post_text.py"]),
            ("live recording timestamps and picture buffer", ["tests/test_live_times.py"]),
            ("long-video loudness memory", ["tests/test_longvideo.py"]),
            ("no sign-in, and the AI key from the dashboard", ["tests/test_local.py"]),
            ("accounts-off permissions", ["tests/test_permissions.py"]),
            ("real PostgreSQL database", ["tests/test_postgres.py"]),
            ("Hindi, Urdu and Nepali captions in Roman letters", ["tests/test_roman.py"]),
            ("turning captions off and back on", ["tests/test_captions.py"]),
            ("the new-version notice", ["tests/test_updates.py"]),
            ("no limits by default", ["tests/test_limits.py"]),
            ("captions never overlap, stuck clips, sentence endings", ["tests/test_stuck.py"]),
            ("ad breaks and frozen screens", ["tests/test_static.py"]),
            ("answers that are not quite right", ["tests/test_answer.py"]),
            ("a live clip keeps the moment that caused it", ["tests/test_live.py"]),
            ("a clip is really rendered, captions and all", ["tests/test_render.py"]),
            ("knowing which game a stream is", ["tests/test_games.py"]),
            ("finding the facecam in a gaming stream", ["tests/test_facecam.py"]),
            ("fresh installs: ffmpeg, the speech model, AI errors", ["tests/test_tools.py"]),
            ("the storage folder: full drive, open, move", ["tests/test_storage.py"]),
        ]
        # Each deep test owns a temporary data folder and exercises one part of the code in detail.
        for path in sorted((ROOT / "tests").glob("test_*_deep.py")):
            label = path.stem.removeprefix("test_").removesuffix("_deep").replace("_", " ")
            runs.append((label, [path.relative_to(ROOT).as_posix()]))
        if pg_base:
            for _, args in runs:
                slug = re.sub(r"\W+", "_", Path(args[0]).stem).strip("_")[:40].lower()
                schemas.append(slug)
        for i, (name, args) in enumerate(runs):
            print(f"\n=== {name} ===", flush=True)
            run_env = env
            if pg_base:
                run_env = {**env, "RONBCLIPS_TEST_DATABASE_URL": _schema_url(pg_base, schemas[i])}
            r = subprocess.run([*runner, *args], cwd=ROOT, env=run_env, capture_output=True, text=True, encoding="utf-8", errors="replace")
            lines = (r.stdout + r.stderr).splitlines()
            passed = sum(1 for x in lines if x.startswith("PASS"))
            for x in lines:
                if not x.startswith("PASS") and x.strip():
                    print("  " + x)
            print(f"  {passed} checks passed")
            if r.returncode != 0 or any(x.startswith("FAIL") for x in lines):
                failed.append(name)
        if coverage_on:
            print("\n=== coverage ===", flush=True)
            r = subprocess.run(
                [sys.executable, "-m", "coverage", "report", "--fail-under=85"],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            print(r.stdout or r.stderr)
            if r.returncode != 0:
                failed.append("coverage below 85%")
    finally:
        server.terminate()
        try:
            server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            server.kill()
        shutil.rmtree(data, ignore_errors=True)
        if pg_base and schemas:
            _drop_schemas(pg_base, schemas)
    print("\nFAILED: " + ", ".join(failed) if failed else "\nAll tests passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
