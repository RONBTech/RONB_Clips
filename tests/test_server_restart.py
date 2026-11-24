"""A job survives the web server being killed and started again on the same data folder.

Runs the real server as a subprocess, creates a real job through the API, kills the process outright (no graceful
shutdown), then starts a fresh process and asks the API what it knows. The job is deliberately left in the middle
of its first network lookup, which is what a crash looks like; the test waits for the new server to put it back on
the queue. No AI key and no network are needed.
"""

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(tempfile.mkdtemp(prefix="rc-server-restart-"))
os.environ["RONBCLIPS_DATA"] = str(DATA)
WANTED_PORT = int(os.environ.get("RONBCLIPS_TEST_PORT", "8798"))


def check(name, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"   got {got!r}"))
    ok or fails.append(name)


def port_is_free(port):
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) != 0


if port_is_free(WANTED_PORT):
    PORT = WANTED_PORT
else:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        PORT = s.getsockname()[1]
    print(f"  port {WANTED_PORT} was already in use; using {PORT} instead")
BASE = f"http://127.0.0.1:{PORT}"

fails = []
procs = []
logs = []
hang = None
hang_stop = threading.Event()


class NeverAnswers(BaseHTTPRequestHandler):
    """The video lookup hangs, so the job stays unfinished on disk while we kill the server."""

    def do_GET(self):
        hang_stop.wait(30)

    do_HEAD = do_GET

    def log_message(self, *a):
        pass


def tail(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")[-1500:]
    except OSError:
        return ""


def kill(proc):
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/F", "/T"], capture_output=True)
    else:
        proc.kill()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        check("the server process actually stopped", False, "it ignored the kill")


def start_server(env, n):
    log_path = DATA / f"server{n}.log"
    out = log_path.open("ab")
    logs.append(out)
    p = subprocess.Popen([sys.executable, "-m", "studio.server"], cwd=ROOT, env=env, stdout=out, stderr=subprocess.STDOUT)
    procs.append(p)
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if p.poll() is not None:
            raise RuntimeError(f"server {n} exited before it was ready:\n{tail(log_path)}")
        try:
            if httpx.get(BASE + "/healthz", timeout=1).status_code == 200:
                return p
        except Exception:  # noqa: BLE001 - not listening yet
            pass
        time.sleep(0.25)
    raise RuntimeError(f"server {n} never became healthy:\n{tail(log_path)}")


try:
    # A local address that never answers is enough to keep the worker inside its first media lookup. The server is
    # told to accept this address as a video site; nothing leaves this machine.
    hang = ThreadingHTTPServer(("127.0.0.1", 0), NeverAnswers)
    hang.daemon_threads = True
    threading.Thread(target=hang.serve_forever, daemon=True).start()
    video_url = f"http://127.0.0.1:{hang.server_address[1]}/video.mp4"

    # A marker model keeps the worker from downloading the real 480 MB speech model in these two throwaway servers.
    model = DATA / "models" / os.environ.get("RONBCLIPS_ASR_MODEL", "sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8")
    model.mkdir(parents=True, exist_ok=True)
    (model / "tokens.txt").write_text("", encoding="utf-8")
    for part in ("encoder", "decoder", "joiner"):
        (model / f"{part}-int8.onnx").write_bytes(b"")

    env = {
        **os.environ,
        "RONBCLIPS_DATA": str(DATA),
        "RONBCLIPS_PORT": str(PORT),
        "RONBCLIPS_PUBLIC_URL": BASE,
        "RONBCLIPS_HOST": "127.0.0.1",
        "RONBCLIPS_AUTO_RELOAD": "0",
        "RONBCLIPS_ACCOUNTS": "0",
        "RONBCLIPS_ALLOWED_SITES": "127.0.0.1,localhost",
        "RONBCLIPS_JOB_WORKERS": "1",
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

    first = start_server(env, 1)
    check("the first test server is healthy", first.poll() is None, True)

    try:
        with httpx.Client(base_url=BASE, headers={"Origin": BASE, "sec-fetch-site": "same-origin"}, timeout=10) as client:
            made = client.post("/api/jobs", json={"url": video_url, "options": {"captions": False}})
            check("POST /api/jobs made a job", made.status_code, 200)
            if made.status_code != 200:
                print("    " + made.text[:500])
            jid = made.json().get("id", "")
            check("the API gave the job an id", bool(jid), True)
            saved = list(DATA.glob(f"users/*/jobs/{jid}/job.json"))
            check("the job is on disk before the crash", len(saved), 1)
            early = client.get(f"/api/jobs/{jid}").json()
            check("the job was still unfinished when the power was cut", early.get("status") in ("queued", "running"), True)
            if early.get("status") not in ("queued", "running"):
                print(f"    before the kill: status={early.get('status')!r} stage={early.get('stage')!r} error={early.get('error')!r}")
                print("    first server log:\n" + tail(DATA / "server1.log"))

            # Kill it like a power cut: the shutdown route that requeues jobs never runs.
            kill(first)
            check("the first process is really gone", first.poll() is not None, True)

            second = start_server(env, 2)
            check("the second test server is healthy on the same folder and port", second.poll() is None, True)

            found, status, stage = None, None, None
            seen = set()
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                answer = client.get("/api/jobs")
                row = next((j for j in answer.json() if j.get("id") == jid), None)
                if row:
                    found, status, stage = row, row.get("status"), row.get("stage", "")
                    seen.add(status)
                    # A job left by a killed worker is re-read as queued, then a worker of the new process takes
                    # it. Seeing "running" here means it is being worked on, not marked running with nobody on it.
                    if status == "running":
                        break
                time.sleep(0.2)

            check("the job is still in the API after the restart", found is not None, True)
            check("the old job is queued or running after the restart", bool(seen & {"queued", "running"}), True)
            check("a worker picked the old job up instead of leaving it stranded", "running" in seen, True)
            if not seen & {"queued", "running"} or "running" not in seen:
                print(f"    statuses seen: {sorted(seen)}  last status={status!r} stage={stage!r} error={(found or {}).get('error')!r}")
                print("    second server log:\n" + tail(DATA / "server2.log"))

            sync = client.get("/api/sync")
            check("/api/sync agrees the job exists", any(j.get("id") == jid for j in sync.json().get("jobs", [])), True)
    finally:
        for p in procs:
            kill(p)
finally:
    hang_stop.set()
    if hang is not None:
        hang.shutdown()
        hang.server_close()
    for out in logs:
        out.close()
    shutil.rmtree(DATA, ignore_errors=True)

print("\n" + (f"{len(fails)} failed" if fails else "All checks passed"))
sys.exit(1 if fails else 0)
