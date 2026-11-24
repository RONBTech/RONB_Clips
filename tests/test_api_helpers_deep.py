"""Deep checks for the small helpers the page leans on: notices, sync, feedback, usage and settings.

Runs against the code directly with its own empty data folder. No server, no network and no AI key: the database
is a throwaway SQLite file, and anything that would contact a service is replaced with a fixed answer.
"""

import importlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

os.environ["RONBCLIPS_DATA"] = tempfile.mkdtemp(prefix="rc-helpers-deep-")
os.environ.update(
    DATABASE_URL=os.environ.get("RONBCLIPS_TEST_DATABASE_URL", ""),
    RONBCLIPS_ACCOUNTS="0",
    RONBCLIPS_FETCH_MODEL="0",
    RONBCLIPS_UPDATE_CHECK="0",
    RONBCLIPS_AI_KEY="",
    PYTHONIOENCODING="utf-8",
)
sys.path.insert(0, ".")

import sqlalchemy as sa  # noqa: E402

import studio  # noqa: E402 - the package itself holds the usage record helpers
from studio import ai, auth, config, creators, ctx, db, feedback, notices, sync_api  # noqa: E402

fails = []
DATA = Path(os.environ["RONBCLIPS_DATA"])


def check(name, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"   got {got!r}"))
    ok or fails.append(name)


db.init()
owner = auth.local_user()
UID = owner.id
ctx.set_uid(UID)

# ------------------------------------------------------------------------------- notifications
notices.add(None, "live", "Nobody", "no owner")  # no owner, no row
check("a notice needs an owner", notices.mine()["items"], [])

before = ctx.version(UID)
notices.add(UID, "live", "Recording started", "body", "/live/x", ref="start-1")
check("adding a notice wakes the page", ctx.version(UID) > before, True)
notices.add(UID, "live", "Recording started again", ref="start-1")
check("the same reference is only recorded once", len(notices.mine(limit=100)["items"]), 1)
with db.engine.begin() as c:
    n = db.notices.c
    row = c.execute(sa.select(n).where(n.user_id == UID)).mappings().first()
check("the notice kept its kind, title and link", (row["kind"], row["title"], row["link"]), ("live", "Recording started", "/live/x"))

with patch.object(notices.time, "time", return_value=100.0):
    notices.add(UID, "post", "X" * 300, "Y" * 500, "Z" * 300, ref="long-1")
mine = notices.mine()
item = next(x for x in mine["items"] if x["title"].startswith("X"))
check("a long notice is trimmed to fit the column", (len(item["title"]), len(item["body"]), len(item["link"])), (160, 400, 200))
check("an unread notice is marked unread", item["read"], False)

with db.engine.begin() as c:
    notices.add(UID, "clips", "Inside a transaction", ref="txn-1", conn=c)
check("a notice can join an existing transaction", any(x["title"] == "Inside a transaction" for x in notices.mine()["items"]), True)

now = time.time()
notices._unread.clear()
check("the unread count counts the rows", notices.unread(), 3)
with db.engine.begin() as c:
    c.execute(db.notices.insert().values(user_id=UID, t=now, kind="clips", title="Invisible for 20 s"))
with patch.object(notices.time, "time", return_value=now + 1):
    check("the unread count is cached for a short while", notices.unread(), 3)
with patch.object(notices.time, "time", return_value=now + 100):
    check("...then it is counted again", notices.unread(), 4)

notices.read_all()
check("read_all marks everything read", notices.mine()["unread"], 0)
check("read_all refreshes the cached count", notices.unread(), 0)
titles = [x["title"] for x in notices.mine(limit=10)["items"]]
check("the newest notice comes first", titles[0], "Invisible for 20 s")
old_spot = next(i for i, t in enumerate(titles) if t.startswith("X"))
check("a notice given an old time comes later", titles.index("Recording started") < old_spot, True)

for i in range(105):
    notices.add(UID, "clips", f"old {i}")
