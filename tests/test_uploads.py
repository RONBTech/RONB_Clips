"""Every upload route, with a tiny generated video: start, put a piece, finish, cancel, and the direct POST.
Runs against the code directly with its own empty data folder. The piece size is set before importing studio so the
one-piece file still goes through the same join path a large file would use.
"""

import os
import shutil
import sys
import tempfile

os.environ["RONBCLIPS_DATA"] = tempfile.mkdtemp(prefix="pc-uploads-")
os.environ.update(
    DATABASE_URL=os.environ.get("RONBCLIPS_TEST_DATABASE_URL", ""),
    RONBCLIPS_ACCOUNTS="0",
    RONBCLIPS_PUBLIC_URL="http://127.0.0.1:8765",
    RONBCLIPS_UPLOAD_PART_MB="5",  # the smallest piece the app allows; our file fits in one
    RONBCLIPS_MAX_UPLOAD_MB="64",
    RONBCLIPS_FETCH_MODEL="0",
    RONBCLIPS_UPDATE_CHECK="0",
)
sys.path.insert(0, ".")
from fastapi.testclient import TestClient  # noqa: E402

from studio import auth, config, db, server  # noqa: E402

db.init()
fails = []
BASE = "http://127.0.0.1:8765"
ORIGIN = {"Origin": BASE}
c = TestClient(server.app, base_url=BASE, follow_redirects=False)
owner = auth.local_user()


def check(name, got, expected):
    ok = expected(got) if callable(expected) else got == expected
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"   got {got!r}"))
    if not ok:
        fails.append(name)


data = bytes(range(256)) * 4  # a small, recognisable "video"
direct_data = b"direct-upload-bytes" * 37
H = {"Content-Type": "application/octet-stream", **ORIGIN}

# ---- start
r = c.post("/api/upload/start", json={"name": "tiny.mp4", "size": len(data), "options": {"max_clips": 2}}, headers=ORIGIN)
check("an upload can be started", r.status_code, 200)
s = r.json()
upid = s["id"]
check("the upload says how many pieces it needs", (s["parts"], s["part"] == config.UPLOAD_PART_MB * 1024 * 1024), (1, True))

# ---- the pieces address refuses misuse
check("a piece number that does not exist is refused", c.put(f"/api/upload/{upid}/9", content=b"x", headers=H).status_code, 400)
check("a piece of the wrong size is refused", c.put(f"/api/upload/{upid}/0", content=data[:-3], headers=H).status_code, 400)
check("finishing before the piece arrives is refused", c.post(f"/api/upload/{upid}/finish", json={}, headers=ORIGIN).status_code, 409)

# ---- put, retry, join
check("the piece is accepted", c.put(f"/api/upload/{upid}/0", content=data, headers=H).status_code, 200)
check("a retried piece is accepted too", c.put(f"/api/upload/{upid}/0", content=data, headers=H).status_code, 200)
r = c.post(f"/api/upload/{upid}/finish", json={}, headers=ORIGIN)
check("finishing joins the pieces into a video", r.status_code, 200)
job = r.json()
check("the joined video has an id", bool(job.get("id")), True)
source = config.user_dir(owner.id) / "jobs" / job["id"] / "source.mp4"
check("the file arrived byte for byte", source.is_file() and source.read_bytes() == data, True)
check("no pieces are left behind", not list(config.TMP_DIR.glob("uploads/up-*")), True)
check("finishing twice does not make a second video", c.post(f"/api/upload/{upid}/finish", json={}, headers=ORIGIN).status_code, 404)

# ---- cancel
s2 = c.post("/api/upload/start", json={"name": "cancel.mp4", "size": 100}, headers=ORIGIN).json()
check("a second upload starts", bool(s2.get("id")), True)
check("an upload can be cancelled", c.delete(f"/api/upload/{s2['id']}", headers=ORIGIN).status_code, 200)
check("a piece for a cancelled upload is gone", c.put(f"/api/upload/{s2['id']}/0", content=b"x" * 100, headers=H).status_code, 404)
check("a cancelled upload cannot be finished", c.post(f"/api/upload/{s2['id']}/finish", json={}, headers=ORIGIN).status_code, 404)

# ---- the direct POST upload
r = c.post(
    "/api/upload",
    files={"file": ("direct.mp4", direct_data, "video/mp4")},
    data={"options": "{}", "creator": ""},
    headers=ORIGIN,
)
check("a direct file upload works", r.status_code, 200)
direct = r.json()
check("the direct upload has an id", bool(direct.get("id")), True)
source2 = config.user_dir(owner.id) / "jobs" / direct["id"] / "source.mp4"
check("the direct file arrived byte for byte", source2.is_file() and source2.read_bytes() == direct_data, True)

# ---- bad starts are refused before any file is accepted
check("a non-video is refused", c.post("/api/upload/start", json={"name": "x.exe", "size": 100}, headers=ORIGIN).status_code, 400)
check("a made-up size is refused", c.post("/api/upload/start", json={"name": "x.mp4", "size": -4}, headers=ORIGIN).status_code, 400)
check(
    "a file over the limit is refused",
    c.post("/api/upload/start", json={"name": "x.mp4", "size": config.MAX_UPLOAD_MB * 1024 * 1024 + 1}, headers=ORIGIN).status_code,
    413,
)

print("ALL PASSED" if not fails else f"{len(fails)} failed")
db.engine.dispose()
shutil.rmtree(os.environ["RONBCLIPS_DATA"], ignore_errors=True)
sys.exit(1 if fails else 0)
