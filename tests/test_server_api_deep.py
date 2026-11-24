"""Deep checks of the web gate, its helpers, and the small identity/secret modules.

Runs against the code directly with its own empty data folder. No server is started: FastAPI's TestClient calls the
app in-process. Only outside work (fonts, providers, thread pools) is mocked. Nothing here renders a video or
contacts a site.
"""

import asyncio
import collections
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

# Set everything before studio is imported: that is when it reads settings and opens its database.
DATA = tempfile.mkdtemp(prefix="pc-server-deep-")
BASE = "http://127.0.0.1:8765"
os.environ.update(
    RONBCLIPS_DATA=DATA,
    RONBCLIPS_STORAGE_FILE=os.path.join(DATA, "storage.json"),
    DATABASE_URL=os.environ.get("RONBCLIPS_TEST_DATABASE_URL", ""),
    RONBCLIPS_ACCOUNTS="0",
    RONBCLIPS_FETCH_MODEL="0",
    RONBCLIPS_UPDATE_CHECK="0",
    RONBCLIPS_AI_PROVIDER="",
    RONBCLIPS_AI_MODELS="",
    RONBCLIPS_AI_KEY="",
    RONBCLIPS_AI_KEY_FILE="",
    RONBCLIPS_PUBLIC_URL=BASE,
    GOOGLE_CLIENT_ID="",
    GOOGLE_CLIENT_SECRET="",
    GOOGLE_CLIENT_FILE="",
)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient  # noqa: E402

from studio import (  # noqa: E402
    api_common,
    auth,
    config,
    ctx,
    db,
    secretfile,
    server,
)

db.init()
fails = []
ORIGIN = {"Origin": BASE}
c = TestClient(server.app, base_url=BASE, follow_redirects=False, raise_server_exceptions=False)
owner = auth.local_user()


def check(name, got, expected):
    ok = expected(got) if callable(expected) else got == expected
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"   got {got!r}"))
    if not ok:
        fails.append(name)


# ------------------------------------------------------------------ the gate on ordinary answers
r = c.get("/api/status")
check("api status opens", r.status_code, 200)
check("a request id is added", len(r.headers.get("x-request-id", "")), 32)
check("api answers are private", "no-store" in r.headers.get("cache-control", ""), True)
check("sensitive paths are not indexed", r.headers.get("x-robots-tag"), "noindex, nofollow")
check("a page is not indexed the same way", "x-robots-tag" not in c.get("/").headers, True)
check("hsts follows the https setting", ("strict-transport-security" in r.headers) == bool(config.HTTPS), True)

r = c.post("/api/creators", json={"name": "Gate fixture"}, headers=ORIGIN)
check("a legitimate write is allowed", r.status_code, 200)
CID = r.json()["id"]

# The browser's own same-origin mark is trusted even if Origin says otherwise.
r = c.post("/api/creators", json={"name": "Fetch metadata fixture"}, headers={**ORIGIN, "Sec-Fetch-Site": "same-origin"})
check("sec-fetch-site same-origin is trusted", r.status_code, 200)

# A caller may choose a short, safe request id, but not a huge or punctuated one.
r = c.get("/api/status", headers={"x-request-id": "my-req_1.2:abc"})
check("a safe request id is echoed", r.headers.get("x-request-id"), "my-req_1.2:abc")
r = c.get("/api/status", headers={"x-request-id": "x" * 200})
check(
    "an oversized request id is replaced", (r.headers.get("x-request-id") != "x" * 200, len(r.headers.get("x-request-id", ""))), (True, 32)
)

# HSTS is added by the gate when the public address is https. We change the setting for one request only.
with patch.object(config, "HTTPS", True):
    r = c.get("/api/status")
    check("hsts is added when https is configured", r.headers.get("strict-transport-security"), "max-age=31536000; includeSubDomains")

# ------------------------------------------------------------------ gate denials
r = c.post("/api/creators", json={"name": "no"}, headers={"Origin": "https://evil.example"})
check("a cross-site write is refused", r.status_code, 403)
check("a refusal still has a request id", len(r.headers.get("x-request-id", "")), 32)
check("a refusal is not cached", "no-store" in r.headers.get("cache-control", ""), True)
check("a refusal explains itself", "Cross-site" in r.json().get("detail", ""), True)

r = c.get("/no-such-page")
check("an unknown page is 404", r.status_code, 404)
check("a 404 has the common headers", (r.headers.get("x-frame-options"), r.headers.get("x-content-type-options")), ("DENY", "nosniff"))
check("a 404 is not cached", "no-store" in r.headers.get("cache-control", ""), True)

