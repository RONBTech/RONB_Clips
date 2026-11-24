"""Deep tests for studio/profiles.py: channel parsing, caching, pictures, live state and refreshes.

The channel pages, image hosts and Twitch's GraphQL endpoint are all replaced with local fakes; nothing is
downloaded. Run with: python tests/test_profiles_deep.py
"""

import base64
import calendar
import json
import os
import shutil
import sys
import tempfile
import time
from concurrent.futures import Future
from pathlib import Path

DATA = tempfile.mkdtemp(prefix="pc-profiles-deep-")
os.environ["RONBCLIPS_DATA"] = DATA
os.environ.update(
    DATABASE_URL=os.environ.get("RONBCLIPS_TEST_DATABASE_URL", ""),
    RONBCLIPS_ACCOUNTS="0",
    RONBCLIPS_FETCH_MODEL="0",
    RONBCLIPS_UPDATE_CHECK="0",
    RONBCLIPS_PUBLIC_URL="http://127.0.0.1:8765",
)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from studio import creators, ctx, profiles  # noqa: E402

UID = "profile_001"
ctx.set_uid(UID)
fails = []


def check(name, got, expected):
    ok = expected(got) if callable(expected) else got == expected
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"   got {got!r}"))
    if not ok:
        fails.append(name)
    return ok


def expect_raise(name, fn, kind=Exception, part=""):
    try:
        fn()
    except Exception as e:  # noqa: BLE001 - the test names the kind it wants
        check(name, isinstance(e, kind) and (not part or part in str(e)), True)
        return e
    check(name, "no exception was raised", "an exception")
    return None


# ---------------------------------------------------------------- fakes for the web
PIC = b"\xff\xd8\xff" + b"a picture" * 20
state = {
    "twitch_graphql_fail": False,
    "twitch_no_user": False,
    "twitch_display": "StreamerName",
    "twitch_offline": False,
    "twitch_no_live_user": False,
    "kick_followers": "12345",
    "kick_live": True,
    "ydl_fail": False,
    "ydl_kind": "youtube",
}

YT_HTML = (
    '<meta property="og:title" content="Tester Channel">'
    '<meta property="og:image" content="https://yt3.googleusercontent.com/avatar.jpg">'
    '"content":"123K subscribers"'
)
YT_LIVE_HTML = (
    '<meta property="og:title" content="Live with Tester">'
    '<meta property="og:image" content="https://yt3.googleusercontent.com/avatar.jpg">'
    '"isLiveNow":true'
    '<link rel="canonical" href="https://www.youtube.com/watch?v=abcdefghijk">'
)
YT_OFFLINE_HTML = '<meta property="og:title" content="Offline Channel">'
YT_BADHOST_HTML = '<meta property="og:title" content="Bad Picture"><meta property="og:image" content="http://evil.example/pic.jpg">'
YT_BIGPIC_HTML = '<meta property="og:title" content="Big Picture"><meta property="og:image" content="https://yt3.ggpht.com/big.jpg">'
YT_WRONGTYPE_HTML = '<meta property="og:title" content="Wrong Type"><meta property="og:image" content="https://yt3.ggpht.com/text.jpg">'
TW_FALLBACK_HTML = '<meta property="og:title" content="Fallback - Live on Twitch">'
KICK_JSON = {
    "user": {"username": "kicker", "profile_pic": "https://files.kick.com/pic.png"},
    "followers_count": "12345",
    "livestream": {"start_time": "2026-01-02 03:04:05", "viewer_count": 77, "session_title": "Kicking it"},
}

WEB = [
    ("youtube.com/@tester/live", YT_LIVE_HTML, "text/html"),
    ("youtube.com/@offline/live", YT_OFFLINE_HTML, "text/html"),
    ("youtube.com/@empty", "<html>no tags here</html>", "text/html"),
    ("youtube.com/@badhost", YT_BADHOST_HTML, "text/html"),
    ("youtube.com/@bigpic", YT_BIGPIC_HTML, "text/html"),
    ("youtube.com/@wrongtype", YT_WRONGTYPE_HTML, "text/html"),
    ("youtube.com/@tester", YT_HTML, "text/html"),
    ("twitch.tv/fallback", TW_FALLBACK_HTML, "text/html"),
    ("twitch.tv/other", TW_FALLBACK_HTML, "text/html"),
    ("kick.com/api/v2/channels/kicker", None, "application/json"),
    ("yt3.googleusercontent.com/avatar.jpg", PIC, "image/jpeg"),
    ("yt3.ggpht.com/big.jpg", b"x" * (profiles.MAX_PIC + 10), "image/png"),
    ("yt3.ggpht.com/text.jpg", b"not an image", "text/html"),
    ("static-cdn.jtvnw.net/pic.jpg", b"\x89PNGtwitch", "image/png"),
    ("files.kick.com/pic.png", b"\x89PNGkick", "image/png"),
]
web_calls = []


