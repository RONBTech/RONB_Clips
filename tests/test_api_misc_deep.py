"""Deep checks of the shared API helpers, the storage folder and the ffmpeg lookup.

No web server and no network: outside work (subprocesses, the clock, disk space, the file manager) is replaced with
fixed answers, while all folder checks and files really happen in a throwaway data folder.
Runs against the code directly, with its own empty data folder.
"""

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

DATA = Path(tempfile.mkdtemp(prefix="ronbclips-api-misc-deep-"))
os.environ["RONBCLIPS_DATA"] = str(DATA)
os.environ.update(
    DATABASE_URL=os.environ.get("RONBCLIPS_TEST_DATABASE_URL", ""),
    RONBCLIPS_ACCOUNTS="0",
    RONBCLIPS_FETCH_MODEL="0",
    RONBCLIPS_UPDATE_CHECK="0",
    RONBCLIPS_AI_KEY="",
    PYTHONIOENCODING="utf-8",
)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import HTTPException  # noqa: E402

from studio import api_common, auth, config, ctx, load, storage, tools  # noqa: E402

fails = []
UID = auth.LOCAL_ID
POINTER = DATA / "storage.json"


def check(name, got, expected):
    ok = got == expected
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  got {got!r}, expected {expected!r}"))
    if not ok:
        fails.append(name)


def refused(call, *a, **kw):
    """The HTTP error a route would turn into a reply, or None."""
    try:
        call(*a, **kw)
    except HTTPException as e:
        return e.status_code, str(e.detail)
    return None


def raised_value(call, *a, **kw):
    try:
        call(*a, **kw)
    except ValueError as e:
        return str(e)
    return ""


# =========================================================================== shared API helpers
check("a huge page is capped", api_common.bounds(100000, -4), (200, 0))
check("an empty page still asks for one row", api_common.bounds(0, 7), (1, 7))
check("a normal page is left alone", api_common.bounds(25, 3), (25, 3))

with patch.object(api_common.auth, "throttle", side_effect=auth.AuthError("slow down")):
    err = refused(api_common.throttle, "login", 3, 60, "Wait a moment.")
    check("too many requests are refused with the caller's message", err, (429, "Wait a moment."))
with patch.object(api_common.auth, "throttle") as slow:
    check("a caller within the limit passes", api_common.throttle("login", 3, 60), None)
    check("the shared limiter was used", slow.call_args.args, ("login", 3, 60))

job = {"id": "job-1234"}
with patch.object(api_common.jobs, "get", return_value=job) as get:
    check("a valid video id finds the video", api_common.job_or_404("job-1234"), job)
    check("the id is checked before the database is touched", get.call_args.args, ("job-1234",))
with patch.object(api_common.jobs, "get") as get:
    check("a short id is refused", refused(api_common.job_or_404, "abc"), (404, "No such video"))
    check("...without asking the database", get.called, False)
with patch.object(api_common.jobs, "get", return_value=None):
    check("an unknown id is refused", refused(api_common.job_or_404, "abcdefgh"), (404, "No such video"))

with patch.object(tools, "missing", return_value=["ffmpeg"]):
    err = refused(api_common.job_slots)
    check("a new video is refused when ffmpeg is missing", (err[0], "ffmpeg" in err[1]), (503, True))
with (
    patch.object(tools, "missing", return_value=[]),
    patch.object(api_common.jobs, "busy_count", return_value=config.MAX_ACTIVE_JOBS_PER_USER),
):
    err = refused(api_common.job_slots)
    check("too many videos in progress are refused", (err[0], "videos in progress" in err[1]), (429, True))
with (
    patch.object(tools, "missing", return_value=[]),
    patch.object(api_common.jobs, "busy_count", return_value=0),
):
    check("a free slot is available", api_common.job_slots(), None)

real_gb = config.MAX_USER_GB
try:
    config.MAX_USER_GB = 0.000001  # a quota of a thousand bytes, for this test only
    api_common._disk.clear()
    api_common._reserved.clear()
    api_common.reserve("owner", 600)
    check("an upload reserves its space now", api_common._reserved["owner"], 600)
    err = refused(api_common.reserve, "owner", 500)
    check("two uploads at once cannot both fit", (err[0], "room for this file" in err[1]), (507, True))
    api_common.release("owner", 100)
    check("a cancelled piece gives space back", api_common._reserved["owner"], 500)
    api_common.release("owner", 500)
    check("the last piece removes the entry", "owner" in api_common._reserved, False)
    api_common._disk["owner"] = (time.time(), 600)
    err = refused(api_common.reserve, "owner", 500)
    check("a full folder counts what is already there", err[0], 507)
    api_common.forget_disk("owner")
    check("a forgotten folder is measured again", "owner" in api_common._disk, False)