# Every address other than a local one, the configured host, or a *.localhost name is refused.
r = c.get("/api/status", headers={"Host": "somewhere.example"})
check("an unknown host is refused", (r.status_code, "own address" in r.json().get("detail", "")), (403, True))
r = c.get("/api/status", headers={"Host": "mybox.localhost:8765"})
check("a *.localhost host is allowed", r.status_code, 200)
r = c.get("/api/status", headers={"Host": "[::1]:8765"})
check("an ipv6 host is allowed", r.status_code, 200)

# Requests to the auth routes that are not /api/auth/me do not exist in this build.
check("auth sub-routes are hidden", c.get("/api/auth/session").status_code, 404)

# With no local owner, only the open pages and public files can be read.
with patch.object(auth, "_local", None), patch.object(auth, "local_user", return_value=None):
    r = c.get("/api/status")
    check("api needs the local owner", (r.status_code, "locally" in r.json().get("detail", "")), (401, True))
    check("the app page is still open", c.get("/").status_code, 200)
    check("public assets are still open", c.get("/assets/main.js").status_code, 200)

# A body larger than the small-form cap is refused before any route reads it.
big = b"x" * (300 * 1024)
r = c.post("/api/creators", content=big, headers={**ORIGIN, "Content-Type": "application/json"})
check("a 300 KB form is refused", (r.status_code, "too large" in r.json().get("detail", "")), (413, True))

# Uploads have their own, larger cap and a different explanation. Make the cap small just for this check.
with patch.object(config, "MAX_UPLOAD_MB", 0):
    r = c.post("/api/upload", content=b"x" * (9 * 1024 * 1024), headers={**ORIGIN, "Content-Type": "video/mp4"})
    check("an oversized upload is refused", r.status_code, 413)
    check("the upload limit is named", "limit is 0 MB" in r.json().get("detail", ""), True)

# A Content-Length that is not a number is a bad request, not a crash.
r = c.post("/api/creators", content=b"{}", headers={**ORIGIN, "Content-Length": "not-a-number"})
check("a bad content length is refused", r.status_code in (400, 413), True)

# Upload starts are checked for a free slot and room on the disk before a byte is accepted.
with patch.object(api_common, "job_slots", side_effect=server.HTTPException(429, "No free slot")):
    r = c.post("/api/upload/start", json={"name": "a.mp4", "size": 10}, headers=ORIGIN)
    check("a busy queue refuses an upload", (r.status_code, r.json().get("detail")), (429, "No free slot"))
with patch.object(api_common, "quota", side_effect=server.HTTPException(507, "Disk full")):
    r = c.post("/api/upload/start", json={"name": "a.mp4", "size": 10}, headers=ORIGIN)
    check("a full disk refuses an upload", (r.status_code, r.json().get("detail")), (507, "Disk full"))

# The last-seen timestamp is kept up to date for api calls.
auth._seen.clear()
with patch.object(auth, "touch", wraps=auth.touch) as touched:
    c.get("/api/status")
    check("a request touches the owner", touched.called, True)
with patch.object(auth, "seen_recently", return_value=True), patch.object(auth, "touch") as touched:
    c.get("/api/status")
    check("a recent request does not touch again", touched.called, False)

# ------------------------------------------------------------------ cheap /api/sync
with ctx.as_user(owner.id):
    from studio import creators

    creators.all_creators()  # make sure the in-memory creator map exists, which enables the short answer
    token = api_common.sync_token()
with patch("studio.sync_api.response", side_effect=AssertionError("the slow route should not run")):
    r = c.get("/api/sync", params={"v": token})
    check("an unchanged sync is answered by the gate", (r.status_code, r.json()), (200, {"v": token}))
r = c.get("/api/sync", params={"v": "wrong.token"})
check("a stale sync falls through to the route", r.status_code, 200)

# ------------------------------------------------------------------ conditional answers and compression
r = c.get("/assets/main.js")
tag = r.headers.get("etag", "")
check("a static file has an etag", bool(tag), True)
r2 = c.get("/assets/main.js", headers={"If-None-Match": tag})
check("an unchanged static file is a bodyless 304", (r2.status_code, len(r2.content)), (304, 0))
check("a weak etag also matches", c.get("/assets/main.js", headers={"If-None-Match": "W/" + tag}).status_code, 304)

r = c.get("/", headers={"Accept-Encoding": "gzip"})
check("text is compressed when asked", r.headers.get("content-encoding"), "gzip")
r = c.get("/assets/main.js", headers={"Accept-Encoding": "gzip"})
check("a large static file is compressed too", r.headers.get("content-encoding"), "gzip")

