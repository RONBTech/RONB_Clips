"""Deep tests for studio/accounts.py, run as one plain script with its own data folder.

Nothing is sent to YouTube, Instagram, TikTok or a webhook: every httpx call is replaced by a small
fake that answers with realistic JSON. Run with: python tests/test_accounts_deep.py
"""

import base64
import hashlib
import hmac
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

DATA = tempfile.mkdtemp(prefix="pc-accounts-deep-")
os.environ["RONBCLIPS_DATA"] = DATA
os.environ.update(
    DATABASE_URL=os.environ.get("RONBCLIPS_TEST_DATABASE_URL", ""),
    RONBCLIPS_ACCOUNTS="0",
    RONBCLIPS_FETCH_MODEL="0",
    RONBCLIPS_UPDATE_CHECK="0",
    RONBCLIPS_PUBLIC_URL="https://clips.example.com",
    GOOGLE_CLIENT_ID="",
    GOOGLE_CLIENT_SECRET="",
    GOOGLE_CLIENT_FILE="",
    RONBCLIPS_IG_APP_ID="",
    RONBCLIPS_IG_APP_SECRET="",
    RONBCLIPS_IG_APP_ID_FILE="",
    RONBCLIPS_IG_APP_SECRET_FILE="",
    RONBCLIPS_TIKTOK_CLIENT_KEY="",
    RONBCLIPS_TIKTOK_CLIENT_SECRET="",
    RONBCLIPS_TIKTOK_CLIENT_KEY_FILE="",
    RONBCLIPS_TIKTOK_CLIENT_SECRET_FILE="",
)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from studio import accounts, config, ctx, secretfile  # noqa: E402

UID = "deepuser_001"
YT_SECRET = "yt-app-secret-abcdef"
IG_SECRET = "ig-app-secret-ghijkl"
YT_REFRESH = "yt-refresh-token-secret"
YT_ACCESS = "yt-access-token-secret"
IG_TOKEN = "ig-long-token-secret"
TT_REFRESH = "tt-refresh-token-secret"

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
    except Exception as e:  # noqa: BLE001 - the test says which kind it wanted
        ok = isinstance(e, kind) and (not part or part in str(e))
        check(name, ok, True)
        return e if ok else None
    check(name, "no exception was raised", "an exception")
    return None


# ---------------------------------------------------------------- the fake network
class Reply:
    def __init__(self, status=200, data=None, text=None, headers=None):
        self.status_code = status
        self._data = {} if data is None else data
        self.text = text if text is not None else json.dumps(self._data)
        self.headers = headers or {}

    def json(self):
        if isinstance(self._data, Exception):
            raise self._data
        return self._data


state = {
    "yt_no_refresh": False,
    "yt_no_channel": False,
    "yt_refresh_fail": False,
    "ig_data_wrapper": True,
    "ig_no_permission": False,
    "ig_account_type": "BUSINESS",
    "ig_refresh_fail": False,
    "ig_status": "FINISHED",
    "ig_views": 42,
    "ig_views_status": 200,
    "ig_permalink_fail": False,
    "tt_no_scope": False,
    "tt_creator_fail": False,
    "tt_refresh_fail": False,
    "tt_init_unaudited": False,
    "tt_upload_fail": False,
    "tt_status": "PUBLISH_COMPLETE",
    "yt_upload_fail": False,
}
http_calls = []
handlers = []


def handle(text, fn):
    handlers.append((text, fn))


def google_token(method, url, kw):
    form = kw.get("data") or {}
    if form.get("grant_type") == "refresh_token":
        if state["yt_refresh_fail"]:
            return Reply(401, {"error": "invalid_grant"})
        return Reply(200, {"access_token": "yt-new-access", "expires_in": 3600})
    if state["yt_no_refresh"]:
        return Reply(200, {"access_token": "yt-access-no-refresh"})
    return Reply(200, {"refresh_token": YT_REFRESH, "access_token": YT_ACCESS, "expires_in": 3600})


def google_channels(method, url, kw):
    if state["yt_no_channel"]:
        return Reply(200, {"items": []})
    return Reply(
        200,
        {"items": [{"id": "UCchannel123", "snippet": {"title": "A Real Channel"}}]},
    )


