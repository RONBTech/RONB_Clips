"""Abuse checks that still apply with accounts off: oversized bodies, links that could point the server at an
internal address, and caching of static files and clips. Runs against the code directly with its own empty data
folder. The original signup/login, unverified-account, email, admin, throttle and visitor-address checks depended on
accounts that no longer exist, so they are not part of this file.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ["RONBCLIPS_DATA"] = tempfile.mkdtemp(prefix="pc-abuse-")
os.environ.update(
    DATABASE_URL=os.environ.get("RONBCLIPS_TEST_DATABASE_URL", ""),
    RONBCLIPS_ACCOUNTS="0",
    RONBCLIPS_PUBLIC_URL="http://127.0.0.1:8765",
    RONBCLIPS_FETCH_MODEL="0",
    RONBCLIPS_UPDATE_CHECK="0",
)
sys.path.insert(0, ".")
from fastapi.testclient import TestClient  # noqa: E402

from studio import auth, ctx, db, jobs, server  # noqa: E402

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


# ---- a body larger than the small-form cap is refused before anything reads it
big = b"x" * (300 * 1024)
check(
    "a 300 KB body is refused",
    c.post("/api/creators", content=big, headers={**ORIGIN, "Content-Type": "application/json"}).status_code,
    413,
)
check("a normal body still works", c.post("/api/creators", json={"name": "Still works"}, headers=ORIGIN).status_code, 200)

# ---- the server fetches links, so only known video sites are allowed
BAD_URLS = (
    "http://169.254.169.254/latest/meta-data",
    "https://youtube.com.evil.example/watch?v=1",
    "https://youtube.com@127.0.0.1/x",
    "file:///etc/passwd",
    "https://example.com/watch?v=1",
)
for url in BAD_URLS:
    check(f"unsafe link refused: {url}", c.post("/api/jobs", json={"url": url}, headers=ORIGIN).status_code, 400)

GOOD = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
for options in ({"range_start": -5}, {"range_start": 50, "range_end": 10}, {"quality": "abc"}, {"range_end": "NaN"}):
    check(f"bad options refused: {options}", c.post("/api/jobs", json={"url": GOOD, "options": options}, headers=ORIGIN).status_code, 400)
check("none of that created a video", c.get("/api/jobs").json(), [])


# ---- unchanged files are answered with 304, and their cache policy is kept
def cache_check(path, expect_cache):
    first = c.get(path)
    tag = first.headers.get("etag", "")
    check(f"{path}: first answer is 200 with an etag", (first.status_code, bool(tag)), (200, True))
    same = c.get(path, headers={"If-None-Match": tag})
    check(f"{path}: an unchanged copy gets 304 and no body", (same.status_code, len(same.content)), (304, 0))
    old = c.get(path, headers={"If-None-Match": '"something-else"'})
    check(f"{path}: an old copy is sent again", old.status_code, 200)
    check(f"{path}: cache policy", expect_cache(first.headers.get("cache-control", "")), True)


cache_check("/assets/main.js", lambda v: "public" in v and "max-age" in v)
check("API answers are never cached", "no-store" in c.get("/api/status").headers.get("cache-control", ""), True)

with ctx.as_user(owner.id):
    job = jobs.create({"type": "file", "path": "", "name": "cache.mp4"}, {}, enqueue=False)
clips = Path(job["dir"]) / "clips"
clips.mkdir(parents=True, exist_ok=True)
(clips / "clip01.mp4").write_bytes(b"clip bytes for the cache check" * 100)
cache_check(f"/media/{job['id']}/clip01.mp4", lambda v: "private" in v and "max-age=300" in v)

print("ALL PASSED" if not fails else f"{len(fails)} failed")
db.engine.dispose()
shutil.rmtree(os.environ["RONBCLIPS_DATA"], ignore_errors=True)
sys.exit(1 if fails else 0)