finally:
    config.MAX_USER_GB = real_gb
    api_common._disk.clear()
    api_common._reserved.clear()

with patch.object(load, "disk_full", return_value=True), patch.object(storage, "full_message", return_value="The drive is full"):
    check("a full drive is explained before anything else", refused(api_common.quota), (507, "The drive is full"))

user_dir = config.user_dir(UID)
(user_dir / "sub").mkdir(parents=True, exist_ok=True)
(user_dir / "one.mp4").write_bytes(b"x" * 100)
(user_dir / "sub" / "two.mp4").write_bytes(b"y" * 250)
normal_size = os.path.getsize


def size_that_fails_once(p):
    if str(p).endswith("two.mp4"):
        raise OSError("file vanished")
    return normal_size(p)


with ctx.as_user(UID), patch.object(load, "disk_full", return_value=False):
    with patch.object(api_common.os.path, "getsize", side_effect=size_that_fails_once):
        api_common.quota()
    check("a file that vanishes while counting is skipped", api_common._disk[UID][1], 100)
    (user_dir / "three.mp4").write_bytes(b"z" * 50)
    check("the folder is not measured on every request", api_common._disk[UID][1], 100)
    api_common.forget_disk(UID)
    api_common.quota()
    check("after forgetting, the new file is counted", api_common._disk[UID][1], 400)
    api_common._disk[UID] = (time.time() - 100, 0)
    api_common.quota()
    check("after a minute the folder is counted again", api_common._disk[UID][1], 400)
    with patch.object(config, "MAX_USER_GB", 0.0000001):
        err = refused(api_common.quota)
        check("a user over their quota is told to delete videos", (err[0], "Your storage is full" in err[1]), (507, True))


class FakeSemaphore:
    def __init__(self, take):
        self.take = take
        self.released = 0

    def acquire(self, blocking):
        return self.take

    def release(self):
        self.released += 1


def lookup_result(tid, tries=200):
    for _ in range(tries):
        got = api_common.tasks.get(tid)
        if got["status"] != "running":
            return got
        time.sleep(0.02)
    raise AssertionError("the lookup task did not finish")


def no_site():
    raise RuntimeError("no site")


real_lookups = api_common._lookups
try:
    busy = FakeSemaphore(False)
    with patch.object(api_common, "_lookups", busy):
        got = lookup_result(api_common.lookup(lambda: "answer")["id"])
        check("too many previews at once are refused", "looking things up" in got["error"], True)
        check("a refused preview releases nothing", busy.released, 0)
    free = FakeSemaphore(True)
    with patch.object(api_common, "_lookups", free):
        got = lookup_result(api_common.lookup(lambda x: x, 5)["id"])
        check("a preview gets the lookup's answer", (got["status"], got["result"]), ("done", 5))
        check("a finished preview frees its slot", free.released, 1)
    broken = FakeSemaphore(True)
    with patch.object(api_common, "_lookups", broken):
        got = lookup_result(api_common.lookup(no_site)["id"])
        check("a failed preview still frees its slot", (got["status"], broken.released), ("error", 1))
finally:
    api_common._lookups = real_lookups

creators = [{"id": "c1"}, {"id": "c2"}]
refreshed = []
fake_live = SimpleNamespace(job={"id": "live-9"})
with (
    patch.object(api_common.creators, "all_creators", return_value=creators),
    patch.object(api_common.profiles, "needs_refresh", side_effect=lambda c: c["id"] == "c1"),
    patch.object(api_common.profiles, "refresh", side_effect=lambda cid: refreshed.append(cid)),
    patch.object(api_common.live, "active_for", side_effect=lambda cid: fake_live if cid == "c2" else None),
    patch.object(api_common.creators, "read_log", return_value=["a log"]),
):
    rows = api_common.creator_rows()
    check("a stale creator is refreshed", refreshed, ["c1"])
    check("a recording creator points at its live job", (rows[0]["live_job"], rows[1]["live_job"]), (None, "live-9"))
    check("each row carries the creator's own log", [r["log"] for r in rows], [["a log"], ["a log"]])