class WebReply:
    def __init__(self, body, ctype):
        self.body = body.encode("utf-8") if isinstance(body, str) else body
        self.headers = {"Content-Type": ctype}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, limit=-1):
        return self.body if limit < 0 else self.body[:limit]


def fake_urlopen(req, timeout=None):
    url = req.full_url
    web_calls.append(url)
    for fragment, body, ctype in WEB:
        if fragment in url:
            if body is None:  # the Kick JSON endpoint changes with the test state
                data = dict(KICK_JSON)
                data["followers_count"] = state["kick_followers"]
                if not state["kick_live"]:
                    data["livestream"] = None
                body = json.dumps(data)
            return WebReply(body, ctype)
    raise AssertionError("Unexpected page request: " + url)


class Reply:
    def __init__(self, data):
        self._data = data
        self.text = json.dumps(data)

    def json(self):
        return self._data

    def raise_for_status(self):
        return None


graphql_calls = []


def fake_twitch_post(url, json=None, headers=None, timeout=None):
    graphql_calls.append((url, json, headers))
    query = (json or {}).get("query", "")
    login = (json or {}).get("variables", {}).get("l")
    if state["twitch_graphql_fail"]:
        raise RuntimeError("Twitch is down")
    if "displayName" in query:
        if state["twitch_no_user"]:
            return Reply({"data": {"user": None}})
        return Reply(
            {
                "data": {
                    "user": {
                        "login": login,
                        "displayName": state["twitch_display"],
                        "profileImageURL": "https://static-cdn.jtvnw.net/pic.jpg",
                        "followers": {"totalCount": 9876},
                    }
                }
            }
        )
    if state["twitch_no_live_user"]:
        return Reply({"data": {"user": None}})
    return Reply(
        {
            "data": {
                "user": {
                    "login": login,
                    "stream": None
                    if state["twitch_offline"]
                    else {
                        "title": "Live now",
                        "viewersCount": 321,
                        "createdAt": "2026-01-02T03:04:05Z",
                        "type": "live",
                    },
                }
            }
        }
    )


ydl_calls = []


def fake_ydl(url):
    ydl_calls.append(url)
    if state["ydl_fail"]:
        raise RuntimeError("no stream")
    if state["ydl_kind"] == "twitch":
        return {
            "is_live": True,
            "title": "switcher",
            "uploader_id": "switcher",
            "description": "The real title\nsecond line",
            "concurrent_view_count": 7,
            "timestamp": 123,
        }
    return {
        "concurrent_view_count": 55,
        "title": "Stream (live) 2026-01-02 03:04",
        "release_timestamp": 1700,
        "id": "abcdefghijk",
    }


real_urlopen = profiles.urllib.request.urlopen
real_httpx_post = httpx.post
real_ydl = profiles._ydl_info
profiles.urllib.request.urlopen = fake_urlopen
httpx.post = fake_twitch_post
profiles._ydl_info = fake_ydl

# ---------------------------------------------------------------- small helpers
check(
    "YouTube links are recognised",
    [profiles.platform_of(x) for x in ("youtube.com/x", "https://youtu.be/abc", "Twitch.tv/x", "kick.com/x", "example.com")],
    ["YouTube", "YouTube", "Twitch", "Kick", ""],
)
check(
    "short counts are readable",
    [profiles._short(n) for n in (999, 1500, 12345, 2_500_000, 1_200_000_000)],
    ["999", "1.5K", "12.3K", "2.5M", "1.2B"],
)
check(
    "share tags are parsed and unescaped",
    (profiles._meta(YT_HTML, "og:title"), profiles._meta('<meta name="a" content="R&amp;B">', "a"), profiles._meta(YT_HTML, "missing")),
    ("Tester Channel", "R&B", ""),
)
check(
    "yt-dlp's date suffix is removed from titles",
    (profiles._no_date("Stream (live) 2026-01-02 03:04"), profiles._no_date("Plain 2026-01-02 03:04"), profiles._no_date("")),
    ("Stream", "Plain", ""),
)

