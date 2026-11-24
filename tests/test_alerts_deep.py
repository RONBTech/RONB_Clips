"""Deeper, direct checks of studio/alerts.py: webhook payloads, truncation, masking and failures.

httpx, the notification bell, the clock and Windows' sleep-prevention call are all replaced with fakes; no
message leaves this machine and no database is used.
"""

import contextlib
import os
import sys
import tempfile
import types
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(tempfile.mkdtemp(prefix="ronbclips-alerts-deep-"))
os.environ["RONBCLIPS_DATA"] = str(DATA)
os.environ.update(DATABASE_URL=os.environ.get("RONBCLIPS_TEST_DATABASE_URL", ""), RONBCLIPS_ACCOUNTS="0", RONBCLIPS_UPDATE_CHECK="0")
sys.path.insert(0, str(ROOT))

from studio import alerts, auth, ctx, notices  # noqa: E402

fails = []


def check(name, got, expected):
    ok = got == expected
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  got {got!r}, expected {expected!r}"))
    if not ok:
        fails.append(name)


@contextlib.contextmanager
def patch(obj, name, value):
    old = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, old)


EMPTY = dict(alerts.accounts.SETTING_DEFAULTS)
DISCORD = {**EMPTY, "discord_webhook": "https://discord.com/api/webhooks/123/secret"}
TELEGRAM = {**EMPTY, "telegram_token": "123456:SECRET-TOKEN", "telegram_chat": "-10042"}


class FakeResponse:
    def __init__(self, code=200):
        self.status_code = code


def fake_post(result, calls, error=None):
    def post(url, **kw):
        calls.append((url, kw))
        if error:
            raise error
        return FakeResponse(result)

    return post


# ---------------------------------------------------------------- configured()
with patch(alerts.accounts, "settings", lambda: EMPTY):
    check("nothing configured means no messages", alerts.configured(), False)
with patch(alerts.accounts, "settings", lambda: DISCORD):
    check("a discord webhook is enough", alerts.configured(), True)
with patch(alerts.accounts, "settings", lambda: {**EMPTY, "telegram_token": TELEGRAM["telegram_token"]}):
    check("a token without a chat is not enough", alerts.configured(), False)
with patch(alerts.accounts, "settings", lambda: TELEGRAM):
    check("a token and a chat are enough", alerts.configured(), True)


# ---------------------------------------------------------------- send()
with patch(alerts.accounts, "settings", lambda: EMPTY), patch(alerts.httpx, "post", fake_post(200, [])):
    check("send does nothing when no channel is set up", alerts.send("hello"), [])

calls = []
with patch(alerts.accounts, "settings", lambda: DISCORD), patch(alerts.httpx, "post", fake_post(200, calls)):
    check("discord accepts a message", alerts.send("hello"), [])
url, kw = calls[0]
check("discord gets the webhook address", url, DISCORD["discord_webhook"])
check("discord gets the text and no pings", kw["json"], {"content": "hello", "allowed_mentions": {"parse": []}})
check("webhook calls have a short timeout", kw["timeout"], 15)

calls = []
with patch(alerts.accounts, "settings", lambda: DISCORD), patch(alerts.httpx, "post", fake_post(200, calls)):
    alerts.send("x" * 2500)
check("discord text is cut to its limit", len(calls[0][1]["json"]["content"]), 1900)

calls = []
with patch(alerts.accounts, "settings", lambda: DISCORD), patch(alerts.httpx, "post", fake_post(429, calls)):
    check("a refused discord message is reported", alerts.send("hello"), ["Discord said 429"])

calls = []
with (
    patch(alerts.accounts, "settings", lambda: DISCORD),
    patch(alerts.httpx, "post", fake_post(200, calls, OSError("could not reach " + "x" * 200))),
):
    got = alerts.send("hello")
check("a discord network error is reported", got[0].startswith("Discord: "), True)
check("a long discord error is shortened", len(got[0]), len("Discord: ") + 80)

calls = []
with patch(alerts.accounts, "settings", lambda: TELEGRAM), patch(alerts.httpx, "post", fake_post(200, calls)):
    check("telegram accepts a message", alerts.send("hello"), [])