mixed = [["v1", "c1"], ("v2", "c2"), ["bad"], ["v3", 3], "not a pair", ["v4", "c4"], ["a b", "c5"], ["x" * 61, "c6"]]
check("only pairs of well-formed ids survive", api_common.pairs(mixed), [("v1", "c1"), ("v2", "c2"), ("v4", "c4")])
check("a page can ask for fewer pairs", api_common.pairs(mixed, limit=2), [("v1", "c1"), ("v2", "c2")])
check("anything that is not a list has no pairs", api_common.pairs("v1,c1"), [])

with (
    patch.object(api_common.sync_api, "token", return_value="token") as token,
    patch.object(api_common.sync_api, "place", return_value=("a", "b")) as place,
    patch.object(api_common.sync_api, "unchanged", return_value={"v": "same"}) as unchanged,
    patch.object(api_common.sync_api, "response", return_value={"v": "full"}) as response,
):
    check(
        "the four sync helpers pass this server's boot id",
        (api_common.sync_token(), api_common.sync_place("v"), api_common.sync_unchanged("v"), api_common.sync_response("v")),
        ("token", ("a", "b"), {"v": "same"}, {"v": "full"}),
    )
    check(
        "every sync helper is called with the boot id",
        [c.call_args.args[-1] for c in (token, place, unchanged, response)],
        [api_common.BOOT] * 4,
    )