# ---------------------------------------------------------------- YouTube profiles and caching
url = "youtube.com/@tester/videos"
before = len(web_calls)
p = profiles.fetch(url)
check("a /videos link is read as a channel", p["platform"], "YouTube")
check("the display name and followers come from the page", (p["name"], p["followers"]), ("Tester Channel", "123K subscribers"))
check("the picture is downloaded from YouTube's own host", (p["pic"][0], p["pic"][1]), (PIC, "image/jpeg"))
check("the page and picture were actually fetched", len(web_calls) - before, 2)
cached = len(web_calls)
check("a second lookup uses the cache", profiles.fetch("https://youtube.com/@tester") is p, True)
check("no page is fetched again inside the cache window", len(web_calls), cached)
preview = profiles.preview("https://youtube.com/@tester")
check(
    "the browser gets an inline picture",
    (preview["platform"], preview["name"], preview["pic"]),
    ("YouTube", "Tester Channel", "data:image/jpeg;base64," + base64.b64encode(PIC).decode()),
)
check("the cache lives under the normalised link", "https://youtube.com/@tester" in profiles._cache, True)

profiles._cache["https://youtube.com/@tester"] = (time.time() - 4000, p)
cached = len(web_calls)
profiles.fetch("https://youtube.com/@tester")
check("an old cache entry is refreshed", len(web_calls) > cached, True)

expect_raise(
    "a page with no name is refused", lambda: profiles.fetch("https://youtube.com/@empty"), ValueError, "Couldn't find that channel"
)
count = len(web_calls)
expect_raise(
    "a failed lookup is remembered", lambda: profiles.fetch("https://youtube.com/@empty"), ValueError, "Couldn't find that channel"
)
check("a recent failure is not asked about again", len(web_calls), count)
profiles._cache["https://youtube.com/@empty"] = (time.time() - 100, None)
expect_raise(
    "an expired failure is tried again", lambda: profiles.fetch("https://youtube.com/@empty"), ValueError, "Couldn't find that channel"
)
check("the retry really asked the page", len(web_calls), count + 1)

check("a picture from another host is ignored", profiles.fetch("https://youtube.com/@badhost")["pic"], None)
check("a too-large picture is ignored", profiles.fetch("https://youtube.com/@bigpic")["pic"], None)
check("a picture with the wrong content type is ignored", profiles.fetch("https://youtube.com/@wrongtype")["pic"], None)
check("a profile without a picture still looks up fine", profiles.preview("https://youtube.com/@badhost")["pic"], "")

# ---------------------------------------------------------------- Kick
kick = profiles.fetch("https://kick.com/kicker")
check(
    "a Kick profile comes from its JSON endpoint",
    (kick["platform"], kick["name"], kick["followers"]),
    ("Kick", "kicker", "12.3K followers"),
)
check("the Kick picture is accepted", kick["pic"][1], "image/png")
state["kick_followers"] = "lots"
profiles._cache.pop("https://kick.com/kicker", None)
check("text followers are left blank", profiles.fetch("https://kick.com/kicker?x=1")["followers"], "")
state["kick_followers"] = "12345"
profiles._cache.pop("https://kick.com/kicker", None)
expect_raise("a short Kick name is refused", lambda: profiles.fetch("https://kick.com/x"), ValueError, "doesn't look like a Kick channel")

# ---------------------------------------------------------------- Twitch profiles
tw = profiles.fetch("https://twitch.tv/streamer")
check(
    "a Twitch profile comes from the web API", (tw["platform"], tw["name"], tw["followers"]), ("Twitch", "StreamerName", "9.9K followers")
)
check("the Twitch picture is accepted", tw["pic"][1], "image/png")
check("the GraphQL query was made with the public client id", graphql_calls[-1][2]["Client-ID"], profiles._TWITCH_WEB)

state["twitch_graphql_fail"] = True
fallback = profiles.fetch("https://twitch.tv/fallback")
check("a failed Twitch API call falls back to the page", (fallback["name"], fallback["followers"]), ("Fallback", ""))
state["twitch_graphql_fail"] = False
state["twitch_no_user"] = True
check("an unknown Twitch user also falls back", profiles.fetch("https://twitch.tv/other")["platform"], "Twitch")
state["twitch_no_user"] = False

expect_raise("other websites are refused", lambda: profiles.fetch("https://example.com/someone"), ValueError, "YouTube, Twitch or Kick")

