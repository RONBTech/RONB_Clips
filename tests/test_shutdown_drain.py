"""Graceful shutdown finishes queued work, and a real stop signal proves it end to end.

The first half holds the workers still, queues one-off and live renders, then asks jobs.drain() to shut down. The
second half starts the real web server, queues several real jobs over HTTP, sends SIGTERM (or CTRL_BREAK_EVENT on
Windows), and checks that the queued jobs ran before the process exited.
"""

import contextlib
import functools
import http.server
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(tempfile.mkdtemp(prefix="ronbclips-drain-deep-"))
os.environ["RONBCLIPS_DATA"] = str(DATA)
os.environ.update(
    DATABASE_URL="",
    RONBCLIPS_ACCOUNTS="0",
    RONBCLIPS_PUBLIC_URL="http://127.0.0.1:8765",
    RONBCLIPS_JOB_WORKERS="1",
    RONBCLIPS_FETCH_MODEL="0",
    RONBCLIPS_UPDATE_CHECK="0",
)
sys.path.insert(0, str(ROOT))

from studio import auth, ctx, db, jobs  # noqa: E402

db.init()
UID = auth.local_user()
fails = []


def check(name, got, expected):
    ok = got == expected
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  got {got!r}, expected {expected!r}"))
    if not ok:
        fails.append(name)


# ---------------------------------------------------------------- the queue itself
ran = []
quick_started = [threading.Event(), threading.Event()]
quick_release = [threading.Event(), threading.Event()]
live_started = threading.Event()
live_release = threading.Event()

jobs.reload()  # a clean queue, as on a fresh start
jobs.start()
try:
    with ctx.as_user(UID, request_id="drain-check"):
        for i in range(2):  # the normal and the one-clip worker are both held still
            jobs.submit(lambda i=i: (quick_started[i].set(), quick_release[i].wait(10), ran.append(f"blocker{i}")))
        jobs.submit_live(lambda: (live_started.set(), live_release.wait(10), ran.append("live-blocker")))
    check("both job workers are busy", all(e.wait(5) for e in quick_started), True)
    check("the live worker is busy", live_started.wait(5), True)

    with ctx.as_user(UID, request_id="drain-check"):
        jobs.submit(lambda: ran.append("queued-one-off"))
        accepted_live = jobs.submit_live(lambda: ran.append("queued-live"))
    check("the live render was accepted", accepted_live, True)
    check("the one-off render is still waiting", jobs._q.pending(), 1)
    check("the live render is still waiting behind the busy worker", jobs._live_pending, 2)

    report = {}
    finished = threading.Event()

    def drain():
        report.update(jobs.drain(timeout=8))
        finished.set()

    threading.Thread(target=drain, daemon=True).start()
    check("shutdown has begun", not finished.wait(0.2), True)
    with ctx.as_user(UID, request_id="drain-check"):
        jobs.submit(lambda: ran.append("late-one-off"))
        late_live = jobs.submit_live(lambda: ran.append("late-live"))
    check("new live work is refused once shutdown starts", late_live, False)

    for event in quick_release:
        event.set()
    live_release.set()
    check("drain waits for the held work and then finishes", finished.wait(10), True)
    check("nothing was abandoned", report.get("abandoned"), 0)
    check("the queued one-off render ran", "queued-one-off" in ran, True)
    check("the queued live render ran", "queued-live" in ran, True)
    check("work submitted after shutdown started was dropped", {"late-one-off", "late-live"}.isdisjoint(ran), True)
finally:
    for event in quick_release:
        event.set()
    live_release.set()


# ---------------------------------------------------------------- a real stop signal
SERVER_DATA = Path(tempfile.mkdtemp(prefix="ronbclips-drain-server-"))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PORT = free_port()
BASE = f"http://127.0.0.1:{PORT}"
procs = []
open_logs = []
video_server = None
video_thread = None


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def start_server(env, log_name):
    log_path = SERVER_DATA / log_name
    out = log_path.open("ab")
    open_logs.append(out)
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
    proc = subprocess.Popen(
        [sys.executable, "-m", "studio.server"],
        cwd=ROOT,
        env=env,
        stdout=out,
        stderr=subprocess.STDOUT,
        creationflags=flags,
        start_new_session=os.name != "nt",
    )
    procs.append(proc)
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited before it was ready:\n{log_path.read_text(errors='replace')[-1500:]}")
        try:
            if httpx.get(BASE + "/healthz", timeout=1).status_code == 200:
                return proc, log_path
        except Exception:  # noqa: BLE001 - not listening yet
            pass
        time.sleep(0.2)
    raise RuntimeError(f"server never became healthy:\n{log_path.read_text(errors='replace')[-1500:]}")


def wait_for_exit(proc, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return True
        time.sleep(0.2)
    return False


def stop_hard(proc):
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/F", "/T"], capture_output=True)
    else:
        proc.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=10)


