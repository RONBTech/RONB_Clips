"""Deleting clips and videos with accounts off (one built-in local owner). Runs against the code directly with its
own empty data folder: nothing outside it is touched. A second context id is used only to check that the helpers
cannot reach another user's files through the current context.
"""

import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

os.environ["RONBCLIPS_DATA"] = tempfile.mkdtemp(prefix="pc-delete-")
os.environ.update(
    DATABASE_URL=os.environ.get("RONBCLIPS_TEST_DATABASE_URL", ""),
    RONBCLIPS_ACCOUNTS="0",
    RONBCLIPS_PUBLIC_URL="http://127.0.0.1:8765",
    RONBCLIPS_FETCH_MODEL="0",
    RONBCLIPS_UPDATE_CHECK="0",
)
sys.path.insert(0, ".")
from fastapi.testclient import TestClient  # noqa: E402

from studio import account, auth, creators, ctx, db, jobs, server  # noqa: E402

db.init()
fails = []
BASE = "http://127.0.0.1:8765"
c = TestClient(server.app, base_url=BASE, follow_redirects=False)
owner = auth.local_user()


def check(name, got, expected):
    ok = expected(got) if callable(expected) else got == expected
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"   got {got!r}"))
    if not ok:
        fails.append(name)


def video(creator=None, n=3, status="done"):
    j = jobs.create({"type": "file", "path": "", "name": "v.mp4"}, {}, enqueue=False, creator=creator)
    clips = Path(j["dir"]) / "clips"
    clips.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        cid = f"clip{i + 1:02d}"
        for ext in ("mp4", "jpg", "ass", "json", "src.ts"):
            (clips / f"{cid}.{ext}").write_bytes(b"x" * 1000)
        j["clips"].append(
            {
                "id": cid,
                "status": "done",
                "file": f"{cid}.mp4",
                "cover": f"{cid}.jpg",
                "title": "Same title",
                "start": 0,
                "end": 10,
                "src": str(clips / f"{cid}.src.ts"),
            }
        )
    j["status"] = status
    jobs.save(j)
    return j


# ---- a few videos to delete, filed under the local owner
with ctx.as_user(owner.id):
    creator = creators.create({"name": "Streamer"})
    j1 = video()
    j2 = video(creator["id"])
    j3 = video(status="running")  # work in progress must be left alone
    j4 = video(n=1)

listed = [j["id"] for j in c.get("/api/jobs").json()]
check("the local owner sees their videos", j1["id"] in listed and j4["id"] in listed, True)

# ---- one clip and only its own files
with ctx.as_user(owner.id):
    check("one clip is deleted", account.delete_clip(j1["id"], "clip01"), True)
clips1 = Path(j1["dir"]) / "clips"
gone = [e for e in ("mp4", "jpg", "ass", "json", "src.ts") if (clips1 / f"clip01.{e}").exists()]
check("its video, cover, captions, notes and cut-out piece are gone", gone, [])
check("the other clips keep their files", all((clips1 / f"clip02.{e}").exists() for e in ("mp4", "ass", "json", "src.ts")), True)
with ctx.as_user(owner.id):
    check("the clip is gone from its video", [x["id"] for x in jobs.get(j1["id"])["clips"]], ["clip02", "clip03"])

# ---- the whole video's own file is not a clip's cut-out piece
whole = Path(j4["dir"]) / "whole.mp4"
whole.write_bytes(b"x" * 1000)
with ctx.as_user(owner.id):
    jj = jobs.get(j4["id"])
    jj["clips"][0]["src"] = str(whole)
    jobs.save(jj)
    check("deleting that clip still works", account.delete_clip(j4["id"], "clip01"), True)
check("the video's own file is never taken", whole.exists(), True)

# ---- a zip of several clips has one distinct name per clip
with ctx.as_user(owner.id):
    z = account.zip_clips([(j1["id"], "clip02"), (j1["id"], "clip03"), (j2["id"], "clip01"), ("nope", "x")])
    names = zipfile.ZipFile(z).namelist()
    check("several clips download as one zip with distinct names", (len(names), len(set(names))), (3, 3))
    z.unlink(missing_ok=True)

# ---- bulk delete leaves work in progress alone
with ctx.as_user(owner.id):
    result = account.delete_many([j1["id"], j3["id"]], [(j2["id"], "clip01"), (j1["id"], "clip02")])
check("bulk delete counts videos, clips and skipped work", result, {"videos": 1, "clips": 1, "skipped": 1})
check("the deleted video's folder is gone", not Path(j1["dir"]).exists(), True)
check("an unfinished video is still there", Path(j3["dir"]).exists(), True)

# ---- a creator's videos go with it
with ctx.as_user(owner.id):
    removed = account.delete_creator(creator["id"])
    check("deleting a creator removes their videos", (removed["ok"], removed["videos"]), (True, 1))
    check("the creator is gone", (creators.get(creator["id"]) is None, not creators.root(creator["id"]).exists()), (True, True))

# ---- another context's files cannot be reached from the local owner's context
OTHER = "u_other"
with ctx.as_user(OTHER):
    other = video(n=1)
other_file = Path(other["dir"]) / "clips" / "clip01.mp4"
with ctx.as_user(owner.id):
    check("another user's clip cannot be deleted", account.delete_clip(other["id"], "clip01"), False)
    check("another user's video cannot be deleted", account.delete_many([other["id"]], []), {"videos": 0, "clips": 0, "skipped": 0})
    try:
        account.zip_clips([(other["id"], "clip01")])
        zipped_other = False
    except ValueError:
        zipped_other = True
    check("another user's clips cannot be downloaded", zipped_other, True)
check("the other context's files are untouched", other_file.exists(), True)
check("the other context's folder is untouched", Path(other["dir"]).exists(), True)

print("ALL PASSED" if not fails else f"{len(fails)} failed")
db.engine.dispose()
shutil.rmtree(os.environ["RONBCLIPS_DATA"], ignore_errors=True)
sys.exit(1 if fails else 0)
