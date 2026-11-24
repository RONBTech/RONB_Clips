"""An interrupted live recording is finished from the pieces left on disk.

Runs against the code directly, with its own empty data folder. The buffer piece is real ffmpeg output, so a
queued clip really is cut and rendered. No AI key and no network are needed. It also checks the two ways this
can go wrong: ffmpeg cannot start at all, and pieces no ffmpeg ever named are swept away.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

os.environ["RONBCLIPS_DATA"] = tempfile.mkdtemp(prefix="rc-live-recover-")
os.environ.update(
    DATABASE_URL=os.environ.get("RONBCLIPS_TEST_DATABASE_URL", ""),
    RONBCLIPS_ACCOUNTS="0",
    RONBCLIPS_FETCH_MODEL="0",
    RONBCLIPS_UPDATE_CHECK="0",
    PYTHONIOENCODING="utf-8",
)
sys.path.insert(0, ".")

from studio import auth, ctx, db, jobs, live, render  # noqa: E402

fails = []
DATA = Path(os.environ["RONBCLIPS_DATA"])


def check(name, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"   got {got!r}"))
    ok or fails.append(name)


def make_piece(path: Path, seconds: int = 12) -> int:
    """A real .ts buffer piece with a picture and sound, the way the recorder writes them."""
    p = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size=320x180:rate=30:d={seconds}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={seconds}",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-f",
            "mpegts",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    return p.returncode


db.init()
owner = auth.local_user()
render.install_fonts()
jobs.start()  # its live worker performs the recovered render and the cleanup that follows it

# Sonar/transcription is not what is being tested here: a recovered clip has already been chosen, so only the
# stream clock is used. Keep the local speech model out of this test.
real_align = live.Session._align
live.Session._align = lambda self, src, guess, dur: (guess, False)

try:
    # ---------------------------------------------------------------- a queued clip from a killed recording
    with ctx.as_user(owner.id):
        job = jobs.create(
            {"type": "live", "url": "", "name": "interrupted stream"},
            {},
            enqueue=False,
            kind="live",
        )
        job.update(status="live", stage="Recording", width=320, height=180, recover=True)
        buf = Path(job["dir"]) / "buffer"
        buf.mkdir(parents=True, exist_ok=True)
        piece = buf / "p01_00000.ts"
        check("ffmpeg made a real buffer piece", make_piece(piece), 0)
        (buf / "p01.csv").write_text("p01_00000.ts,0,12\n", encoding="utf-8")
        (Path(job["dir"]) / "words.json").write_text("[]", encoding="utf-8")
        job["clips"] = [
            {
                "id": "clip001",
                "start": 1.0,
                "end": 5.0,
                "hook": "Interrupted",
                "title": "Interrupted stream",
                "description": "",
                "hashtags": [],
                "score": 70,
                "reason": "picked before the restart",
                "style": "bounce",
                "layout": "blur",
                "captions": False,
                "status": "queued",
                "version": 0,
            }
        ]
        jobs.save(job)

        raised = None
        try:
            live.recover(job)
        except Exception as e:  # noqa: BLE001 - recover must not throw at all
            raised = e
        check("live.recover returns without raising", raised is None, True)

        status, saved = None, None
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            saved = jobs.get(job["id"])
            status = saved["clips"][0].get("status")
            if status in ("done", "error"):
                break
            time.sleep(0.5)
        check("the queued clip is finished instead of staying queued", status in ("done", "error"), True)
        check("the interrupted clip really rendered", status, "done")
        if status == "error":
            print("    clip error: " + str(saved["clips"][0].get("error")))
        check("recovery clears the recover flag", "recover" not in jobs.get(job["id"]), True)
        check("recovery removes the buffer afterwards", not any(buf.iterdir()), True)

    # ---------------------------------------------------------------- pieces ffmpeg never named
    # test_live_times.py covers this too; it is checked here because this is where a stale piece could be
    # mistaken for part of the interrupted recording.
    orphan_dir = Path(tempfile.mkdtemp(prefix="rc-live-orphan-"))
    try:
        b = orphan_dir / "buffer"
        b.mkdir()
        (b / "p01_00000.ts").write_bytes(b"x")
        (b / "p01.csv").write_text("p01_00000.ts,0,6\n", encoding="utf-8")
        old = b / "p01_00009.ts"
        old.write_bytes(b"x" * 32)
        old_time = time.time() - 400
        os.utime(old, (old_time, old_time))
        fresh = b / "p01_00010.ts"  # still being written when the studio stopped
        fresh.write_bytes(b"x")
        s = live.Session(
            {
                "id": "orphan",
                "title": "a stream",
                "uploader": "someone",
                "dir": str(orphan_dir),
                "creator": "",
                "ai": {},
                "clips": [],
                "source": {"url": "", "name": "a stream"},
            },
            {"buffer_min": 10},
        )
        live._pieces_on_disk(s)
        s._scan_segments()
        check("an old orphan piece is removed", old.exists(), False)
        check("a piece ffmpeg named survives", (b / "p01_00000.ts").exists(), True)
        check("a fresh unfinished piece survives", fresh.exists(), True)
    finally:
        shutil.rmtree(orphan_dir, ignore_errors=True)

    # ---------------------------------------------------------------- ffmpeg cannot start
    with ctx.as_user(owner.id):
        broken = jobs.create(
            {"type": "live", "url": "http://127.0.0.1/stream", "name": "ffmpeg missing"},
            {},
            enqueue=False,
            kind="live",
        )
        broken.update(status="live", stage="Connecting to the stream")
        jobs.save(broken)
    s = live.Session(broken, {"buffer_min": 10})
    info = {"urls": ["http://127.0.0.1:1/stream"], "title": "a stream", "uploader": "someone"}
    real_resolve, real_popen = live.resolve, live.subprocess.Popen
    live.resolve = lambda url: info  # no yt-dlp / network in this test

    def no_ffmpeg(*a, **k):
        raise FileNotFoundError("[WinError 2] ffmpeg.exe was not found")

    live.subprocess.Popen = no_ffmpeg  # the same error Windows gives when the binary cannot start
    began = time.monotonic()
    try:
        with ctx.as_user(owner.id):
            s._ingest()
            s._analyze()  # this is what writes the final status and the reason to the job
        elapsed = time.monotonic() - began
    finally:
        live.resolve, live.subprocess.Popen = real_resolve, real_popen
    with ctx.as_user(owner.id):
        ended = jobs.get(broken["id"])
    reason = str(ended.get("error") or "")
    check("a missing ffmpeg does not hang the recording", elapsed < 10, True)
    check("the session is marked ended when ffmpeg cannot start", s.ended, True)
    check("the job is ended with a reason, not left live", ended["status"], "error")
    check("the reason explains that recording could not start", "recording stopped" in reason.lower() and "ffmpeg" in reason.lower(), True)

finally:
    live.Session._align = real_align
    jobs.stop(wait=True)

print("\n" + (f"{len(fails)} failed" if fails else "All checks passed"))
shutil.rmtree(DATA, ignore_errors=True)
sys.exit(1 if fails else 0)