# ---------------------------------------------------------------- live state: Kick
live = profiles.live_now("Kick", "https://kick.com/kicker")
expected_start = calendar.timegm(time.strptime("2026-01-02 03:04:05", "%Y-%m-%d %H:%M:%S"))
check(
    "Kick live details are read from the channel API",
    (live["live"], live["viewers"], live["title"], live["started"], live["embed"]),
    (True, 77, "Kicking it", expected_start, "kicker"),
)
state["kick_live"] = False
profiles._cache.clear()
check("a Kick channel with no stream is offline", profiles.live_now("Kick", "https://kick.com/kicker")["live"], False)
state["kick_live"] = True

# ---------------------------------------------------------------- live state: YouTube
live = profiles.live_now("YouTube", "https://www.youtube.com/@tester/live")
check(
    "a YouTube stream is detected from the page",
    (live["live"], live["viewers"], live["title"], live["started"], live["embed"]),
    (True, 55, "Stream", 1700, "abcdefghijk"),
)
state["ydl_fail"] = True
live = profiles.live_now("YouTube", "https://www.youtube.com/@tester/live")
check(
    "without yt-dlp the page still says who is live",
    (live["viewers"], live["title"], live["embed"]),
    (None, "Live with Tester", "abcdefghijk"),
)
state["ydl_fail"] = False
check("an offline YouTube page says so", profiles.live_now("YouTube", "https://www.youtube.com/@offline/live")["live"], False)

# ---------------------------------------------------------------- live state: Twitch
live = profiles.live_now("Twitch", "https://twitch.tv/streamer")
twitch_start = calendar.timegm(time.strptime("2026-01-02T03:04:05Z", "%Y-%m-%dT%H:%M:%SZ"))
check(
    "Twitch live details come from GraphQL",
    (live["live"], live["viewers"], live["title"], live["started"], live["embed"]),
    (True, 321, "Live now", twitch_start, "streamer"),
)
state["twitch_offline"] = True
check("a Twitch channel with no stream is offline", profiles.live_now("Twitch", "https://twitch.tv/streamer")["live"], False)
state["twitch_offline"] = False
state["twitch_no_live_user"] = True
check("an unknown Twitch user is offline", profiles.live_now("Twitch", "https://twitch.tv/streamer")["live"], False)
state["twitch_no_live_user"] = False
state["twitch_graphql_fail"] = True
state["ydl_kind"] = "twitch"
live = profiles.live_now("Twitch", "https://twitch.tv/switcher")
check(
    "yt-dlp is a fallback for Twitch live state",
    (live["live"], live["title"], live["viewers"], live["embed"]),
    (True, "The real title", 7, "switcher"),
)
state["ydl_fail"] = True
check("a failed fallback is simply offline", profiles.live_now("Twitch", "https://twitch.tv/switcher")["live"], False)
state["ydl_fail"] = False
state["ydl_kind"] = "youtube"
state["twitch_graphql_fail"] = False
check("an unknown platform is offline", profiles.live_now("Kick2", "https://example.com/x")["live"], False)

# ---------------------------------------------------------------- shared live answers
real_live_now = profiles.live_now
shared_calls = []
profiles.live_now = lambda platform, url: (
    shared_calls.append((platform, url)) or {"live": True, "viewers": 5, "title": "Shared", "started": None, "embed": "x"}
)
key = "https://shared.example/channel"
first = profiles.live_shared("Twitch", key)
second = profiles.live_shared("Twitch", key)
check("one answer is shared by everyone watching", (first, second, len(shared_calls)), (first, first, 1))
check("callers get their own copy", second is not first, True)
profiles.live_now = lambda platform, url: (_ for _ in ()).throw(RuntimeError("platform down"))
check("a hiccup keeps the last live answer", profiles.live_shared("Twitch", key), first)
profiles._live.pop(key.lower(), None)
check("a hiccup on a new channel gives an offline answer", profiles.live_shared("Twitch", "https://new.example/channel")["live"], False)
profiles.live_now = real_live_now
for k in (key.lower(), "https://new.example/channel"):
    profiles._live.pop(k, None)

