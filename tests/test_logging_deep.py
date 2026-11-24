"""Structured log lines carry a time, a level, an event and the id of the work that produced them.

Runs against the code directly with its own empty data folder. Standard output is captured for a few calls and read
back as JSON, so the test checks the lines people would actually search in a log file.
"""

import contextlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(tempfile.mkdtemp(prefix="ronbclips-logging-deep-"))
os.environ["RONBCLIPS_DATA"] = str(DATA)
os.environ.update(
    DATABASE_URL=os.environ.get("RONBCLIPS_TEST_DATABASE_URL", ""),
    RONBCLIPS_ACCOUNTS="0",
    RONBCLIPS_PUBLIC_URL="http://127.0.0.1:8765",
    RONBCLIPS_FETCH_MODEL="0",
    RONBCLIPS_UPDATE_CHECK="0",
)
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from studio import ctx, db, logs, server, tasks  # noqa: E402

db.init()
BASE = "http://127.0.0.1:8765"
fails = []


def check(name, got, expected):
    ok = got == expected
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  got {got!r}, expected {expected!r}"))
    if not ok:
        fails.append(name)


def lines(text):
    out = []
    for row in text.splitlines():
        try:
            line = json.loads(row)
        except ValueError:
            continue
        if "event" in line:
            out.append(line)
    return out


buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    with ctx.as_context("u1", request_id="req-42", ip="127.0.0.1"):
        logs.emit("check.basic", msg="hello", count=3)
line = lines(buf.getvalue())[-1]
check("a log line has the required fields", sorted(line), ["count", "event", "level", "msg", "request_id", "ts"])
check("the level and event are written", (line["level"], line["event"], line["msg"], line["count"]), ("info", "check.basic", "hello", 3))
check("the time is UTC", bool(re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", line["ts"])), True)
check("the request id comes from the context", line["request_id"], "req-42")

check("a safe caller id is kept", logs.clean_request_id("web_1.2:abc"), "web_1.2:abc")
unsafe = logs.clean_request_id("bad\nid" + "x" * 100)
check("an unsafe caller id is replaced", bool(re.fullmatch(r"[0-9a-f]{32}", unsafe)), True)
check("a background id is readable and safe", logs.background_id("job", "2026-11-24/a:b"), "job-2026-11-24-a:b")
check("an unnamed background id is still unique", logs.background_id("task") != logs.background_id("task"), True)

with ctx.as_context("u1", request_id="req-thread"):
    worker = ctx.bind(lambda: (ctx.uid(), ctx.request_id()))
result = []
t = threading.Thread(target=lambda: result.append(worker()))
t.start()
t.join(5)
check("a worker thread inherits the user and request id", result, [("u1", "req-thread")])

buf = io.StringIO()
old_level = os.environ.get("RONBCLIPS_LOG_LEVEL", "")
os.environ["RONBCLIPS_LOG_LEVEL"] = "warning"
try:
    with contextlib.redirect_stdout(buf):
        logs.emit("check.quiet", msg="not written")
        logs.emit("check.loud", "warning", msg="written")
finally:
    if old_level:
        os.environ["RONBCLIPS_LOG_LEVEL"] = old_level
    else:
        os.environ.pop("RONBCLIPS_LOG_LEVEL", None)
check("the level filter drops quieter events", [x["event"] for x in lines(buf.getvalue())], ["check.loud"])

buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    with ctx.as_context("u1", request_id="web-77"):

        def work():
            logs.emit("worker.custom", msg="from a background task")

        tid = tasks.submit("log check", work)
    for _ in range(100):
        if tasks.get(tid)["status"] != "running":
            break
        time.sleep(0.05)
background = [x for x in lines(buf.getvalue()) if x.get("request_id") == "web-77"]
check(
    "provider tasks log when they start and finish",
    [x["event"] for x in background if x["event"].startswith("task.")],
    ["task.started", "task.finished"],
)
check("code inside a provider task keeps the request id", any(x["event"] == "worker.custom" for x in background), True)

c = TestClient(server.app, base_url=BASE, follow_redirects=False)
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    answer = c.get("/api/status", headers={"X-Request-Id": "http-99"})
http_lines = [x for x in lines(buf.getvalue()) if x["event"] == "http" and x.get("request_id") == "http-99"]
check("an HTTP request logs one structured line", len(http_lines), 1)
check(
    "the HTTP line records the path and status",
    (http_lines[0]["method"], http_lines[0]["path"], http_lines[0]["status"]),
    ("GET", "/api/status", answer.status_code),
)
check("the response carries the same request id", answer.headers.get("x-request-id"), "http-99")

tasks.stop(wait=True)
print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
db.engine.dispose()
shutil.rmtree(DATA, ignore_errors=True)
sys.exit(1 if fails else 0)