url, kw = calls[0]
check("telegram gets a bot URL with the token", url, "https://api.telegram.org/bot123456:SECRET-TOKEN/sendMessage")
check("telegram gets the chat and text", kw["json"], {"chat_id": "-10042", "text": "hello"})
check("telegram calls have a short timeout", kw["timeout"], 15)

calls = []
with patch(alerts.accounts, "settings", lambda: TELEGRAM), patch(alerts.httpx, "post", fake_post(200, calls)):
    alerts.send("y" * 5000)
check("telegram text is cut to its limit", len(calls[0][1]["json"]["text"]), 3900)

calls = []
with patch(alerts.accounts, "settings", lambda: TELEGRAM), patch(alerts.httpx, "post", fake_post(503, calls)):
    check("a refused telegram message is reported", alerts.send("hello"), ["Telegram said 503"])

calls = []
with patch(alerts.accounts, "settings", lambda: TELEGRAM), patch(alerts.httpx, "post", fake_post(200, calls, OSError("refused"))):
    got = alerts.send("hello")
check("a telegram network error is reported", got, ["Telegram: could not connect"])
check("a telegram error never echoes the bot token", "SECRET-TOKEN" in " ".join(got), False)

calls = []
BOTH = {
    **EMPTY,
    "discord_webhook": DISCORD["discord_webhook"],
    "telegram_token": TELEGRAM["telegram_token"],
    "telegram_chat": TELEGRAM["telegram_chat"],
}
with patch(alerts.accounts, "settings", lambda: BOTH), patch(alerts.httpx, "post", fake_post(200, calls)):
    check("both channels can be used", alerts.send("hello"), [])
check("send posts to both channels", len(calls), 2)

calls = []


def mixed_post(url, **kw):
    calls.append((url, kw))
    return FakeResponse(400 if "discord" in url else 200)


with patch(alerts.accounts, "settings", lambda: BOTH), patch(alerts.httpx, "post", mixed_post):
    check("one channel's failure does not stop the other", alerts.send("hello"), ["Discord said 400"])
check("the good channel is still called", len(calls), 2)


# ---------------------------------------------------------------- notify()
class FakeThread:
    made = []

    def __init__(self, target, args=(), daemon=False):
        self.target, self.args, self.daemon = target, args, daemon
        FakeThread.made.append(self)

    def start(self):
        self.started = True
        self.target(*self.args)


notice_calls = []
FakeThread.made = []
with ctx.as_user(auth.LOCAL_ID):
    with (
        patch(notices, "add", lambda *a: notice_calls.append(a)),
        patch(alerts, "configured", lambda: False),
        patch(alerts.threading, "Thread", FakeThread),
    ):
        alerts.notify("hello world", kind="clips", link="/videos")
        check("notify capitalises the bell message", notice_calls[0][2], "Hello world")
        check("notify keeps the kind and link", (notice_calls[0][1], notice_calls[0][4]), ("clips", "/videos"))
        check("notify starts no thread when nothing is configured", FakeThread.made, [])
        check("the bell message has no body", notice_calls[0][3], "")

    sent = []
    FakeThread.made = []
    with (
        patch(notices, "add", lambda *a: notice_calls.append(a)),
        patch(alerts, "configured", lambda: True),
        patch(alerts, "send", lambda text: sent.append(text)),
        patch(alerts.threading, "Thread", FakeThread),
    ):
        alerts.notify("long " * 100)
        check("notify starts one background thread", len(FakeThread.made), 1)
        check("the background thread is a daemon", FakeThread.made[0].daemon, True)
        check("the message sent to the phone is prefixed", sent, ["RONB Clips: " + "long " * 100])
        check("the bell message is capped", len(notice_calls[-1][2]), 160)

    notice_calls.clear()
    with (
        patch(notices, "add", lambda *a: (_ for _ in ()).throw(RuntimeError("no database"))),
        patch(alerts, "configured", lambda: False),
    ):
        check("a broken bell cannot stop notify", alerts.notify("still works"), None)