def stop_signal(proc):
    if os.name == "nt":
        os.kill(proc.pid, signal.CTRL_BREAK_EVENT)
    else:
        proc.send_signal(signal.SIGTERM)


def events(path):
    out = []
    for row in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not row.startswith("{"):
            continue
        try:
            line = json.loads(row)
        except ValueError:
            continue
        if "event" in line:
            out.append(line)
    return out


try:
    tiny = SERVER_DATA / "tiny.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=5:duration=6",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=8000:duration=6",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(tiny),
        ],
        check=True,
    )
    video_port = free_port()
    video_server = http.server.ThreadingHTTPServer(("127.0.0.1", video_port), functools.partial(QuietHandler, directory=str(SERVER_DATA)))
    video_thread = threading.Thread(target=video_server.serve_forever, daemon=True)
    video_thread.start()
    video_url = f"http://127.0.0.1:{video_port}/tiny.mp4"

    model = SERVER_DATA / "models" / os.environ.get("RONBCLIPS_ASR_MODEL", "sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8")
    model.mkdir(parents=True, exist_ok=True)
    (model / "tokens.txt").write_text("", encoding="utf-8")
    for part in ("encoder", "decoder", "joiner"):
        (model / f"{part}-int8.onnx").write_bytes(b"")

    env = {
        **os.environ,
        "RONBCLIPS_DATA": str(SERVER_DATA),
        "RONBCLIPS_PORT": str(PORT),
        "RONBCLIPS_PUBLIC_URL": BASE,
        "RONBCLIPS_HOST": "127.0.0.1",
        "RONBCLIPS_AUTO_RELOAD": "0",
        "RONBCLIPS_ALLOWED_SITES": "127.0.0.1,localhost",
        "RONBCLIPS_JOB_WORKERS": "1",
        "RONBCLIPS_DRAIN_SECONDS": "45",
        "RONBCLIPS_FETCH_MODEL": "0",
        "RONBCLIPS_UPDATE_CHECK": "0",
        "RONBCLIPS_AI_KEY": "",
        "RONBCLIPS_AI_KEY_FILE": "",
        "RONBCLIPS_AI_MODELS": "",
        "GOOGLE_CLIENT_ID": "",
        "GOOGLE_CLIENT_SECRET": "",
        "GOOGLE_CLIENT_FILE": "",
        "DATABASE_URL": "",
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
    }

    first, first_log = start_server(env, "first.log")
    ids, queued_ids = [], []
    with httpx.Client(base_url=BASE, headers={"Origin": BASE, "sec-fetch-site": "same-origin"}, timeout=20) as client:
        for _ in range(4):
            made = client.post("/api/jobs", json={"url": video_url, "options": {"captions": False, "max_clips": 1}})
            check("a real job was queued over HTTP", made.status_code, 200)
            if made.status_code == 200:
                ids.append(made.json()["id"])
        check("four jobs were made", len(ids), 4)
        if ids:
            rows = client.get("/api/jobs").json()
            queued_ids = [j["id"] for j in rows if j.get("id") in ids and j.get("status") == "queued"]
            check("some jobs were still queued when the signal arrived", bool(queued_ids), True)

        stop_signal(first)
        check("the server exited after the stop signal", wait_for_exit(first, 75), True)
        if first.poll() is None:
            stop_hard(first)

        logged = events(first_log)
        check("the server logged that it was stopping", any(x["event"] == "server.stopping" for x in logged), True)
        drains = [x for x in logged if x["event"] == "worker.drain"]
        check("shutdown logged a drain", bool(drains), True)
        if drains:
            check("queued jobs were drained before exit", drains[-1].get("drained", 0) >= len(queued_ids), True)
            check("nothing was abandoned by the drain", drains[-1].get("abandoned"), 0)
        if not drains or (drains and drains[-1].get("abandoned") != 0):
            print("    first server log tail:\n" + first_log.read_text(errors="replace")[-2000:])

        second, _ = start_server(env, "second.log")
        check("the server restarted on the same data", second.poll() is None, True)
        final = {j["id"]: j for j in client.get("/api/jobs").json()}
        check("every job survived the graceful restart", all(jid in final for jid in ids), True)
        stranded = [jid for jid in ids if final.get(jid, {}).get("status") in ("queued", "running") or final.get(jid, {}).get("resume")]
        check("no job was left waiting for recovery", stranded, [])
finally:
    for proc in procs:
        stop_hard(proc)
    if video_server is not None:
        video_server.shutdown()
        video_server.server_close()
    for out in open_logs:
        out.close()
    shutil.rmtree(DATA, ignore_errors=True)
    shutil.rmtree(SERVER_DATA, ignore_errors=True)

print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
db.engine.dispose()
sys.exit(1 if fails else 0)
