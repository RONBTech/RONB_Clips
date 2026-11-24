"""A small local benchmark for the work that matters: starting up, the database, an HTTP upload, probing a video,
cutting a clip and (when asked) speech to text.

Nothing here touches the real data folder. The speech model is only loaded when you pass --transcribe; add a WAV
file to time your own recording, or leave it out to have a short test tone generated for you.
"""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATA = Path(tempfile.mkdtemp(prefix="pc-benchmark-"))
os.environ["RONBCLIPS_DATA"] = str(DATA)
os.environ["DATABASE_URL"] = ""

import numpy as np  # noqa: E402

from studio import db, media, render, tools  # noqa: E402


def timed(name, function):
    started = time.perf_counter()
    result = function()
    print(f"{name}: {time.perf_counter() - started:.3f}s", flush=True)
    return result


def start_up():
    """A fresh Python process loading the whole web app, as it does when RONB Clips starts."""
    started = time.perf_counter()
    subprocess.run([sys.executable, "-c", "import studio.server"], check=True, capture_output=True, env=os.environ.copy())
    return time.perf_counter() - started


db.init()
missing = tools.setup()
if missing:
    print("ffmpeg not found:", ", ".join(missing), "- the video checks below will be skipped")
timed("start-up: import the web app in a new process", start_up)

# ---- database: a batch of AI usage rows, then the read the usage page does
ROWS = 500


def write_rows():
    with db.engine.begin() as connection:
        for n in range(ROWS):
            connection.execute(db.usage.insert().values(user_id="u_local", t=time.time() + n, purpose="benchmark"))


def read_rows():
    with db.engine.connect() as connection:
        return len(connection.execute(db.usage.select().where(db.usage.c.purpose == "benchmark")).all())


timed(f"database: insert {ROWS} usage rows", write_rows)
print("rows read back:", timed("database: read them", read_rows))

# ---- files: the size of a small upload, written, read and thrown away
BLOB = b"x" * (4 * 1024 * 1024)


def files():
    path = DATA / "benchmark.tmp"
    path.write_bytes(BLOB)
    if path.read_bytes() != BLOB:
        raise OSError("The test file did not read back the same.")
    path.unlink()


timed("files: write/read/delete 4 MB", files)

# ---- an ffmpeg source, then the first steps of the video pipeline
SOURCE = DATA / "source.mp4"


def make_source():
    proc = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=1280x720:rate=30",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            "-t",
            "3",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-c:a",
            "aac",
            str(SOURCE),
        ],
        capture_output=True,
    )
    if proc.returncode:
        print("  ffmpeg said:", proc.stderr.decode("utf-8", "replace")[-200:])
        return None
    return SOURCE


source = None if missing else timed("ffmpeg: make a 3-second test video", make_source)
if source:
    probe = timed("upload step: probe the video", lambda: media.probe(source))
    print("  source:", probe["width"], "x", probe["height"])

    def http_upload():
        """Post the file through the real route, including the request gate, as the browser does."""
        from fastapi.testclient import TestClient

        from studio import server

        base = "http://127.0.0.1:8765"
        client = TestClient(server.app, base_url=base)
        with source.open("rb") as f:
            response = client.post(
                "/api/upload",
                files={"file": ("source.mp4", f, "video/mp4")},
                data={"options": "{}"},
                headers={"Origin": base},
            )
        if response.status_code != 200:
            raise RuntimeError(f"Upload failed: {response.status_code} {response.text[:200]}")
        return response.json()

    uploaded = timed("upload: POST a 3-second file through the web route", http_upload)
    print("  job:", uploaded.get("id"))

    render.install_fonts()
    clip = {"start": 0.2, "end": 1.2, "captions": False, "layout": "crop", "hook": ""}
    result = timed("render: cut a 1-second vertical clip", lambda: render.render_clip(source, DATA / "clips", "bench", clip, [], 1280, 720))
    print("  clip:", result.get("file") if isinstance(result, dict) else result)

# ---- speech needs the 480 MB model, so it is opt-in
if "--transcribe" in sys.argv:
    from studio import transcribe

    flag = sys.argv.index("--transcribe")
    given = sys.argv[flag + 1] if len(sys.argv) > flag + 1 and not sys.argv[flag + 1].startswith("--") else ""
    if given:
        wav = Path(given)
    else:
        wav = DATA / "tone.wav"  # a short test tone: no words are expected, but the whole pipeline is timed
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-ac", "1", "-ar", "16000", str(wav)],
            check=True,
            capture_output=True,
        )
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(wav), "-f", "s16le", "-ac", "1", "-ar", "16000", "-"],
        check=True,
        capture_output=True,
    ).stdout
    audio = np.frombuffer(raw, dtype=np.int16)  # transcribe() converts to floats itself
    words = timed(f"speech: transcribe {wav.name}", lambda: transcribe.transcribe(audio))
    print("  words:", len(words), "(a generated tone has no speech)" if not given else "")
else:
    print("speech: skipped (pass --transcribe to time it, or --transcribe <file.wav>; this loads the speech model)")