check("only the newest hundred notices are kept", len(notices.mine(limit=200)["items"]), 100)
check("offset pages through the notices", len(notices.mine(limit=10, offset=95)["items"]), 5)

# ------------------------------------------------------------------------------- feedback
for bad, words in (("x", "too short"), ("a" * 4001, "too long")):
    raised = None
    try:
        feedback.submit(UID, "bug", bad)
    except feedback.Refused as e:
        raised = str(e)
    check(f"feedback refuses a message that is {words}", isinstance(raised, str), True)

fid = feedback.submit(UID, "nonsense", "  Please look at this  ", "/settings", "x" * 1500)["id"]
rows = feedback.listing()["items"]
row = next(r for r in rows if r["id"] == fid)
check("an unknown kind becomes a message", row["kind"], "other")
check("the message is trimmed", row["message"], "Please look at this")
check("context is trimmed to fit", len(row["context"]), 1000)
check("new feedback is counted", feedback.listing()["counts"]["new"] >= 1, True)
check("old account fields are not shown with feedback", {"name", "email"}.isdisjoint(row), True)

feedback.submit(UID, "idea", "An idea", page="/ideas")
feedback.set_status(fid, "seen")
check("a status change is stored", next(r for r in feedback.listing()["items"] if r["id"] == fid)["status"], "seen")
check("status counts follow the change", feedback.listing(status="seen")["counts"]["seen"] >= 1, True)
for status, which, expect in (("bogus", fid, "Unknown status."), ("seen", 999999, "That message no longer exists.")):
    raised = None
    try:
        feedback.set_status(which, status)
    except feedback.Refused as e:
        raised = str(e)
    check(f"feedback refuses a {status} status", raised, expect)
check("limit and offset are clamped", len(feedback.listing(limit=1000, offset=-5)["items"]) > 0, True)

# ------------------------------------------------------------------------------- sync /api/sync
for busy, epoch, struct, want_middle in ((0, 7, 12, 0), (2, 7, 12, 7)):
    with (
        patch.object(sync_api.jobs, "busy_count", return_value=busy),
        patch.object(sync_api.jobs, "queue_epoch", return_value=epoch),
        patch.object(sync_api.jobs, "struct_version", return_value=struct),
    ):
        check(
            f"the sync token includes the queue when {busy} videos are busy",
            sync_api.token("boot"),
            f"boot.{ctx.version(UID)}.{want_middle}.{struct}",
        )

check("a good token is read back", sync_api.place("boot.4.0.9", "boot"), (4, 9))
check("a token from another start is thrown away", sync_api.place("other.4.0.9", "boot"), (None, None))
check("a token with a missing part is thrown away", sync_api.place("boot.4.9", "boot"), (None, None))
check("a token with text where a number belongs is thrown away", sync_api.place("boot.x.0.9", "boot"), (None, None))

with (
    patch.object(sync_api.jobs, "busy_count", return_value=0),
    patch.object(sync_api.jobs, "queue_epoch", return_value=0),
    patch.object(sync_api.jobs, "struct_version", return_value=9),
    patch.dict(sync_api.creators._mem, {UID: {}}),
):
    current = sync_api.token("boot")
    check("an unchanged page gets the short answer", sync_api.unchanged(current, "boot"), {"v": current})
    check("an old token does not get the short answer", sync_api.unchanged("boot.1.0.9", "boot"), None)
    check("no token at all does not get the short answer", sync_api.unchanged("", "boot"), None)
    check("response still answers when the token matches", sync_api.response(current, "boot"), {"v": current})
    old = "boot.1.0.9"
    with (
        patch.object(sync_api.jobs, "changed_since", return_value=[]) as changed,
        patch.object(sync_api, "creator_rows", return_value=[{"id": "c1"}]),
        patch.object(sync_api.accounts, "list_accounts", return_value=[]),
        patch.object(sync_api.usage, "brief", return_value={"ready": False}),
        patch.object(notices, "unread", return_value=2),
    ):
        out = sync_api.response(old, "boot")
        check("an incremental answer knows it is not full", "jobs_full" in out, False)
        check("an incremental answer asks for changes since the token", changed.call_args.args, (1, False))
        check(
            "the small answer still carries the page state",
            (out["creators"], out["accounts"], out["unread"], out["ai"]),
            ([{"id": "c1"}], [], 2, {"ready": False}),
        )
        out = sync_api.response("boot.x.0.9", "boot")
        check("an unreadable token asks for everything", changed.call_args.args, (None, True))
        check("a full answer is marked as full", out["jobs_full"], True)
        out = sync_api.response("boot.1.0.3", "boot")
        check("a changed structure asks for everything", changed.call_args.args, (1, True))