# A media file must pass through untouched so byte ranges keep working.
from studio import jobs

with ctx.as_user(owner.id):
    job = jobs.create({"type": "file", "path": "", "name": "gate.mp4"}, {}, enqueue=False)
clips = Path(job["dir"]) / "clips"
clips.mkdir(parents=True, exist_ok=True)
(clips / "clip01.mp4").write_bytes(b"gate bytes" * 200)
r = c.get(f"/media/{job['id']}/clip01.mp4", headers={"Accept-Encoding": "gzip"})
check("media is not compressed", (r.status_code, r.headers.get("content-encoding")), (200, None))
check("media keeps a short private cache", "private, max-age=300" in r.headers.get("cache-control", ""), True)


# ------------------------------------------------------------------ the pure helpers
def from_here(headers, must=False):
    """Build a tiny request and ask the gate's origin check about it."""
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/x",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "query_string": b"",
    }
    return server._from_here(server.Request(scope), must)


check("sec-fetch-site wins", from_here({"Sec-Fetch-Site": "Same-Origin", "Origin": "https://evil.example"}), True)
check("no origin is enough for a normal check", from_here({}), True)
check("no origin is not enough when one is required", from_here({}, True), False)
check("an origin matching the host is fine", from_here({"Origin": BASE, "Host": "127.0.0.1:8765"}), True)
check("an origin matching the public url is fine", from_here({"Origin": BASE}), True)
check("an unrelated origin is refused", from_here({"Origin": "https://evil.example"}), False)
check("a trailing slash on an origin is harmless", from_here({"Origin": BASE + "/"}), True)

for host, want in (
    ("127.0.0.1", True),
    ("localhost", True),
    ("127.0.0.1:8765", True),
    ("[::1]:8765", True),
    ("127.0.0.1.nip.io", False),
    ("somewhere.example", False),
    ("127.0.0.1:8765:8765", False),
):
    check(f"own address {host}", server._own_address(host), want)


def trust(*values):
    def fake(key, default=""):
        return values[0] if key == "RONBCLIPS_TRUST_PROXY" else default

    with patch.object(config, "env", side_effect=fake):
        return server._trust_proxy()


check("proxy trust is off by default", trust(""), False)
check("zero means off", trust("0"), False)
check("the word false means off", trust(" false "), False)
check("the word no means off", trust("NO"), False)
check("off means off", trust("off"), False)
check("one means on", trust("1"), True)
check("true means on", trust("True"), True)
check("yes means on", trust("yes"), True)
check("on means on", trust("on"), True)


def visitor(peer="10.0.0.5", header=None, trusted="1", proxies="127.0.0.1,::1", header_name="CF-Connecting-IP"):
    client = (peer, 1234) if peer is not None else None
    scope = {"type": "http", "client": client, "path": "/api/x", "method": "GET", "headers": [], "query_string": b""}
    if header is not None:
        scope["headers"] = [(header_name.lower().encode(), header.encode())]
    with (
        patch.object(config, "PROXY_IPS", proxies),
        patch.object(config, "CLIENT_IP_HEADER", header_name),
        patch.object(config, "env", side_effect=lambda k, d="": trusted if k == "RONBCLIPS_TRUST_PROXY" else d),
    ):
        return server._visitor_ip(scope, server.Request(scope))


check("the socket address is used normally", visitor(header="8.8.8.8"), "10.0.0.5")
check("a trusted proxy supplies an address", visitor(peer="127.0.0.1", header="8.8.8.8"), "8.8.8.8")
check("an address is normalised", visitor(peer="127.0.0.1", header="2001:0db8::1"), "2001:db8::1")
check("the first address in a list is used", visitor(peer="127.0.0.1", header="8.8.8.8, 1.1.1.1"), "8.8.8.8")
check("a name in the header is ignored", visitor(peer="127.0.0.1", header="not-an-ip"), "127.0.0.1")
check("an empty header is ignored", visitor(peer="127.0.0.1", header=""), "127.0.0.1")
check("an unlisted proxy is not trusted", visitor(peer="192.168.1.9", header="8.8.8.8"), "192.168.1.9")
check("a wildcard allows any proxy", visitor(peer="192.168.1.9", header="8.8.8.8", proxies="*"), "8.8.8.8")
check("a custom header is read", visitor(peer="127.0.0.1", header="9.9.9.9", header_name="X-Real-IP"), "9.9.9.9")
check("a missing socket address is empty", visitor(peer=None), "")


# ------------------------------------------------------------------ non-http scopes and the app shims
def non_http():
    seen = []

    async def inner(scope, receive, send):
        seen.append(scope)

    gate = server.Gate(inner)

    async def run():
        for kind in ("websocket", "lifespan"):
            await gate({"type": kind}, None, None)

    asyncio.run(run())
    return seen