# ---------------------------------------------------------------- a creator's profiles on disk
profiles._cache.clear()
state["twitch_display"] = "Streamer - Live on Twitch"
creator = creators.create(
    {
        "name": "Streamer - Live on Twitch",
        "channels": {"youtube": "https://youtube.com/@tester", "twitch": "https://twitch.tv/streamer"},
        "channel": "https://youtube.com/@tester",
    }
)
cid = creator["id"]
shared = {
    "https://youtube.com/@tester": {"live": True, "viewers": 10, "title": "On air", "started": 1, "embed": "vid"},
    "https://twitch.tv/streamer": {"live": False, "viewers": None, "title": "", "started": None, "embed": ""},
}
profiles.live_shared = lambda platform, url: shared.get(url, {"live": False, "viewers": None, "title": "", "started": None, "embed": ""})
profiles.refresh(cid, force=True)
saved = None
for _ in range(200):
    c = creators.get(cid) or {}
    if len(c.get("profiles") or {}) == 2:
        saved = c
        break
    time.sleep(0.02)
check("a forced refresh fills in both channels", saved is not None, True)
check("each channel keeps its own profile", sorted((saved or {}).get("profiles", {})), ["twitch", "youtube"])
check("live state is merged into each profile", (saved["profiles"]["youtube"]["viewers"], saved["profiles"]["twitch"]["live"]), (10, False))
check("the creator's name loses the live suffix", saved["name"], "Streamer")
check("the recording channel's profile is the creator's picture", saved["profile"]["platform"], "YouTube")
check("avatars are written under the creator", (creators.root(cid) / "avatar-youtube.img").read_bytes() == PIC, True)
check("the Twitch avatar is written too", (creators.root(cid) / "avatar-twitch.img").is_file(), True)
check("a fresh profile needs no refresh", profiles.needs_refresh(saved), False)
check(
    "a stale profile needs a refresh",
    profiles.needs_refresh({**saved, "profiles": {k: {**v, "at": 0} for k, v in saved["profiles"].items()}}),
    True,
)
check(
    "a changed channel needs a refresh",
    profiles.needs_refresh({**saved, "channels": {**saved["channels"], "youtube": "https://youtube.com/@other"}}),
    True,
)
check("a creator with no profiles needs a refresh", profiles.needs_refresh({}), False)
check(
    "cleaning only removes the live suffix",
    (profiles._clean_name({"name": "Someone - Live on Twitch"}), profiles._clean_name({"name": "Someone - Live on Kick"})),
    ({"name": "Someone"}, {}),
)

# ---------------------------------------------------------------- _profiles' own error handling
old = {"youtube": {"channel": "https://youtube.com/@tester", "name": "Old", "at": time.time()}}
ok_future = Future()
ok_future.set_result({"live": True, "viewers": 3, "title": "Now", "started": None, "embed": "v"})
bad_future = Future()
bad_future.set_exception(RuntimeError("live lookup failed"))
real_fetch = profiles.fetch
profiles.fetch = lambda url: (_ for _ in ()).throw(RuntimeError("channel gone")) if "twitch.tv" in url else real_fetch(url)
rows = profiles._profiles(cid, creators.get(cid), old, {"youtube": ok_future, "twitch": bad_future}, True, time.time())
profiles.fetch = real_fetch
check(
    "a failed profile keeps its platform and link",
    (rows["twitch"]["platform"], rows["twitch"]["channel"]),
    ("Twitch", "https://twitch.tv/streamer"),
)
check("a failed live lookup does not spoil the profile", rows["twitch"].get("viewers", None), None)
check("a good channel still gets its live answer", (rows["youtube"]["name"], rows["youtube"]["viewers"]), ("Tester Channel", 3))
check("every row says when it was checked", all("live_at" in r for r in rows.values()), True)

# ---------------------------------------------------------------- refresh's busy and throttle rules
submitted = []
real_submit = profiles._pool.submit
profiles._pool.submit = lambda fn: submitted.append(fn)
key = (UID, cid)
profiles._busy.add(key)
profiles.refresh(cid, force=True)
check("a lookup already running is not started twice", (len(submitted), key in profiles._again), (0, True))
profiles._busy.discard(key)
profiles._tried[key] = time.time()
profiles.refresh(cid)
check("a recent check is not repeated", len(submitted), 0)
profiles.refresh(cid, force=True)
check("a forced check is always submitted", len(submitted), 1)
profiles._pool.submit = real_submit
profiles._busy.discard(key)
profiles._again.discard(key)
profiles._tried.pop(key, None)

profiles.urllib.request.urlopen = real_urlopen
httpx.post = real_httpx_post
profiles._ydl_info = real_ydl
shutil.rmtree(DATA, ignore_errors=True)
print("ALL PASSED" if not fails else f"{len(fails)} failed")
sys.exit(1 if fails else 0)