# =========================================================================== the storage folder
real_pointer, real_from, real_data_dir = config.STORAGE_FILE, config.DATA_FROM, config.DATA_DIR
STORE = DATA / "store"
STORE.mkdir(parents=True, exist_ok=True)
config.STORAGE_FILE = POINTER
config.DATA_DIR = STORE
try:
    f1 = STORE / "one.mp4"
    f2 = STORE / "sub" / "two.mp4"
    f2.parent.mkdir(parents=True, exist_ok=True)
    f1.write_bytes(b"a" * 30)
    f2.write_bytes(b"b" * 70)
    check("the folder size includes files in subfolders", storage._size(STORE), 100)
    with patch.object(storage.os.path, "getsize", side_effect=size_that_fails_once):
        check("a file that vanishes is not counted", storage._size(STORE), 30)

    storage._used.update(at=0, bytes=0)
    check("the used size is measured once", storage.used_bytes() >= 100, True)
    (STORE / "c.bin").write_bytes(b"c" * 10)
    check("it is cached for a minute", storage.used_bytes() >= 100, True)
    storage._used["at"] = 0
    check("an old measurement is thrown away", storage.used_bytes() >= 110, True)
    check("the drive of a folder is a name", isinstance(storage.drive_of(STORE), str) and bool(storage.drive_of(STORE)), True)

    with patch.object(storage, "shutil_free", return_value=1.5):
        message = storage.full_message()
        check(
            "a full drive is named in plain words",
            all(
                x in message
                for x in (storage.drive_of(config.DATA_DIR), "1.5 GB free", f"{load.MIN_FREE_DISK_GB:g} GB", "Settings, Storage")
            ),
            True,
        )
    with patch.object(storage, "shutil_free", side_effect=OSError("gone")):
        check("an unreadable drive is reported as empty", "0.0 GB free" in storage.full_message(), True)

    POINTER.unlink(missing_ok=True)
    check("no storage file means no pending move", storage.pending(), "")
    POINTER.write_text("{not json", encoding="utf-8")
    check("a broken storage file means no pending move", storage.pending(), "")
    POINTER.write_text(json.dumps({"path": str(STORE)}), encoding="utf-8")
    check("choosing the folder already in use is no pending move", storage.pending(), "")
    target = DATA / (STORE.name + "-another")
    POINTER.write_text(json.dumps({"path": str(target)}), encoding="utf-8")
    check("a chosen folder is pending", storage.pending(), str(target.resolve()))

    with patch.object(shutil, "disk_usage", side_effect=OSError("no drive")):
        info = storage.info(local=True)
        check("an unreadable drive reports zero space", (info["free_gb"], info["total_gb"], info["full"]), (0.0, 0.0, True))
    info = storage.info(local=False)
    check(
        "the info carries the folder and its limits",
        (info["path"], info["local"], info["min_free_gb"]),
        (str(config.DATA_DIR.resolve()), False, load.MIN_FREE_DISK_GB),
    )
    config.DATA_FROM = "env"
    check("a folder set by .env is fixed", storage.info(True)["fixed"], True)
    config.DATA_FROM = real_from

    opened = []
    with patch.object(storage.sys, "platform", "win32"), patch.object(storage.os, "startfile", lambda p: opened.append(p), create=True):
        storage.open_folder()
    check("on Windows the folder is opened with the file manager", opened, [str(config.DATA_DIR.resolve())])
    opened.clear()
    with patch.object(storage.sys, "platform", "darwin"), patch.object(storage.subprocess, "Popen", lambda cmd: opened.append(cmd)):
        storage.open_folder()
    check("on a Mac the folder is opened with open", opened, [["open", str(config.DATA_DIR.resolve())]])
    opened.clear()
    with patch.object(storage.sys, "platform", "linux"), patch.object(storage.subprocess, "Popen", lambda cmd: opened.append(cmd)):
        storage.open_folder()
    check("on Linux the folder is opened with xdg-open", opened, [["xdg-open", str(config.DATA_DIR.resolve())]])

    with patch.object(
        storage.subprocess, "run", return_value=SimpleNamespace(stdout="dialog failed\n/x/never shown\n", returncode=1, stderr="")
    ):
        check("a closed folder window returns nothing", storage.pick_folder(), "")
    with patch.object(storage.subprocess, "run", return_value=SimpleNamespace(stdout="\n", returncode=0, stderr="")):
        check("an empty answer returns nothing", storage.pick_folder(), "")
    with patch.object(storage.subprocess, "run", side_effect=OSError("no window")):
        check("a folder window that cannot open returns nothing", storage.pick_folder(), "")
    with patch.object(
        storage.subprocess, "run", return_value=SimpleNamespace(stdout="\n" + str(target) + "\n", returncode=0, stderr="")
    ) as pick:
        check("the chosen folder is read from the window's last line", storage.pick_folder(), str(target))
        check("the picker is given a timeout", pick.call_args.kwargs["timeout"], 600)

    check("a folder set in .env cannot be changed", ".env" in raised_value(storage.change, str(target)), True)
    config.DATA_FROM = "chosen"
    check("no folder chosen", "Choose a folder" in raised_value(storage.change, "   "), True)
    check("a relative path is refused", "whole path" in raised_value(storage.change, "videos"), True)
    check("the current folder is refused", "already uses" in raised_value(storage.change, str(config.DATA_DIR)), True)
    check("a folder inside the current one is refused", "not inside" in raised_value(storage.change, str(STORE / "inner")), True)
    check("the current folder inside the new one is refused", "not inside" in raised_value(storage.change, str(DATA.parent)), True)
    blocked = DATA / "a file"
    blocked.write_text("not a folder", encoding="utf-8")
    check("a file cannot be used as a folder", "can't write" in raised_value(storage.change, str(blocked / "child")), True)
    with (
        patch.object(storage, "shutil_free", return_value=0.0),
        patch.object(storage, "drive_of", side_effect=lambda p: "C:" if p == target else "D:"),
    ):
        check("a folder with no room is refused", "isn't enough room" in raised_value(storage.change, str(target)), True)

    POINTER.unlink(missing_ok=True)
    moved = storage.change(str(target), move=True)
    saved = json.loads(POINTER.read_text(encoding="utf-8"))
    check(
        "a chosen folder is accepted for the next start",
        (moved["move"], saved),
        (True, {"path": str(target.resolve()), "move_from": str(config.DATA_DIR.resolve())}),
    )
    moved = storage.change(str(target), move=False)
    check(
        "a folder with no move needs no move_from",
        (moved["move"], json.loads(POINTER.read_text(encoding="utf-8"))),
        (False, {"path": str(target.resolve())}),
    )

    POINTER.unlink(missing_ok=True)
    storage.cancel_change()
    check("cancelling with nothing pending does nothing", POINTER.exists(), False)
    POINTER.write_text(json.dumps({"path": str(target)}), encoding="utf-8")
    storage.cancel_change()
    check("cancelling writes the folder back", json.loads(POINTER.read_text(encoding="utf-8")), {"path": str(config.DATA_DIR.resolve())})
    POINTER.write_text(json.dumps({"path": str(target)}), encoding="utf-8")
    config.DATA_FROM = "env"
    storage.cancel_change()
    check("cancelling with a fixed folder removes the choice", POINTER.exists(), False)
    config.DATA_FROM = real_from
finally:
    config.STORAGE_FILE = real_pointer
    config.DATA_FROM = real_from
    config.DATA_DIR = real_data_dir