passed = non_http()
check("non-http scopes pass straight through", len(passed), 2)
check("non-http scopes get no user state", all("state" not in scope for scope in passed), True)

check(
    "an exception in a background job is printed",
    isinstance(server._quiet(lambda: (_ for _ in ()).throw(ValueError("fine"))), type(None)),
    True,
)

with patch("studio.clip_api.rerender") as fn:
    server.rerender("job12345", "clip1", {"start": 1})
    check(
        "the rerender shim passes its helpers",
        (fn.call_args.args[0], fn.call_args.args[1], callable(fn.call_args.args[3])),
        ("job12345", "clip1", True),
    )
with patch("studio.clip_api.clip_text") as fn:
    server.clip_text("job12345", "clip1", {"title": "x"})
    check("the clip text shim passes its helpers", fn.call_args.args[:2], ("job12345", "clip1"))
with patch("studio.clip_api.clip_write") as fn:
    server.clip_write("job12345", "clip1")
    check("the clip write shim passes its helpers", fn.call_args.args[:2], ("job12345", "clip1"))
with patch("studio.creator_api.new_creator", return_value={"ok": True}) as fn:
    check("the creator shim accepts a dict", server.new_creator({"name": "x"}), {"ok": True})
    check("the creator shim normalises the form", fn.call_args.args[0].name, "x")

# ------------------------------------------------------------------ startup and shutdown
import anyio.to_thread as _to_thread  # noqa: E402

os.environ["RONBCLIPS_REQUEST_THREADS"] = "24"


async def thread_pool_check():
    await server._more_threads()
    return _to_thread.current_default_thread_limiter().total_tokens


check("the thread pool size can be set", asyncio.run(thread_pool_check()), 24)

with (
    patch.object(server.db, "init") as init,
    patch.object(server.render, "install_fonts") as fonts,
    patch.object(server._tools, "missing", return_value=False),
    patch.object(server.transcribe, "REMOTE", True),
    patch.object(server.jobs, "start") as jobs_start,
    patch.object(server.live, "start") as live_start,
    patch.object(server.posting, "start") as posting_start,
    patch.object(server.sync_api, "start_background_refresh") as refresh,
    patch("studio.updates.start") as updates,
):
    server._startup()
    check("startup opens the database", init.called, True)
    check("startup installs fonts", fonts.called, True)
    check("startup starts the pipeline", (jobs_start.called, live_start.called, posting_start.called), (True, True, True))
    check("startup starts background work", (refresh.called, updates.called), (True, True))

with (
    patch.object(server.live, "stop_all") as live_stop,
    patch.object(server.posting, "stop") as posting_stop,
    patch.object(server.jobs, "stop") as jobs_stop,
    patch.object(server.tasks, "stop") as tasks_stop,
):
    server._shutdown()
    check(
        "shutdown stops everything", (live_stop.called, posting_stop.called, jobs_stop.called, tasks_stop.called), (True, True, True, True)
    )
    check("shutdown waits for the queue", jobs_stop.call_args.kwargs, {"wait": True})

# ------------------------------------------------------------------ main()
with patch.object(server.uvicorn, "run") as run:
    os.environ["RONBCLIPS_TRUST_PROXY"] = "0"
    os.environ.pop("RONBCLIPS_AUTO_RELOAD", None)
    server.main()
    check("main starts uvicorn once", run.call_count, 1)
    check("no proxy trust is passed on", run.call_args.kwargs["proxy_headers"], False)
    check("no forwarded ips are passed on", run.call_args.kwargs["forwarded_allow_ips"], "")

with patch.object(server.uvicorn, "run") as run:
    os.environ["RONBCLIPS_TRUST_PROXY"] = "1"
    os.environ["RONBCLIPS_AUTO_RELOAD"] = "1"
    server.main()
    check("auto reload is supported", run.call_args.kwargs["reload"], True)
    check(
        "trusted proxies are passed on",
        (run.call_args.kwargs["proxy_headers"], run.call_args.kwargs["forwarded_allow_ips"] == config.PROXY_IPS),
        (True, True),
    )
os.environ.pop("RONBCLIPS_TRUST_PROXY", None)
os.environ.pop("RONBCLIPS_AUTO_RELOAD", None)

# ------------------------------------------------------------------ ctx: which user is asking
check("there is no user by default", ctx.uid_or_none(), None)
try:
    ctx.uid()
    check("uid() needs a user", False, True)
except ctx.NoUser:
    check("uid() needs a user", True, True)
