"""Deep tests for studio/creators.py: workspaces, settings, profiles, logs, caching and isolation.

Everything is kept in a fresh data folder; no site, service or external process is used. Run with:
python tests/test_creators_deep.py
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

DATA = tempfile.mkdtemp(prefix="pc-creators-deep-")
os.environ["RONBCLIPS_DATA"] = DATA
os.environ.update(
    DATABASE_URL=os.environ.get("RONBCLIPS_TEST_DATABASE_URL", ""),
    RONBCLIPS_ACCOUNTS="0",
    RONBCLIPS_FETCH_MODEL="0",
    RONBCLIPS_UPDATE_CHECK="0",
)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from studio import auth, config, creators, ctx, db  # noqa: E402

db.init()
UID = auth.local_user().id
ctx.set_uid(UID)
creators._mem.clear()
creators._log_cache.clear()
fails = []


def check(name, got, expected):
    ok = expected(got) if callable(expected) else got == expected
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"   got {got!a}, wanted {expected!a}"))
    if not ok:
        fails.append(name)
    return ok


def expect_raise(name, fn, kind=Exception, part=""):
    try:
        fn()
    except Exception as e:  # noqa: BLE001 - the test names the kind it wants
        ok = isinstance(e, kind) and (not part or part in str(e))
        check(name, ok, True if ok else f"{type(e).__name__}: {e}")
        return e if ok else None
    check(name, "no exception was raised", kind.__name__)
    return None


def saved(cid):
    return json.loads((creators.root(cid) / "creator.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- links and safe ids
check(
    "channel links are sorted by their site",
    [
        creators.platform_key(x)
        for x in (
            "https://www.youtube.com/@someone",
            "youtu.be/abc",
            "https://twitch.tv/someone",
            "http://kick.com/someone",
            "https://example.test/x",
            "example.com",
            "",
        )
    ],
    ["youtube", "youtube", "twitch", "kick", "", "", ""],
)
check("host names are read without caring about case", creators.platform_key("https://TWITCH.TV/Name"), "twitch")
check("a workspace lives under this user only", creators.root("abc_1"), config.user_dir(UID) / "creators" / "abc_1")
for bad in ("", None, "../etc", "a/b", "a b", "x" * 61):
    expect_raise("an unsafe workspace id is refused: " + str(bad)[:12], lambda b=bad: creators.root(b), ValueError, "bad creator id")

# ---------------------------------------------------------------- one channel per site, always a recording channel
fixed = creators._fix(
    {
        "channel": "https://youtube.com/@main",
        "channels": {"youtube": "https://youtube.com/@other", "twitch": "https://twitch.tv/t", "bad": "https://x.test"},
        "ai_every_min": 0,
    }
)
check("unknown platforms are dropped", sorted(fixed["channels"]), ["twitch", "youtube"])
check("an existing link is not overwritten by the channel field", fixed["channels"]["youtube"], "https://youtube.com/@other")
check("the channel field follows the link that is already there", fixed["channel"], "https://youtube.com/@other")
check("Auto for the AI interval means three minutes", fixed["ai_every_min"], 3)
added_channel = creators._fix({"channel": "https://youtube.com/@main", "channels": {"twitch": "https://twitch.tv/t"}})
check("the recording channel is added when its platform is missing", added_channel["channels"]["youtube"], "https://youtube.com/@main")
check("the added channel stays the recording one", added_channel["channel"], "https://youtube.com/@main")
fallback = creators._fix({"channel": "https://vimeo.com/x", "channels": {"twitch": "https://twitch.tv/t"}})
check("an unusable recording channel falls back to another one", fallback["channel"], "https://twitch.tv/t")
check("an empty workspace gets no channel", creators._fix({"channels": {}})["channel"], "")

# ---------------------------------------------------------------- settings from the page
clean = creators._clean(
    {
        "min_len": 10**9,
        "max_len": -5,
        "post_gap_min": 1,
        "daily_cap": 999,
        "rate_per_1k": -7,
        "plan": "hacker",
        "decide_mode": "whatever",
        "privacy": "secret",
        "accounts": ["a"] * 25,
        "channels": {"youtube": "  https://youtube.com/@y  ", "bad": "x", "twitch": " "},
        "captions": False,
        "unknown": "ignored",
        "name": "  A name  ",
    }
)
check("numbers are kept inside their limits", [clean[k] for k in ("min_len", "max_len", "post_gap_min", "daily_cap")], [120, 10, 5, 50])
check("a negative rate is zero", clean["rate_per_1k"], 0.0)
check("unknown choices fall back to the safe ones", [clean[k] for k in ("plan", "decide_mode", "privacy")], ["free", "both", "public"])
check("only known channels with links survive", clean["channels"], {"youtube": "https://youtube.com/@y"})
check("accounts are trimmed to twenty", len(clean["accounts"]), 20)
check("text is trimmed, unknown keys are dropped", (clean["name"], "unknown" in clean, clean["captions"]), ("A name", False, False))
check(
    "switches are real booleans",
    [
        creators._clean(x)[k]
        for x, k in (
            ({"auto_watch": "yes"}, "auto_watch"),
            ({"autopost": 0}, "autopost"),
            ({"ai_only_on_spike": "off"}, "ai_only_on_spike"),
        )
    ],
    [True, False, True],
)
check("missing and null settings are left out", creators._clean({"style": None}), {})
check("a valid rate is kept", creators._clean({"rate_per_1k": "2.5"})["rate_per_1k"], 2.5)
check("a valid plan is kept", [creators._clean({"plan": p})["plan"] for p in ("free", "pro", "max")], ["free", "pro", "max"])
check("accounts must be a list", creators._clean({"accounts": "abc"})["accounts"], [])
expect_raise("captions must be on or off", lambda: creators._clean({"captions": "yes"}), ValueError, "Captions")

# ---------------------------------------------------------------- creating workspaces
before_version = ctx.version(UID)
expect_raise("a creator needs a name", lambda: creators.create({}), ValueError, "Give the creator a name")
one = creators.create(
    {
        "name": "  Hello, World!  ",
        "channel": "https://youtube.com/@hello",
        "style": "pop",
        "channels": {"youtube": "https://youtube.com/@hello"},
    }
)
two = creators.create({"name": "Hello, World!", "channel": "https://twitch.tv/hello"})
blank = creators.create({"name": "!!!"})
long_name = creators.create({"name": "A" * 60})
check("a name becomes a short id", one["id"], "hello-world")
check("a second creator with the same name gets a number", two["id"], "hello-world-2")
check("a name with no letters still gets an id", blank["id"], "creator")
check("a long name is cut short", long_name["id"], "a" * 40)
check(
    "defaults fill in the settings the page did not send",
    (one["style"], one["min_len"], one["plan"], one["channels"]),
    ("pop", 15, "free", {"youtube": "https://youtube.com/@hello"}),
)
check(
    "a creator has a time and a folder",
    (one["created"] > 0, (creators.root(one["id"]) / "videos").is_dir(), (creators.root(one["id"]) / "live").is_dir()),
    (True, True, True),
)
check("the file on disk matches the creator", saved(one["id"])["name"], "Hello, World!")
check("creating a workspace and logging it changes the page", ctx.version(UID) - before_version, 8)
check("the first log line says who made the workspace", creators.read_log(one["id"], 1)[0]["msg"], "Workspace created for Hello, World!")

check("a missing creator is nothing", creators.get("not_here"), None)
for bad in ("", None, "bad/id", "x" * 61):
    check("an unsafe id cannot name a creator: " + str(bad)[:8], creators.get(bad), None)
check("a creator can be read back", creators.get(one["id"])["style"], "pop")
listed = {c["id"] for c in creators.all_creators()}
check("all four workspaces are listed", len(listed), 4)
copy_of_one = creators.all_creators()[0]
copy_of_one["name"] = "Changed in memory"
check("listed creators are copies, not the live settings", creators.get(one["id"])["name"], "Hello, World!")

# ---------------------------------------------------------------- updating settings
expect_raise("updating someone who is not there fails", lambda: creators.update("no_such_creator", {}), KeyError)
bad_update = creators.get(one["id"])
expect_raise(
    "a clip cannot start after it ends", lambda: creators.update(one["id"], {"min_len": 70, "max_len": 40}), ValueError, "Shortest clip"
)
check("a refused update writes nothing", creators.get(one["id"])["min_len"], bad_update["min_len"])
updated = creators.update(one["id"], {"min_len": 1, "max_len": 1000, "captions": True})
check("update clamps numbers too", (updated["min_len"], updated["max_len"]), (5, 180))
check("update wrote the file", saved(one["id"])["captions"], True)

removed = creators.update(two["id"], {"channels": {"twitch": "https://twitch.tv/new"}})
check("removing the recording channel picks another one", removed["channel"], "https://twitch.tv/new")
added = creators.update(two["id"], {"channel": "https://kick.com/new"})
check("adding a recording channel also adds its platform link", added["channels"]["kick"], "https://kick.com/new")
check("the recording channel is the new one", added["channel"], "https://kick.com/new")

# ---------------------------------------------------------------- a channel's public profile and live state
creators.set_profiles("nowhere", {"youtube": {}})
check("a profile for nobody is ignored", creators.get("nowhere"), None)
profiles = {
    "youtube": {"name": "YT", "followers": 10},
    "twitch": {"name": "TW", "followers": 20},
    "kick": {"name": "K", "followers": 30},
}
pid = two["id"]
creators.set_profiles(pid, profiles, {"live_at": 100, "live_job": None})
with_profile = creators.get(pid)
check("profiles from channels the creator does not use are dropped", sorted(with_profile["profiles"]), ["kick", "twitch"])
check("the recording channel's profile is the one shown", with_profile["profile"], {"name": "K", "followers": 30})
check("extra live details are saved", with_profile["live_at"], 100)
profile_file = creators.root(pid) / "creator.json"
raw_before = profile_file.read_bytes()
version_before = ctx.version(UID)
creators.set_profiles(pid, {k: dict(v) for k, v in profiles.items()}, {"live_at": 200})
check("a refreshed live_at is remembered without a write", profile_file.read_bytes(), raw_before)
check("a refreshed live_at does not make every page reload", ctx.version(UID), version_before)
check("the in-memory live_at did move on", creators.get(pid)["live_at"], 200)
creators.set_profiles(pid, {k: dict(v, followers=99) for k, v in profiles.items()}, {"live_at": 300})
check("a real profile change is written", saved(pid)["profiles"]["kick"]["followers"], 99)
check("a real profile change makes the page reload", ctx.version(UID), version_before + 1)

# ---------------------------------------------------------------- logs and how they are read
missing_id = creators.create({"name": "No Log Yet"})
log_path = creators.root(missing_id["id"]) / "log.jsonl"
log_path.unlink()
check("a creator with no log has no lines", creators.read_log(missing_id["id"]), [])
creators.log("not_a_creator", "ignored")
check("a log for no workspace is ignored", log_path.exists(), False)
version_before = ctx.version(UID)
creators.log(missing_id["id"], "first line")
creators.log(missing_id["id"], "second line")
check("each log line changes the page", ctx.version(UID) - version_before, 2)
check("the log is newest first", [x["msg"] for x in creators.read_log(missing_id["id"])], ["second line", "first line"])
check("read_log asks for the newest n", [x["msg"] for x in creators.read_log(missing_id["id"], 1)], ["second line"])
first_read = creators.read_log(missing_id["id"], 1)
second_read = creators.read_log(missing_id["id"], 1)
check("a second read uses the cache", second_read, first_read)
with ctx.as_user(None):
    creators.log(missing_id["id"], "no signed-in user")
check(
    "a background task with no user cannot write into a log",
    [x["msg"] for x in creators.read_log(missing_id["id"])],
    ["second line", "first line"],
)

with log_path.open("a", encoding="utf-8") as f:
    f.write("{this is not json}\n")
    f.write(json.dumps({"t": 1, "msg": "third line"}) + "\n")
check(
    "a broken line in a log is skipped",
    [x["msg"] for x in creators.read_log(missing_id["id"], 3)],
    ["third line", "second line", "first line"],
)

real_max, real_keep = creators.LOG_MAX_BYTES, creators.LOG_KEEP_LINES
creators.LOG_MAX_BYTES, creators.LOG_KEEP_LINES = 100, 3
try:
    for i in range(10):
        creators.log(missing_id["id"], f"entry {i}")
    kept = creators.read_log(missing_id["id"], 20)
    check("an old log is trimmed to the newest lines", len(kept), 3)
    check("the newest log line survives trimming", kept[0]["msg"], "entry 9")
finally:
    creators.LOG_MAX_BYTES, creators.LOG_KEEP_LINES = real_max, real_keep

real_tail = creators.LOG_TAIL_BYTES
creators.LOG_TAIL_BYTES = 400
try:
    with log_path.open("w", encoding="utf-8") as f:
        for i in range(45):
            f.write(json.dumps({"t": i, "msg": f"m{i:02d}", "pad": "x" * 40}) + "\n")
    tail = creators.read_log(missing_id["id"], 5)
    check("a long log reads only its end", len(tail), 5)
    check("the newest lines of a long log are the last ones", tail[0]["msg"], "m44")
finally:
    creators.LOG_TAIL_BYTES = real_tail

directory_log = creators.root(long_name["id"]) / "log.jsonl"
directory_log.unlink(missing_ok=True)
directory_log.mkdir()
check("a log that turned into a folder is harmless", creators.read_log(long_name["id"]), [])
directory_log.rmdir()

# ---------------------------------------------------------------- the cache, and files changed from outside
check("the creator cache holds this user's workspaces", UID in creators._mem, True)
file = creators.root(one["id"]) / "creator.json"
outside = saved(one["id"])
outside["name"] = "Renamed Elsewhere"
file.write_text(json.dumps(outside), encoding="utf-8")
check("the page first answers from memory", creators.get(one["id"])["name"], "Hello, World!")
log_before = creators.read_log(one["id"])
version_before = ctx.version(UID)
creators.forget(UID)
check("forget drops the workspace cache", one["id"] not in creators._mem.get(UID, {}), True)
check(
    "forget drops this user's log cache",
    all(str(creators.root(x["id"]) / "log.jsonl") not in creators._log_cache for x in creators.all_creators()),
    True,
)
check("forget makes the page reload", ctx.version(UID), version_before + 1)
check("an outside edit is read after forget", creators.get(one["id"])["name"], "Renamed Elsewhere")
check("logs are read again after forget", creators.read_log(one["id"]), log_before)

with ctx.as_user("other_001"):
    other = creators.create({"name": "Someone Else"})
    check("another user's workspace is not in this list", one["id"] not in {c["id"] for c in creators.all_creators()}, True)
check("the first user cannot see the other workspace", creators.get(other["id"]), None)
creators._mem.pop("other_001", None)

shutil.rmtree(DATA, ignore_errors=True)
print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