sent = []
with (
    patch(alerts, "configured", lambda: True),
    patch(alerts, "send", lambda text: sent.append(text)),
    patch(alerts.threading, "Thread", FakeThread),
):
    check("notify works without a signed-in user", alerts.notify("no user"), None)
check("a message is still sent without a signed-in user", sent, ["RONB Clips: no user"])


# ---------------------------------------------------------------- digest_loop()
class StopLoop(Exception):
    pass


def run_digest(settings_list, summary="Today: 3 clips", configured=True, iterations=1):
    sent, saved, made = [], [], []
    n = [0]

    def sleep(_):
        n[0] += 1
        if n[0] > iterations:
            raise StopLoop

    fake_time = SimpleNamespace(sleep=sleep, strftime=lambda fmt: "2026-11-23", localtime=lambda: SimpleNamespace(tm_hour=8))
    with (
        patch(alerts, "time", fake_time),
        patch(alerts.auth, "user_ids", lambda: [auth.LOCAL_ID]),
        patch(alerts.accounts, "settings", lambda: settings_list[min(n[0] - 1, len(settings_list) - 1)]),
        patch(alerts.accounts, "save_settings", lambda patch_: saved.append(patch_)),
        patch(alerts, "configured", lambda: configured),
        patch(alerts, "send", lambda text: sent.append(text)),
    ):
        try:

            def make_summary():
                made.append(1)
                return summary() if callable(summary) else summary

            alerts.digest_loop(make_summary)
        except StopLoop:
            pass
    return sent, saved, len(made)


sent, saved, made = run_digest([{**TELEGRAM, "digest_hour": 9, "last_digest": ""}], iterations=1)
check("the digest waits for the configured hour", (sent, saved, made), ([], [], 0))
sent, saved, made = run_digest([TELEGRAM], iterations=1)
check("a digest sends the summary", sent, ["Today: 3 clips"])
check("a digest is recorded as sent", saved, [{"last_digest": "2026-11-23"}])
check("a digest builds one summary", made, 1)

sent, saved, made = run_digest([{**TELEGRAM, "last_digest": "2026-11-23"}], iterations=1)
check("a digest is sent only once a day", (sent, saved, made), ([], [], 0))
sent, saved, made = run_digest([TELEGRAM], configured=False, iterations=1)
check("no digest goes out when nothing is configured", (sent, saved), ([], []))


def broken_summary():
    raise RuntimeError("AI is down")


sent, saved, made = run_digest([TELEGRAM], summary=broken_summary, iterations=1)
check("a broken summary is swallowed", (sent, saved), ([], [{"last_digest": "2026-11-23"}]))


# ---------------------------------------------------------------- keep_awake_loop()
with patch(alerts, "os", SimpleNamespace(name="posix")):
    check("keep-awake does nothing away from Windows", alerts.keep_awake_loop(lambda: True), None)

calls = []
state = [True]
n = [0]


def sleep(_):
    n[0] += 1
    if n[0] == 1:
        state[0] = False
    elif n[0] > 1:
        raise StopLoop


fake_ctypes = types.ModuleType("ctypes_fake")
fake_ctypes.windll = SimpleNamespace(kernel32=SimpleNamespace(SetThreadExecutionState=lambda flags: calls.append(flags)))
old_ctypes = sys.modules.get("ctypes")
sys.modules["ctypes"] = fake_ctypes
fake_time = SimpleNamespace(sleep=sleep, strftime=lambda fmt: "", localtime=lambda: None)
old_time = alerts.time
alerts.time = fake_time
try:
    try:
        alerts.keep_awake_loop(lambda: state[0])
    except StopLoop:
        pass
finally:
    alerts.time = old_time
    if old_ctypes is None:
        sys.modules.pop("ctypes", None)
    else:
        sys.modules["ctypes"] = old_ctypes
check("keep-awake asks Windows to stay awake while busy", calls, [0x80000001, 0x80000000])
check("keep-awake releases Windows once work stops", calls[-1], 0x80000000)

print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