check("an empty ip means background work", ctx.ip(), "")
check("set_uid installs a user for a thread", (ctx.set_uid("u_local"), ctx.uid())[1], "u_local")
check("the ip can be set for a request", (ctx._ip.set("1.2.3.4"), ctx.ip())[1], "1.2.3.4")
ctx._ip.set("")
with ctx.as_user("u_worker"):
    check("as_user switches identity", ctx.uid(), "u_worker")
check("as_user restores the old identity", ctx.uid(), "u_local")
check("bind captures the current user", ctx.bind(lambda: ctx.uid())(), "u_local")
ctx.set_uid(None)

before = ctx.version("u_counter")
ctx.bump("u_counter")
check("bump moves a named counter", ctx.version("u_counter"), before + 1)
with ctx.as_user("u_counter"):
    ctx.bump()
    check("bump uses the current user", ctx.version(), before + 2)
    check("version uses the current user", ctx.version(), before + 2)
ctx.bump(None)
check("bump without a user is harmless", ctx.version("u_counter"), before + 2)

# ------------------------------------------------------------------ auth: the built-in owner
user = auth.local_user()
check("the local owner has a fixed id", user.id, auth.LOCAL_ID)
check("the local owner is named You", user.name, "You")
check("the owner's folder exists", config.user_dir(user.id).is_dir(), True)
check("public(None) is empty", auth.public(None), None)
check("public() hides nothing about the owner", auth.public(user), {"id": user.id, "name": "You"})
check("there is exactly one user id", auth.user_ids(), [auth.LOCAL_ID])
check("a new owner has not been seen", auth.seen_recently("u_never_seen"), False)
auth.touch("u_seen")
check("touching an owner makes them recent", auth.seen_recently("u_seen"), True)

# Throttling keeps a short memory of attempts and forgets old ones.
with patch("studio.auth.ctx.ip", return_value="9.9.9.9"):
    key = "deep-throttle"
    try:
        for _ in range(3):
            auth.throttle(key, 3, 60)
        check("three attempts fit in the limit", True, True)
        try:
            auth.throttle(key, 3, 60)
            check("a fourth attempt is refused", False, True)
        except auth.AuthError:
            check("a fourth attempt is refused", True, True)
        full = "9.9.9.9|" + key
        # After the window has passed, the old attempts for this key are dropped one by one.
        with patch("studio.auth.time.time", return_value=__import__("time").time() + 120):
            auth.throttle(key, 3, 60)
        check("attempts older than the window are dropped", len(auth._attempts[full]), 1)
        old = auth._attempts
        auth._attempts = collections.defaultdict(collections.deque)
        try:
            auth.throttle(key, 1, 60)
            check("a fresh memory allows another attempt", len(auth._attempts[full]), 1)
            long_key = "k" * 300
            auth.throttle(long_key, 1, 60)
            check("keys are shortened", max(len(k) for k in auth._attempts) <= 200, True)
            # With a very large memory, old and empty entries are dropped before a new attempt is recorded.
            seed = {f"old{i}": collections.deque([0]) for i in range(20001)}
            seed.update({"gone": collections.deque(), "fresh": collections.deque([__import__("time").time()])})
            auth._attempts = collections.defaultdict(collections.deque, seed)
            with patch("studio.auth.time.time", return_value=4000):
                auth.throttle(key, 1, 60)
            check("old and empty attempts are forgotten", ("gone" not in auth._attempts, "old0" not in auth._attempts), (True, True))
        finally:
            auth._attempts = old
    finally:
        pass

# ------------------------------------------------------------------ secretfile: private atomic writes
secret = Path(DATA) / "keys" / "token.txt"
secret.parent.mkdir(parents=True, exist_ok=True)
secretfile.write_private(secret, b"top-secret-123")
check("the secret is written", secret.read_bytes(), b"top-secret-123")
check("no half-written file is left behind", secret.with_name(secret.name + ".tmp").exists(), False)
secretfile.write_private(secret, b"replacement")
check("a rewrite replaces the old bytes", secret.read_bytes(), b"replacement")

# Some filesystems refuse the early mode; the write must still happen.
with (
    patch.object(secretfile.os, "open", side_effect=OSError("no modes here")),
    patch.object(secretfile.os, "chmod", side_effect=OSError("no chmod here")),
):
    secretfile.write_private(secret, b"fallback")
check("a filesystem without modes still gets the secret", secret.read_bytes(), b"fallback")

print("ALL PASSED" if not fails else f"{len(fails)} failed")
c.close()
db.engine.dispose()
shutil.rmtree(DATA, ignore_errors=True)
sys.exit(1 if fails else 0)