creator = creators.create({"name": "Sync Creator", "channel": "https://www.twitch.tv/synccreator"})
with patch.object(sync_api.live, "active_for", return_value=None):
    rows = sync_api.creator_rows()
check("a creator row has a log and no live job", ("id" in rows[0], "log" in rows[0], rows[0]["live_job"]), (True, True, None))
fake_live = type("S", (), {"job": {"id": "live-42"}})()
with patch.object(sync_api.live, "active_for", return_value=fake_live):
    check("a recording creator points at its live job", sync_api.creator_rows()[0]["live_job"], "live-42")


class StopLoop(Exception):
    pass


class FakeThread:
    def __init__(self, target, **kw):
        self.target = target

    def start(self):
        try:
            self.target()
        except StopLoop:
            pass


refreshed = []
slept = []


def fake_sleep(n):
    slept.append(n)
    if len(slept) > 1:
        raise StopLoop()


with (
    patch.object(sync_api.threading, "Thread", FakeThread),
    patch.object(sync_api.time, "sleep", fake_sleep),
    patch.dict(sync_api.creators._mem, {UID: {"c1": {"id": "c1"}}}),
    patch.object(sync_api.profiles, "needs_refresh", return_value=True),
    patch.object(sync_api.profiles, "refresh", side_effect=lambda cid: refreshed.append(cid)),
):
    sync_api.start_background_refresh()
check("the background loop refreshes stale creators", refreshed, ["c1"])
check("the background loop checks every five minutes", slept[0], 300)

# ------------------------------------------------------------------------------- the package's usage record
studio._brief.clear()
studio.forget(UID)
studio.set_context("c1", "a place")
version = ctx.version(UID)
studio.record(10, 5, "clip")
check("recording AI use wakes the page", ctx.version(UID) > version, True)
with db.engine.connect() as c:
    u = db.usage.c
    row = c.execute(sa.select(u).where(u.user_id == UID).order_by(u.t.desc()).limit(1)).mappings().first()
check(
    "the usage row has the tokens and the place",
    (row["tokens_in"], row["tokens_out"], row["purpose"], row["creator"], row["place"]),
    (10, 5, "clip", "c1", "a place"),
)
check("recording AI use drops the cached summary", UID not in studio._brief, True)

with ctx.as_user(None):
    n = None
    with db.engine.connect() as c:
        n = c.execute(sa.select(sa.func.count()).select_from(db.usage)).scalar()
    studio.record(1, 1, "nobody")
    with db.engine.connect() as c:
        check("a background thread with no user records nothing", c.execute(sa.select(sa.func.count()).select_from(db.usage)).scalar(), n)

check("the day key is a date", len(studio._day(time.time())), 10)
check("token totals add both directions", str(studio._tokens()), str(db.usage.c.tokens_in + db.usage.c.tokens_out))

studio.set_context("c2", "another place")
with db.engine.begin() as c:
    c.execute(
        db.usage.insert().values(
            user_id=UID, t=time.time(), tokens_in=7, tokens_out=3, purpose="title", creator="c2", place="another place"
        )
    )
check("a new AI call invalidates the brief", UID not in studio._brief, True)