def google_videos(method, url, kw):
    ids = (kw.get("params") or {}).get("id", "").split(",")
    return Reply(200, {"items": [{"id": i, "statistics": {"viewCount": str(100 + n)}} for n, i in enumerate(ids)]})


def instagram_token(method, url, kw):
    if state["ig_no_permission"]:
        return Reply(200, {"access_token": "short", "permissions": "instagram_business_basic"})
    short = {"access_token": "ig-short-token", "permissions": "instagram_business_content_publish"}
    return Reply(200, {"data": [short]} if state["ig_data_wrapper"] else short)


def instagram_me(method, url, kw):
    return Reply(200, {"user_id": 17841, "username": "clipper", "account_type": state["ig_account_type"]})


def instagram_media(method, url, kw):
    if method == "POST":
        return Reply(200, {"id": "igmedia1"})
    fields = (kw.get("params") or {}).get("fields")
    if fields == "status_code,status":
        if state["ig_status"] == "FINISHED":
            return Reply(200, {"status_code": "FINISHED", "status": "all good"})
        if state["ig_status"] == "ERROR":
            return Reply(200, {"status_code": "ERROR", "status": "bad file"})
        return Reply(200, {"status_code": "IN_PROGRESS", "status": "wait"})
    if fields == "permalink":
        if state["ig_permalink_fail"]:
            return Reply(500, {"error": "no link"})
        return Reply(200, {"permalink": "https://www.instagram.com/reel/abc/"})
    return Reply(200, {})


def instagram_insights(method, url, kw):
    if state["ig_views_status"] != 200:
        return Reply(state["ig_views_status"], {"error": "no"})
    if state["ig_views"] is None:
        return Reply(200, {"data": []})
    return Reply(200, {"data": [{"values": [{"value": state["ig_views"]}]}]})


def tiktok_token(method, url, kw):
    form = kw.get("data") or {}
    if form.get("grant_type") == "refresh_token":
        if state["tt_refresh_fail"]:
            return Reply(401, {"error": {"code": "invalid_grant", "message": "expired"}})
        return Reply(200, {"access_token": "tt-access-new", "refresh_token": "tt-refresh-new", "expires_in": 7200})
    scope = "user.info.basic" if state["tt_no_scope"] else "user.info.basic,video.publish"
    return Reply(
        200,
        {
            "access_token": "tt-access-1",
            "refresh_token": TT_REFRESH,
            "open_id": "open123",
            "scope": scope,
            "expires_in": 7200,
        },
    )


def tiktok_creator(method, url, kw):
    if state["tt_creator_fail"]:
        return Reply(200, {"error": {"code": "other", "message": "not now"}})
    return Reply(
        200,
        {
            "data": {
                "creator_username": "tiktoker",
                "creator_nickname": "The TikToker",
                "privacy_level_options": ["PUBLIC_TO_EVERYONE", "SELF_ONLY"],
            }
        },
    )


tt_levels = []


def tiktok_init(method, url, kw):
    if state["tt_init_unaudited"]:
        state["tt_init_unaudited"] = False
        return Reply(200, {"error": {"code": "unaudited", "message": "App not reviewed"}})
    body = kw.get("json") or {}
    tt_levels.append(body["post_info"]["privacy_level"])
    return Reply(200, {"data": {"publish_id": "publish123", "upload_url": "https://upload.example/tiktok"}})