# =========================================================================== ffmpeg discovery
real_env_dir = os.environ.get("RONBCLIPS_FFMPEG_DIR")
try:
    with patch.dict(os.environ, {"RONBCLIPS_FFMPEG_DIR": "D:/odd place"}), patch.object(tools.sys, "platform", "win32"):
        with patch.dict(
            os.environ,
            {
                "LOCALAPPDATA": "D:/local",
                "ProgramFiles": "D:/programs",
                "ProgramFiles(x86)": "",
                "SystemDrive": "D:",
                "ProgramData": "D:/data",
                "USERPROFILE": "D:/home",
            },
        ):
            places = tools._places()
        check("the explicitly set folder is looked in first", places[0], Path("D:/odd place"))

        def under(prefix, path):
            base = Path(prefix)
            return path == base or base in path.parents

        check(
            "Windows installer folders are looked in too",
            all(any(under(prefix, p) for p in places) for prefix in ("D:/local", "D:/programs", "D:/data", "D:/home")),
            True,
        )
    with patch.object(tools.sys, "platform", "linux"), patch.dict(os.environ, {}, clear=False):
        os.environ.pop("RONBCLIPS_FFMPEG_DIR", None)
        places = tools._places()
        check("the usual Linux folders are looked in", (Path("/usr/local/bin") in places, Path("/snap/bin") in places), (True, True))
finally:
    if real_env_dir is None:
        os.environ.pop("RONBCLIPS_FFMPEG_DIR", None)
    else:
        os.environ["RONBCLIPS_FFMPEG_DIR"] = real_env_dir

real_path = os.environ.get("PATH", "")
hidden = DATA / "hidden ffmpeg"
hidden.mkdir(exist_ok=True)
exe = ".exe" if sys.platform == "win32" else ""
for name in tools.NEEDED:
    (hidden / (name + exe)).write_bytes(b"x")
real_places = tools._places
seen = dict(tools._seen)
try:
    os.environ["PATH"] = str(DATA / "empty")
    tools._places = lambda: [hidden]

    def which_only_installed(name, *a, **kw):
        candidate = hidden / (name + exe)
        return str(candidate) if str(hidden) in os.environ.get("PATH", "") and candidate.is_file() else None

    with patch.object(tools.shutil, "which", side_effect=which_only_installed):
        check("both programs are found in the usual folder", tools.setup(), [])
        check("the folder is put on the PATH", os.environ["PATH"].startswith(str(hidden)), True)
        (hidden / ("ffprobe" + exe)).unlink()
        os.environ["PATH"] = str(DATA / "empty")
        check("one missing program is still reported", tools.setup(), ["ffprobe"])

    class BadFolder:
        def __truediv__(self, name):
            raise OSError("cannot look here")

    tools._places = lambda: [BadFolder()]
    os.environ["PATH"] = str(DATA / "empty")
    with patch.object(tools.shutil, "which", return_value=None):
        check("a folder that cannot be read is skipped", tools.setup(), list(tools.NEEDED))
finally:
    os.environ["PATH"] = real_path
    tools._places = real_places
    tools._seen.clear()
    tools._seen.update(seen)

fresh = {"at": 0.0, "missing": ["ffmpeg"]}
with patch.dict(tools._seen, fresh, clear=True), patch.object(tools, "setup", return_value=["ffmpeg"]) as setup:
    check("missing programs are looked for again after twenty seconds", tools.missing(), ["ffmpeg"])
    check("...and only once per check", setup.call_count, 1)
    tools._seen["at"] = time.time()
    check("a recent check is reused", tools.missing(), ["ffmpeg"])
    check("...without another search", setup.call_count, 1)
    tools._seen.clear()
    tools._seen.update(at=time.time(), missing=[])
    check("nothing missing means no search", tools.missing(), [])
    check("...still no search", setup.call_count, 1)

with patch.object(tools, "missing", return_value=["ffmpeg"]):
    check("a FileNotFoundError is explained", tools.explain(FileNotFoundError(2, "not found")), tools.HELP)
    check("the Windows text version is explained too", tools.explain(RuntimeError("[WinError 2] cannot find")), tools.HELP)
    check("an ffmpeg path in the error is explained", tools.explain(RuntimeError("No such file or directory: 'ffmpeg'")), tools.HELP)
with patch.object(tools, "missing", return_value=[]):
    check("a WinError 2 is explained even when the last check found nothing", tools.explain(RuntimeError("[WinError 2] gone")), tools.HELP)
    check("an unrelated FileNotFoundError is not guessed at", tools.explain(FileNotFoundError("nothing to do with video")), "")
    check("some other error is not guessed at", tools.explain(ValueError("a bad link")), "")
check("the help text says how to install ffmpeg on this machine", "ffmpeg" in tools.HELP and "install" in tools.HELP.lower(), True)

shutil.rmtree(DATA, ignore_errors=True)
print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