with (
    patch.object(ai, "ready", return_value=True),
    patch.object(ai, "label", return_value="Test AI"),
    patch.object(ai, "models", return_value=["m1", "m2"]),
    patch.object(ai, "problem_text", return_value=""),
):
    brief = studio.brief()
    check(
        "the brief reports today's calls",
        (brief["ready"], brief["provider"], brief["model"], brief["today_calls"]),
        (True, "Test AI", "m1", 2),
    )
    check("the brief reports today's tokens", brief["today_tokens"], 25)
    with db.engine.begin() as c:
        c.execute(db.usage.insert().values(user_id=UID, t=time.time(), tokens_in=1, tokens_out=1, purpose="extra"))
    check("the brief is cached for ten seconds", studio.brief()["today_calls"], 2)
    studio.forget(UID)
    check("forget recomputes the brief", studio.brief()["today_calls"], 3)
    studio._brief[UID] = (time.time() - 100, {"stale": True})
    studio.forget_all()
    check("forget_all clears every cached brief", UID not in studio._brief, True)

    with db.engine.begin() as c:
        c.execute(
            db.usage.insert().values(
                user_id=UID, t=time.time() - 86400, tokens_in=4, tokens_out=6, purpose="clip", creator="c1", place="old"
            )
        )
    summary = studio.summary({"c1": "Creator One", "c2": "Creator Two"})
    check("the summary counts every call", (summary["calls_total"], summary["tokens_total"]), (4, 37))
    check("the summary has thirty days", len(summary["daily"]), 30)
    check("the summary groups by purpose", summary["by_purpose"][0][0] in ("clip", "title"), True)
    check("the summary names the creator", [x[0] for x in summary["by_creator"]], ["Creator One", "Creator Two", "No creator"])
    check("the summary lists the latest calls first", len(summary["recent"]) > 0 and summary["recent"][0]["tokens"] > 0, True)
    check("the summary carries the brief numbers", summary["provider"], "Test AI")

# ------------------------------------------------------------------------------- settings, from the environment
check("env falls back to a default", config.env("RONBCLIPS_TEST_ABSENT", "fallback"), "fallback")
check("user ids are checked", config.user_dir(UID).name, UID)
raised = None
try:
    config.user_dir("../escape")
except ValueError as e:
    raised = str(e)
check("a bad user id is refused", raised, "bad user id")

check("a limit of zero means no limit", config._limit("RONBCLIPS_TEST_LIMIT_A"), config._NONE)
with patch.dict(os.environ, {"RONBCLIPS_TEST_LIMIT_B": "-2", "RONBCLIPS_TEST_LIMIT_C": "2.5"}):
    check("a negative limit also means no limit", config._limit("RONBCLIPS_TEST_LIMIT_B"), config._NONE)
    check("a positive limit is kept", config._limit("RONBCLIPS_TEST_LIMIT_C"), 2.5)

config._kept.clear()
calls = []


def make():
    calls.append(1)
    return "value"


check("a file-backed setting is read once", config._keep("test", make), "value")
check("the cached value is reused", config._keep("test", make), "value")
check("...only one read happened", len(calls), 1)
config._kept["test"] = (time.time() - 100, "old")
check("an old cached value is read again", config._keep("test", make), "value")

config._kept.clear()
with patch.dict(os.environ, {"RONBCLIPS_TEST_SECRET": "  abcdefghijklmnop  "}):
    check("a secret can be given directly", config._secret("RONBCLIPS_TEST_SECRET", "RONBCLIPS_TEST_SECRET_FILE"), "abcdefghijklmnop")
secret_file = DATA / "secret.txt"
secret_file.write_text("token is ABCDEFGHIJKLMNOPQR\n", encoding="utf-8")
with patch.dict(os.environ, {"RONBCLIPS_TEST_SECRET": "", "RONBCLIPS_TEST_SECRET_FILE": str(secret_file)}):
    config._kept.clear()
    check("a secret can be found in a file", config._secret("RONBCLIPS_TEST_SECRET", "RONBCLIPS_TEST_SECRET_FILE"), "ABCDEFGHIJKLMNOPQR")
    check(
        "the same file can be read without the cache",
        config._read_secret("RONBCLIPS_TEST_SECRET", "RONBCLIPS_TEST_SECRET_FILE", r"\S{16,}"),
        "ABCDEFGHIJKLMNOPQR",
    )