def tiktok_status(method, url, kw):
    if state["tt_status"] == "FAILED":
        return Reply(200, {"data": {"status": "FAILED", "fail_reason": "bad audio"}})
    if state["tt_status"] == "PROCESSING":
        return Reply(200, {"data": {"status": "PROCESSING"}})
    return Reply(200, {"data": {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": ["111"]}})


handle("oauth2.googleapis.com/token", google_token)
handle("youtube/v3/channels", google_channels)
handle("upload/youtube/v3/videos", lambda m, u, k: Reply(200, {"id": "ytvid1"}, headers={"Location": "https://upload.example/youtube"}))
handle("upload.example/youtube", lambda m, u, k: Reply(200, {"id": "ytvid1"}))
handle("youtube/v3/videos", google_videos)
handle("api.instagram.com/oauth/access_token", instagram_token)
handle("graph.instagram.com/access_token", lambda m, u, k: Reply(200, {"access_token": IG_TOKEN}))
handle(
    "graph.instagram.com/refresh_access_token",
    lambda m, u, k: (Reply(403, {"error": "expired"}) if state["ig_refresh_fail"] else Reply(200, {"access_token": "ig-refreshed-token"})),
)
handle("graph.instagram.com/me", instagram_me)
handle("rupload.facebook.com", lambda m, u, k: Reply(200, {}))
handle("/media_publish", lambda m, u, k: Reply(200, {"id": "igpub1"}))
handle("/insights", instagram_insights)
handle("graph.instagram.com", instagram_media)
handle("/oauth/token/", tiktok_token)
handle("/creator_info/query/", tiktok_creator)
handle("/video/init/", tiktok_init)
handle("/status/fetch/", tiktok_status)
handle("upload.example/tiktok", lambda m, u, k: (Reply(500, {"error": "broken"}) if state["tt_upload_fail"] else Reply(206, {})))


def fake_http(method, url, **kw):
    http_calls.append((method.upper(), url, kw))
    for text, fn in handlers:
        if text in url:
            return fn(method.upper(), url, kw)
    raise AssertionError("Unexpected network call: " + method.upper() + " " + url)


_real_post, _real_get, _real_put = httpx.post, httpx.get, httpx.put
httpx.post = lambda url, **kw: fake_http("POST", url, **kw)
httpx.get = lambda url, **kw: fake_http("GET", url, **kw)
httpx.put = lambda url, **kw: fake_http("PUT", url, **kw)


def fake_client_request(self, method, url, **kw):
    return fake_http(method, url, **kw)


_real_client_request = httpx.Client.request
httpx.Client.request = fake_client_request

# Every user-level secret file is written through studio.secretfile.write_private.
private_writes = []
_real_write_private = secretfile.write_private


def recorded_write_private(path, data):
    private_writes.append((Path(path).name, bytes(data)))
    return _real_write_private(path, data)


secretfile.write_private = recorded_write_private


def wrote_private(name):
    return any(n == name for n, _ in private_writes)


# ---------------------------------------------------------------- settings
with ctx.as_user(UID):
    defaults = accounts.settings()
    check("new settings have the documented defaults", defaults, accounts.SETTING_DEFAULTS)
    check("settings() returns a copy", (defaults is accounts.settings(), defaults == accounts.settings()), (False, True))

    updated = accounts.save_settings(
        {
            "discord_webhook": "https://discord.com/api/webhooks/123/token-secret",
            "telegram_token": "telegram-bot-secret",
            "telegram_chat": "12345",
            "paused": "yes",
            "digest_hour": "99",
            "tour_done": 1,
            "not_a_setting": "ignored",
        }
    )
    check(
        "known settings are saved, unknown ones ignored",
        (updated["paused"], updated["digest_hour"], updated["tour_done"], "not_a_setting" in updated),
        (True, 23, True, False),
    )
    check(
        "a blank secret keeps the saved one",
        accounts.save_settings({"telegram_token": "", "discord_webhook": ""})["telegram_token"],
        "telegram-bot-secret",
    )
    expect_raise(
        "a bad Discord link is refused",
        lambda: accounts.save_settings({"discord_webhook": "http://evil.example/hook"}),
        ValueError,
        "Discord webhook",
    )
    check("a refused patch changes nothing", accounts.settings()["discord_webhook"], "https://discord.com/api/webhooks/123/token-secret")

    public = accounts.public_settings()
    blob = json.dumps(public)
    check("public settings mask both secrets as booleans", (public["discord_webhook"], public["telegram_token"]), (True, True))
    check("no secret value reaches the page", ("token-secret" in blob or "telegram-bot-secret" in blob), False)
    check("the other settings are unchanged", public["digest_hour"], 23)

    raw = json.loads((Path(DATA) / "users" / UID / "accounts" / "settings.json").read_text(encoding="utf-8"))
    check("settings are saved with the real secret values", (raw["telegram_token"], raw["paused"]), ("telegram-bot-secret", True))
    check("settings go through the private-file writer", wrote_private("settings.json"), True)

with ctx.as_user("other_002"):
    f = config.user_dir("other_002") / "accounts" / "settings.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("{not json", encoding="utf-8")
    accounts._settings_mem.pop("other_002", None)
    check("a broken settings file falls back to defaults", accounts.settings()["digest_hour"], 8)

# ---------------------------------------------------------------- the server's apps
with ctx.as_user(UID):
    check("no app is set up at first", accounts.app_keys("google"), ("", ""))
    check("the setup panel says so", accounts.apps_public()["youtube"]["ready"], False)
    expect_raise(
        "an unknown platform cannot be saved", lambda: accounts.save_app("myspace", "id", "secret"), ValueError, "Unknown platform"
    )
    expect_raise("saving nothing is refused", lambda: accounts.save_app("youtube", "", ""), ValueError, "Paste the")
    expect_raise("a new id needs a secret", lambda: accounts.save_app("youtube", "another-id", ""), ValueError, "Client secret")
    expect_raise(
        "a badly shaped id is refused", lambda: accounts.save_app("youtube", "bad id!", YT_SECRET), ValueError, "exactly as the platform"
    )
    expect_raise("an unknown platform has no Connect link", lambda: accounts.connect_url("myspace"), ValueError, "Unknown platform")

    accounts.save_app("youtube", "g-client-1234567890", YT_SECRET)
    accounts.save_app("instagram", "ig-app-123", IG_SECRET)
    check("apps.json is written privately", wrote_private("apps.json"), True)
    check("a dashboard app is used for requests", accounts.app_keys("google"), ("g-client-1234567890", YT_SECRET))
    check("every app is ready", [accounts.can_connect(p) for p in ("youtube", "instagram")], [True, True])
    apps = accounts.apps_public()
    blob = json.dumps(apps)
    check("saved app secrets never reach the panel", (YT_SECRET in blob or IG_SECRET in blob), False)
    check("the panel shows a short id hint", apps["youtube"]["id_hint"], "g-clie…")
    check(
        "the panel knows what to call the fields",
        (apps["youtube"]["id_label"], apps["instagram"]["secret_label"]),
        ("Client ID", "Instagram app secret"),
    )
    check("the panel knows the app came from the dashboard", apps["youtube"]["from_dashboard"], True)
    check("the redirect goes back to this server", apps["instagram"]["redirect"], "https://clips.example.com/oauth/instagram/callback")
    check("https is reported for a public address", apps["tiktok"]["https"], True)

    accounts.save_app("youtube", "g-client-1234567890", "")
    check("an empty secret keeps the saved one", accounts.app_keys("google")[1], YT_SECRET)
    accounts.save_app("youtube", "g-client-1234567890", YT_SECRET)  # keep later tests simple
    check("a short id is shown whole", accounts.apps_public()["instagram"]["id_hint"], "ig-app-123")

    key_file, secret_file = Path(DATA) / "tt-key.txt", Path(DATA) / "tt-secret.txt"
    key_file.write_text("tt-file-key-123\n", encoding="utf-8")
    secret_file.write_text("tt-file-secret-456\n", encoding="utf-8")
    os.environ.update(
        RONBCLIPS_TIKTOK_CLIENT_KEY="",
        RONBCLIPS_TIKTOK_CLIENT_SECRET="",
        RONBCLIPS_TIKTOK_CLIENT_KEY_FILE=str(key_file),
        RONBCLIPS_TIKTOK_CLIENT_SECRET_FILE=str(secret_file),
    )
    check("an app can come from files", accounts.app_keys("tiktok"), ("tt-file-key-123", "tt-file-secret-456"))
    os.environ["RONBCLIPS_TIKTOK_CLIENT_SECRET"] = "tt-env-secret-456"
    check("a direct secret wins over its file", accounts.app_keys("tiktok"), ("tt-file-key-123", "tt-env-secret-456"))
    check("that app is not marked as saved in the dashboard", accounts.apps_public()["tiktok"]["from_dashboard"], False)
    check("all three platforms can now connect", [accounts.can_connect(p) for p in ("youtube", "instagram", "tiktok")], [True, True, True])

# ---------------------------------------------------------------- one-time OAuth states
with ctx.as_user(UID):
    url = accounts.connect_url("youtube")
    query = parse_qs(urlparse(url).query)
    state_value = query["state"][0]
    check("Connect uses Google's own page", url.startswith("https://accounts.google.com/o/oauth2/v2/auth?"), True)
    check("the state belongs to this user and platform", accounts._states[state_value][::2], (UID, "youtube"))
    check("the scope asks to upload videos", "youtube.upload" in query["scope"][0], True)
    acc = accounts.connect_finish("youtube", "code-1", state_value)
    check("the YouTube account is saved", (acc["id"], acc["name"]), ("yt_UCchannel123", "A Real Channel"))
    check(
        "the state is used only once",
        accounts.connect_finish("youtube", "code-1", state_value) if state_value in accounts._states else "gone",
        "gone",
    )

    other = parse_qs(urlparse(accounts.connect_url("instagram")).query)["state"][0]
    expect_raise(
        "a state cannot be used for another platform", lambda: accounts.connect_finish("tiktok", "c", other), ValueError, "expired"
    )
    check("a callback attempt uses up its state, even a wrong one", other in accounts._states, False)
    other = parse_qs(urlparse(accounts.connect_url("youtube")).query)["state"][0]
    with ctx.as_user("other_002"):
        expect_raise(
            "a state cannot be used by another user", lambda: accounts.connect_finish("youtube", "c", other), ValueError, "expired"
        )
    check("a callback from another user also uses up the state", other in accounts._states, False)
    accounts._states["empty"] = (UID, time.time(), "youtube")
    expect_raise(
        "a missing sign-in code is refused",
        lambda: accounts.connect_finish("youtube", "", "empty"),
        ValueError,
        "did not send a sign-in code",
    )

    saved_states = dict(accounts._states)
    accounts._states.clear()
    now = time.time()
    for i in range(500):
        accounts._states[f"fresh{i}"] = (UID, now, "youtube")
    accounts._states["ancient"] = (UID, now - 1000, "youtube")
    fresh = accounts._new_state("tiktok")
    check(
        "old states are thrown away and the cap holds",
        (len(accounts._states), "ancient" in accounts._states, fresh in accounts._states),
        (500, False, True),
    )
    accounts._states.clear()
    accounts._states.update(saved_states)

# ---------------------------------------------------------------- YouTube
with ctx.as_user(UID):
    check("connecting YouTube needs the app set up", accounts.youtube_ready(), True)
    acc = accounts._youtube_finish("code-2")
    check("the refresh token is stored privately", accounts.get(acc["id"])["refresh_token"], YT_REFRESH)
    check("the account list has no token fields", [k for k in accounts.list_accounts()[0] if "token" in k], [])
    check(
        "the tokens never appear in the account list",
        (YT_REFRESH in json.dumps(accounts.list_accounts()) or YT_ACCESS in json.dumps(accounts.list_accounts())),
        False,
    )
    check("the account file exists and is private-writer made", wrote_private("accounts.json"), True)

    calls = len(http_calls)
    check("a live token is reused", accounts._yt_token(dict(acc)), YT_ACCESS)
    check("reusing a token makes no request", len(http_calls), calls)
    stale = dict(acc, expires=0)
    check("an expired token is refreshed", accounts._yt_token(stale), "yt-new-access")
    check("a refresh is written back to disk", accounts.get(acc["id"])["access_token"], "yt-new-access")
    check("a refresh does not drop the refresh token", accounts.get(acc["id"])["refresh_token"], YT_REFRESH)

    state["yt_refresh_fail"] = True
    expect_raise("a 401 refresh is reported", lambda: accounts._yt_token(dict(acc, expires=0)), RuntimeError, "(401)")
    state["yt_refresh_fail"] = False
    expect_raise(
        "an account with no refresh token fails", lambda: accounts._yt_token({"expires": 0, "access_token": "x"}), KeyError, "refresh_token"
    )

    state["yt_no_refresh"] = True
    expect_raise(
        "a token answer with no refresh token is refused", lambda: accounts._youtube_finish("code"), ValueError, "long-lived sign-in"
    )
    state["yt_no_refresh"] = False
    state["yt_no_channel"] = True
    expect_raise(
        "a Google account without a channel is refused", lambda: accounts._youtube_finish("code"), ValueError, "no YouTube channel"
    )
    state["yt_no_channel"] = False

    clip = Path(DATA) / "youtube-clip.mp4"
    clip.write_bytes(b"\x00\x01video bytes" * 50)
    tags = [f"tag{i}" for i in range(20)]
    rid, link = accounts.youtube_upload(acc, clip, "<A <title>>", "<b>desc & more</b>", tags, "unlisted")
    check("the YouTube upload returns a short link", (rid, link), ("ytvid1", "https://www.youtube.com/shorts/ytvid1"))
    start = next(c for c in http_calls if "upload/youtube" in c[1])
    check(
        "the upload starts as resumable with the right privacy",
        (start[2]["params"]["uploadType"], start[2]["json"]["status"]["privacyStatus"]),
        ("resumable", "unlisted"),
    )
    meta = start[2]["json"]["snippet"]
    check("angle brackets are stripped from the title", ("<" in meta["title"] or ">" in meta["title"]), False)
    check("tags are capped at 15", len(meta["tags"]), 15)
    check(
        "the video bytes are sent to the returned address",
        any(c[1] == "https://upload.example/youtube" and c[2]["content"] == clip.read_bytes() for c in http_calls),
        True,
    )

    ids = [f"vid{i:03d}" for i in range(55)]
    views = accounts.youtube_views(acc, ids)
    check(
        "views are read in batches of 50",
        (len(views), views["vid054"], sum(1 for c in http_calls if "youtube/v3/videos" in c[1] and c[0] == "GET")),
        (55, 104, 2),
    )

# ---------------------------------------------------------------- Instagram
with ctx.as_user(UID):
    url = accounts.connect_url("instagram")
    state_value = parse_qs(urlparse(url).query)["state"][0]
    acc = accounts.connect_finish("instagram", "ig-code#_", state_value)
    check("Instagram's extra code suffix is removed", (acc["id"], acc["name"], acc["detail"]), ("ig_17841", "@clipper", "Business account"))
    check("the Instagram token is stored", accounts.get(acc["id"])["token"], IG_TOKEN)

    state["ig_no_permission"] = True
    expect_raise("publishing permission is required", lambda: accounts._instagram_finish("code"), ValueError, "permission to publish")
    state["ig_no_permission"] = False
    state["ig_data_wrapper"] = False
    check("a plain short-token answer also works", accounts._instagram_finish("code")["token"], IG_TOKEN)
    state["ig_data_wrapper"] = True
    state["ig_account_type"] = "MEDIA_CREATOR"
    check("a creator account is labelled", accounts.instagram_add(" pasted-token ")["detail"], "Creator account")
    state["ig_account_type"] = "PERSONAL"
    check("an unknown account type has no label", accounts.instagram_add("t")["detail"], "")
    state["ig_account_type"] = "BUSINESS"

    old = accounts.instagram_add("old-token")
    old["token_time"] = time.time() - 8 * 86400
    check("a week-old Instagram token is refreshed", accounts._ig_token(old), "ig-refreshed-token")
    check("the refreshed token is stored", accounts.get(old["id"])["token"], "ig-refreshed-token")
    state["ig_refresh_fail"] = True
    older = dict(old, token_time=0)
    check("a failed Instagram refresh keeps the old token", accounts._ig_token(older), "ig-refreshed-token")
    state["ig_refresh_fail"] = False
    expect_raise("an account with no token fails", lambda: accounts._ig_token({}), KeyError, "token")

    clip = Path(DATA) / "ig-clip.mp4"
    clip.write_bytes(b"\x00\x01reel" * 100)
    pid, link = accounts.instagram_upload(acc, clip, "A reel caption", "job1234567")
    check("the Instagram reel is published", (pid, link), ("igpub1", "https://www.instagram.com/reel/abc/"))
    media_call = next(c for c in http_calls if c[1].endswith("/media") and c[0] == "POST")
    check("a public server sends a signed clip link", "/pub/" in media_call[2]["data"]["video_url"], True)
    check("Instagram is asked for a Reel", media_call[2]["data"]["media_type"], "REELS")

    public_url = config.PUBLIC_URL
    config.PUBLIC_URL = "http://127.0.0.1:8765"
    try:
        accounts.instagram_upload(acc, clip, "local caption", "")
    finally:
        config.PUBLIC_URL = public_url
    upload_call = next(c for c in http_calls if "rupload.facebook.com" in c[1])
    check(
        "a local server sends the file itself",
        (upload_call[2]["headers"]["Authorization"], upload_call[2]["headers"]["offset"], upload_call[2]["content"]),
        ("OAuth " + IG_TOKEN, "0", clip.read_bytes()),
    )
    check(
        "a local upload does not send a video link",
        "video_url"
        not in next(c for c in http_calls if c[1].endswith("/media") and c[0] == "POST" and "video_url" not in c[2]["data"])[2].get(
            "data", {}
        ),
        True,
    )

    state["ig_status"] = "ERROR"
    expect_raise(
        "an Instagram error is reported",
        lambda: accounts.instagram_upload(acc, clip, "cap", "job1234567"),
        RuntimeError,
        "rejected the video",
    )
    state["ig_status"] = "IN_PROGRESS"
    real_sleep = time.sleep
    time.sleep = lambda _: None
    try:
        expect_raise(
            "Instagram cannot wait for ever", lambda: accounts.instagram_upload(acc, clip, "cap", "job1234567"), RuntimeError, "too long"
        )
    finally:
        time.sleep = real_sleep
    state["ig_status"] = "FINISHED"
    state["ig_permalink_fail"] = True
    check("a missing permalink is not fatal", accounts.instagram_upload(acc, clip, "cap", "job1234567")[1], "")
    state["ig_permalink_fail"] = False

    check("Instagram views are read", accounts.instagram_views(acc, "igmedia1"), 42)
    state["ig_views"] = None
    check("no insights data means no count", accounts.instagram_views(acc, "igmedia1"), None)
    state["ig_views_status"] = 403
    check("a refused insights call means no count", accounts.instagram_views(acc, "igmedia1"), None)
    state["ig_views_status"] = 200
    state["ig_views"] = 42

# ---------------------------------------------------------------- TikTok
with ctx.as_user(UID):
    acc = accounts._tiktok_finish("code-tt")
    check("the TikTok account name comes from the creator info", (acc["id"], acc["name"]), ("tt_open123", "@tiktoker"))
    check("the TikTok tokens are stored", accounts.get(acc["id"])["refresh_token"], TT_REFRESH)

    state["tt_no_scope"] = True
    expect_raise("posting permission is required", lambda: accounts._tiktok_finish("code"), ValueError, "permission to post videos")
    state["tt_no_scope"] = False
    state["tt_creator_fail"] = True
    check("a failed creator lookup keeps a plain name", accounts._tiktok_finish("code")["name"], "TikTok account")
    state["tt_creator_fail"] = False

    stale = dict(acc, expires=0)
    check("an expired TikTok token is refreshed", accounts._tt_token(stale), "tt-access-new")
    check("the rotated refresh token is stored", accounts.get(acc["id"])["refresh_token"], "tt-refresh-new")
    state["tt_refresh_fail"] = True
    expect_raise(
        "a 401 TikTok refresh is an API error", lambda: accounts._tt_token(dict(acc, expires=0)), accounts.APIError, "invalid_grant"
    )
    state["tt_refresh_fail"] = False
    expect_raise("an account with no refresh token fails", lambda: accounts._tt_token({"expires": 0}), KeyError, "refresh_token")

    check("small files go in one piece", accounts._tt_chunks(1000), (1000, 1))
    check("big files are split into 10 MB pieces", accounts._tt_chunks(65_000_000), (10_000_000, 6))

    clip = Path(DATA) / "tt-clip.mp4"
    clip.write_bytes(b"\x00\x01tiktok" * 60)
    rid, link = accounts.tiktok_upload(acc, clip, "A TikTok caption", "public")
    check("the TikTok post is published", (rid, link), ("111", "https://www.tiktok.com/@tiktoker/video/111"))
    check("the requested privacy was used", tt_levels[-1], "PUBLIC_TO_EVERYONE")
    puts = [c for c in http_calls if c[1] == "https://upload.example/tiktok"]
    check("the file is sent with a byte range", len(puts) == 1 and puts[0][2]["headers"]["Content-Range"].startswith("bytes 0-"), True)

    state["tt_init_unaudited"] = True
    fresh = accounts._tiktok_finish("code")
    check("an unreviewed app falls back to a private post", accounts.tiktok_upload(fresh, clip, "cap", "public")[0], "111")
    check("the retry asks for a private post", tt_levels[-1], "SELF_ONLY")
    check(
        "the account says why posts are private",
        accounts.get(fresh["id"])["detail"],
        "Posts are private until this server's TikTok app is reviewed",
    )

    state["tt_upload_fail"] = True
    expect_raise("a refused TikTok upload is reported", lambda: accounts.tiktok_upload(acc, clip, "cap"), RuntimeError, "(500)")
    state["tt_upload_fail"] = False
    state["tt_status"] = "FAILED"
    expect_raise("a failed TikTok publish is reported", lambda: accounts.tiktok_upload(acc, clip, "cap"), RuntimeError, "bad audio")
    state["tt_status"] = "PROCESSING"
    real_sleep = time.sleep
    time.sleep = lambda _: None
    try:
        expect_raise("TikTok cannot wait for ever", lambda: accounts.tiktok_upload(acc, clip, "cap"), RuntimeError, "too long")
    finally:
        time.sleep = real_sleep
    state["tt_status"] = "PUBLISH_COMPLETE"

    err = APIError_stub = expect_raise(
        "a bad TikTok error object is understood",
        lambda: accounts._tt(Reply(400, {"error": {"code": "bad", "message": "nope"}}), "Test"),
        accounts.APIError,
        "bad",
    )
    check("the code is kept on the error", getattr(err, "code", ""), "bad")
    expect_raise("a string error is understood", lambda: accounts._tt(Reply(200, {"error": "oops"}), "Test"), accounts.APIError, "oops")
    check("an ok error object is not a failure", accounts._tt(Reply(200, {"error": {"code": "ok"}}), "Test"), {"error": {"code": "ok"}})
    check("non-JSON answers can still fail cleanly", accounts._tt(Reply(200, ValueError("not json"), text="not json"), "Test"), {})

# ---------------------------------------------------------------- signed clip links
with ctx.as_user(UID):
    link = accounts.clip_link("job1234567", "clip01.mp4")
    token = urlparse(link).path[len("/pub/") : -len(".mp4")]
    check(
        "the link is signed and points at this server",
        (link.startswith("https://clips.example.com/pub/"), link.endswith(".mp4")),
        (True, True),
    )
    check("the signature reads back", accounts.read_clip_link(token), (UID, "job1234567", "clip01.mp4"))
    check(
        "the signing key is reused, not recreated",
        (accounts._link_key() == accounts._link_key(), sum(1 for n, _ in private_writes if n == "link.key")),
        (True, 1),
    )
    check("the key file was written privately", wrote_private("link.key"), True)

    b = token.split(".")[0]
    sig = token.split(".")[1]
    check("a changed signature is refused", accounts.read_clip_link(b + "." + ("A" if sig[0] != "A" else "B")), None)
    check("a made-up token is refused", accounts.read_clip_link("abc.def"), None)
    check("an empty token is refused", accounts.read_clip_link(""), None)

    def signed(payload):
        body = json.dumps(payload, separators=(",", ":")).encode()
        data = base64.urlsafe_b64encode(body).decode().rstrip("=")
        mac = base64.urlsafe_b64encode(hmac.new(accounts._link_key(), data.encode(), hashlib.sha256).digest()[:18]).decode().rstrip("=")
        return data + "." + mac

    bad_payloads = [
        [UID, "job1234567", "clip01.mp4", "tomorrow"],
        [UID, "job1234567", "clip01.mp4", time.time() - 5],
        [123, "job1234567", "clip01.mp4", time.time() + 60],
        ["bad uid!", "job1234567", "clip01.mp4", time.time() + 60],
        [UID, "short", "clip01.mp4", time.time() + 60],
        [UID, "job1234567", "../clip01.mp4", time.time() + 60],
        [UID, "job1234567", "clip01.avi", time.time() + 60],
        [UID, "job1234567"],
        "not a list",
    ]
    for payload in bad_payloads:
        check("an invalid signed payload is refused: " + str(payload)[:36], accounts.read_clip_link(signed(payload)), None)
    check(
        "an expired link is refused",
        accounts.read_clip_link(urlparse(accounts.clip_link("job1234567", "clip01.mp4", seconds=-5)).path[len("/pub/") : -len(".mp4")]),
        None,
    )

# ---------------------------------------------------------------- tidy up
httpx.post, httpx.get, httpx.put = _real_post, _real_get, _real_put
httpx.Client.request = _real_client_request
secretfile.write_private = _real_write_private
shutil.rmtree(DATA, ignore_errors=True)
print("ALL PASSED" if not fails else f"{len(fails)} failed")
sys.exit(1 if fails else 0)