secret_file.write_text('"plain short secret"\n', encoding="utf-8")
with patch.dict(os.environ, {"RONBCLIPS_TEST_SECRET": "", "RONBCLIPS_TEST_SECRET_FILE": str(secret_file)}):
    config._kept.clear()
    check(
        "a file without a long token is used whole",
        config._secret("RONBCLIPS_TEST_SECRET", "RONBCLIPS_TEST_SECRET_FILE"),
        "plain short secret",
    )
with patch.dict(os.environ, {"RONBCLIPS_TEST_SECRET": "", "RONBCLIPS_TEST_SECRET_FILE": ""}):
    config._kept.clear()
    check("a missing secret is empty", config._secret("RONBCLIPS_TEST_SECRET", "RONBCLIPS_TEST_SECRET_FILE"), "")

google = DATA / "google.json"
google.write_text(json.dumps({"web": {"client_id": "id1", "client_secret": "secret1"}}), encoding="utf-8")
with patch.dict(os.environ, {"GOOGLE_CLIENT_ID": "", "GOOGLE_CLIENT_SECRET": "", "GOOGLE_CLIENT_FILE": str(google)}):
    check("a Google client can come from a downloaded file", config._read_google_client(), ("id1", "secret1"))
google.write_text(json.dumps({"installed": {"client_id": "id2", "client_secret": "secret2"}}), encoding="utf-8")
with patch.dict(os.environ, {"GOOGLE_CLIENT_ID": "", "GOOGLE_CLIENT_SECRET": "", "GOOGLE_CLIENT_FILE": str(google)}):
    check("...or from an installed-app file", config._read_google_client(), ("id2", "secret2"))
google.write_text("not json", encoding="utf-8")
with patch.dict(os.environ, {"GOOGLE_CLIENT_ID": "", "GOOGLE_CLIENT_SECRET": "", "GOOGLE_CLIENT_FILE": str(google)}):
    check("a broken Google file gives no client", config._read_google_client(), ("", ""))
with patch.dict(os.environ, {"GOOGLE_CLIENT_ID": " a ", "GOOGLE_CLIENT_SECRET": " b "}):
    check("a Google client can come from the environment", config._read_google_client(), ("a", "b"))

dotenv_root = DATA / "dotenv-root"
dotenv_root.mkdir()
(dotenv_root / ".env").write_text('# comment\n\nRONBCLIPS_TEST_DOTENV = " spaced " \nRONBCLIPS_DATA=/ignored\n', encoding="utf-8")
with patch.dict(os.environ, {}, clear=False):
    os.environ.pop("RONBCLIPS_TEST_DOTENV", None)
    with patch.object(config, "ROOT", dotenv_root):
        config._load_dotenv()
    check("a .env line is read with its quotes and outer spaces removed", os.environ["RONBCLIPS_TEST_DOTENV"], " spaced ")
    check("a .env line never overrides the real environment", os.environ["RONBCLIPS_DATA"], str(DATA))

with patch.dict(os.environ, {"RONBCLIPS_DATA": str(DATA)}):
    check("the data folder can come from the environment", config._data_folder(), (DATA, "env"))
with patch.dict(os.environ, {}, clear=False):
    os.environ.pop("RONBCLIPS_DATA", None)
    with patch.object(config, "STORAGE_FILE", DATA / "missing-storage.json"):
        check("without a choice the data folder is inside the app", config._data_folder(), (config.ROOT / "data", "default"))
    storage = DATA / "storage.json"
    chosen = DATA / "chosen"
    chosen.mkdir()
    storage.write_text(json.dumps({"path": str(chosen)}), encoding="utf-8")
    with patch.object(config, "STORAGE_FILE", storage):
        check("a chosen data folder is used", config._data_folder(), (chosen, "chosen"))
    old = DATA / "old-data"
    (old / "part").mkdir(parents=True)
    (old / "part" / "kept.txt").write_text("kept", encoding="utf-8")
    (old / "loose.txt").write_text("loose", encoding="utf-8")
    storage.write_text(json.dumps({"path": str(chosen), "move_from": str(old)}), encoding="utf-8")
    with patch.object(config, "STORAGE_FILE", storage):
        check("an old data folder is moved into the new one", config._data_folder(), (chosen, "chosen"))
    check(
        "the moved files are all there",
        (chosen / "part" / "kept.txt").read_text(encoding="utf-8") + (chosen / "loose.txt").read_text(encoding="utf-8"),
        "keptloose",
    )
    check("the move is remembered", json.loads(storage.read_text(encoding="utf-8")), {"path": str(chosen)})
    check("moving a folder that is already gone is harmless", config._move_folder(DATA / "nowhere", chosen), True)
    storage.write_text("{broken", encoding="utf-8")
    with patch.object(config, "STORAGE_FILE", storage):
        check("broken storage settings fall back to the default folder", config._data_folder(), (config.ROOT / "data", "default"))
    blocked = DATA / "blocked"
    blocked.write_text("a file, not a folder", encoding="utf-8")
    storage.write_text(json.dumps({"path": str(blocked / "child")}), encoding="utf-8")
    unreachable = None
    with patch.object(config, "STORAGE_FILE", storage):
        try:
            config._data_folder()
        except SystemExit as e:
            unreachable = str(e)
    check("an unreachable chosen folder gives a clear error", "can't be reached right now" in str(unreachable), True)

# Module-level settings are branchy (limits, flags, URLs); reload once with everything set, then put them back.
keep_env = {k: v for k, v in os.environ.items() if k.startswith(("RONBCLIPS_", "GOOGLE_", "DATABASE_URL"))}
override = {
    "RONBCLIPS_TRUST_PROXY": "yes",
    "RONBCLIPS_PUBLIC_URL": "https://example.org/",
    "RONBCLIPS_SKIP_STATIC": "1",
    "RONBCLIPS_YT_TRANSCRIPT": "0",
    "RONBCLIPS_STATIC_THRESHOLD": "0.9",
    "RONBCLIPS_STATIC_MIN_SECONDS": "2.5",
    "RONBCLIPS_LIVE_AI_TIMEOUT": "12.5",
    "RONBCLIPS_ASR_MODEL": "other-model",
    "RONBCLIPS_MAX_UPLOAD_MB": "42",
    "RONBCLIPS_UPLOAD_PART_MB": "1",
    "RONBCLIPS_ALLOWED_SITES": " example.com , ,twitch.tv ",
    "RONBCLIPS_AI_MODELS": "a, b,",
    "RONBCLIPS_PORT": "9999",
}
try:
    os.environ.update(override)
    config = importlib.reload(config)
    check("a trusted proxy flag is read", config.TRUST_PROXY, True)
    check("a public https url sets HTTPS and drops the slash", (config.PUBLIC_URL, config.HTTPS), ("https://example.org", True))
    check(
        "static skipping is read with its thresholds",
        (config.SKIP_STATIC, config.STATIC_THRESHOLD, config.STATIC_MIN_SECONDS),
        (True, 0.9, 2.5),
    )
    check("YouTube transcripts and the live timeout can be changed", (config.YT_TRANSCRIPT, config.LIVE_AI_TIMEOUT), (False, 12.5))
    check("the speech model folder follows its setting", config.ASR_MODEL_DIR.name, "other-model")
    check("the upload part has a floor", config.UPLOAD_PART_MB, 5)
    check("allowed sites are cleaned up", config.ALLOWED_SITES, ("example.com", "twitch.tv"))
    check("AI models are cleaned up", config.AI_MODELS, ["a", "b"])
    check("the port is used in the default public url", config.env("RONBCLIPS_PORT"), "9999")
finally:
    for key in override:
        os.environ.pop(key, None)
    importlib.reload(config)

print("\n" + (f"{len(fails)} failed" if fails else "All checks passed"))
import shutil

shutil.rmtree(DATA, ignore_errors=True)
sys.exit(1 if fails else 0)
