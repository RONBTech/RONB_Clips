"""With accounts off (the default): no sign-in, and the AI key is set from the dashboard. Runs against the code
directly, with its own empty data folder and the network replaced: nothing outside it is touched."""

import json
import os
import sys
import tempfile
import threading
import time

os.environ["RONBCLIPS_DATA"] = tempfile.mkdtemp(prefix="pc-local-")
os.environ.update(
    DATABASE_URL=os.environ.get("RONBCLIPS_TEST_DATABASE_URL", ""),
    RONBCLIPS_ACCOUNTS="0",
    RONBCLIPS_AI_PROVIDER="gemini",
    RONBCLIPS_AI_MODELS="",
    RONBCLIPS_AI_KEY="",
    RONBCLIPS_AI_KEY_FILE="",
    RONBCLIPS_AI_BASE_URL="",
    RONBCLIPS_PUBLIC_URL="http://127.0.0.1:8765",
)
sys.path.insert(0, ".")
from fastapi.testclient import TestClient  # noqa: E402

from studio import ai, auth, config, ctx, db, jobs, server, tasks  # noqa: E402

db.init()
fails = []
BASE = "http://127.0.0.1:8765"
ORIGIN = {"Origin": BASE}
c = TestClient(server.app, base_url=BASE, follow_redirects=False)


def check(name, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"   got {got!r}"))
    ok or fails.append(name)


# ---- no sign-in anywhere
check("the dashboard opens at the root", c.get("/").status_code, 200)
check(
    "root-level app pages open",
    (
        c.get("/videos").status_code,
        c.get("/usage").status_code,
        c.get("/settings/apps").status_code,
        c.get("/creators/example").status_code,
    ),
    (200, 200, 200, 200),
)
live, ready, detail = (c.get(p) for p in ("/livez", "/readyz", "/health/detail"))
check(
    "health endpoints say what they checked",
    (
        (live.status_code, live.json().get("status")),
        (ready.status_code, ready.json().get("status")),
        (detail.status_code, detail.json().get("ok"), sorted(detail.json())),
    ),
    (
        (200, "alive"),
        (200, "ready"),
        (
            200,
            True,
            [
                "database",
                "db",
                "host",
                "jobs",
                "lifecycle",
                "load",
                "mode",
                "ok",
                "port",
                "speech_model",
                "status",
                "version",
                "workers",
            ],
        ),
    ),
)
check(
    "detailed health reports a real database probe and the queue",
    (
        detail.json()["database"],
        detail.json()["db"]["backend"],
        detail.json()["db"]["ok"],
        sorted(detail.json()["jobs"]),
    ),
    ("connected", db.engine.dialect.name, True, ["running", "waiting"]),
)
check(
    "detailed health reports lifecycle and workers",
    (
        detail.json()["status"],
        detail.json()["lifecycle"]["state"],
        sorted(detail.json()["workers"]),
    ),
    ("healthy", "running", ["job_threads", "live_sessions", "queue_closed", "shutting_down"]),
)
check("detailed health is not in the public list", "/health/detail" not in server.OPEN, True)

# Five of each, so the pages can be checked by counting what comes back.
owner = auth.local_user()
with ctx.as_user(owner.id):
    for i in range(5):
        jobs.create({"type": "file", "path": "", "name": f"page-{i}.mp4"}, {}, enqueue=False)
for i in range(5):
    c.post("/api/creators", json={"name": f"Page {i}"}, headers=ORIGIN)


def page(path):
    return c.get(path).json()


check("job list honours limit and offset", (len(page("/api/jobs?limit=2")), len(page("/api/jobs?limit=2&offset=2"))), (2, 2))
check("job list clamps odd limits", (len(page("/api/jobs?limit=0&offset=-1")), len(page("/api/jobs?limit=999"))), (1, 5))
check(
    "creator list honours limit and offset",
    (len(page("/api/creators?limit=2")), len(page("/api/creators?limit=2&offset=2"))),
    (2, 2),
)
check(
    "creator list clamps odd limits",
    (len(page("/api/creators?limit=0&offset=-1")), len(page("/api/creators?limit=999"))),
    (1, 5),
)
check(
    "notifications and usage lists accept bounded pages",
    (
        c.get("/api/notifications?limit=0&offset=-1").status_code,
        c.get("/api/usage?limit=0&offset=-1").status_code,
    ),
    (200, 200),
)
tid = tasks.submit("a small background job", lambda: 42)
for _ in range(50):
    if tasks.get(tid)["status"] != "running":
        break
    time.sleep(0.02)
task = c.get(f"/api/tasks/{tid}").json()
check("background tasks report their result", (task["status"], task["result"]), ("done", 42))
hold = threading.Event()
old_cap = tasks.MAX_TASKS
try:
    tasks.MAX_TASKS = 3
    held = [tasks.submit("held", hold.wait) for _ in range(5)]
    check("the task list stays bounded when many jobs are queued", len(tasks._tasks) <= 3, True)
    check("the oldest queued job is dropped first", tasks.get(held[0])["status"], "missing")
finally:
    hold.set()
    tasks.MAX_TASKS = old_cap
check(
    "old app and account addresses do not exist",
    (c.get("/app").status_code, c.get("/login").status_code, c.get("/signup").status_code, c.get("/reset").status_code),
    (404, 404, 404, 404),
)
denied = c.post("/api/creators", json={"name": "Not allowed"}, headers={"Origin": "https://elsewhere.example"})
check(
    "refused requests carry a request id",
    (denied.status_code, bool(denied.headers.get("x-request-id"))),
    (403, True),
)
st = c.get("/api/status").json()
check("the page is told who is using it", st["user"], {"id": auth.LOCAL_ID, "name": "You"})
check("the owner's things work", c.post("/api/creators", json={"name": "Someone"}, headers=ORIGIN).status_code, 200)
check("the owner cannot delete itself", c.post("/api/account/delete", json={"confirm": "anything"}, headers=ORIGIN).status_code, 404)

# ---- with no sign-in, the address and the sender still have to be right
check(
    "our own page is believed even when the address in between was rewritten",
    c.put("/api/settings", json={}, headers={"Origin": "null", "Sec-Fetch-Site": "same-origin"}).status_code,
    200,
)
check(
    "another website is refused whatever address it claims",
    c.put("/api/settings", json={}, headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"}).status_code,
    403,
)
check(
    "another program on this computer's page is refused",
    c.put("/api/settings", json={}, headers={"Origin": "http://localhost:3000", "Sec-Fetch-Site": "same-site"}).status_code,
    403,
)
check(
    "another website cannot change things",
    c.post("/api/creators", json={"name": "x"}, headers={"Origin": "https://evil.example"}).status_code,
    403,
)
check("a made-up name pointing at this machine is refused", c.get("/api/status", headers={"Host": "evil.example:8765"}).status_code, 403)
for host in ("localhost:8765", "192.168.1.20:8765", "[::1]:8765", "127.0.0.1"):
    check(f"reached as {host}", c.get("/api/status", headers={"Host": host}).status_code, 200)

# ---- the AI key, from the dashboard
KEY = "test-key-abcdefgh12345678"
a = c.get("/api/ai").json()
check("no AI to begin with", (a["ready"], a["has_key"], st["ai"]["ready"]), (False, False, False))
check("the services are listed", [p["id"] for p in a["providers"]][:3], ["gemini", "openai", "xai"])
put = lambda body: c.put("/api/ai", json=body, headers=ORIGIN)  # noqa: E731
check("a service needs its key", put({"provider": "gemini", "models": "model-a"}).status_code, 400)
check("...and a model", put({"provider": "gemini", "models": "", "key": KEY}).status_code, 400)
check("an unknown service is refused", put({"provider": "nonsense", "models": "m", "key": KEY}).status_code, 400)
check("a custom service needs an address", put({"provider": "custom", "models": "m"}).status_code, 400)
check("an address must be a web address", put({"provider": "custom", "models": "m", "base_url": "file:///etc/passwd"}).status_code, 400)
check("a model id cannot carry anything odd", put({"provider": "gemini", "models": 'm"}; drop', "key": KEY}).status_code, 400)
check(
    "saving from another website is refused",
    c.put("/api/ai", json={"provider": "gemini", "models": "m", "key": KEY}, headers={"Origin": "https://evil.example"}).status_code,
    403,
)
r = put({"provider": "gemini", "models": "model-a, model-b", "key": KEY})
check(
    "the key is saved",
    (r.status_code, r.json()["ready"], r.json()["models"], r.json()["key_hint"]),
    (200, True, ["model-a", "model-b"], "5678"),
)
check("the key itself is never sent back", KEY in r.text or KEY in c.get("/api/ai").text or KEY in c.get("/api/status").text, False)
check(
    "every page now says the AI is ready",
    c.get("/api/status").json()["ai"],
    lambda x: x["ready"] and x["provider"] == "Gemini" and x["model"] == "model-a",
)
check("it is kept in the data folder", json.loads((config.DATA_DIR / "ai.json").read_text())["key"], KEY)
r = put({"provider": "gemini", "models": "model-c", "key": ""})
check("saving again without typing the key keeps it", (r.status_code, r.json()["has_key"], ai.key()), (200, True, KEY))
check(
    "changing service without a key is refused, and the old key is not carried over",
    put({"provider": "openai", "models": "m"}).status_code,
    400,
)
r = put({"provider": "ollama", "models": "llama"})
check("a model on this machine needs no key", (r.status_code, r.json()["ready"], ai.key()), (200, True, ""))


# ---- finding models and testing, with the network replaced
class Reply:
    def __init__(self, status, data):
        self.status_code, self._d, self.text = status, data, json.dumps(data)

    def json(self):
        return self._d


asked = []


def fake_get(url, params=None, headers=None, timeout=None):
    asked.append((url, headers or {}))
    if "generativelanguage" in url:
        if (headers or {}).get("x-goog-api-key") != KEY:
            return Reply(400, {"error": {"message": f"API key not valid: {(headers or {}).get('x-goog-api-key')}"}})
        return Reply(
            200,
            {
                "models": [
                    {"name": "models/g-fast", "supportedGenerationMethods": ["generateContent"]},
                    {"name": "models/g-embed", "supportedGenerationMethods": ["embedContent"]},
                ]
            },
        )
    return Reply(200, {"data": [{"id": "b-model"}, {"id": "a-model"}]})


def fake_post(url, json=None, headers=None, timeout=None):  # noqa: A002
    if (headers or {}).get("x-goog-api-key") != KEY:
        return Reply(403, {"error": {"message": "bad key " + str((headers or {}).get("x-goog-api-key"))}})
    return Reply(
        200, {"candidates": [{"content": {"parts": [{"text": '["ok"]'}]}}], "usageMetadata": {"promptTokenCount": 9, "totalTokenCount": 12}}
    )


ai.httpx.get, ai.httpx.post = fake_get, fake_post
post = lambda path, body=None: c.post(path, json=body or {}, headers=ORIGIN)  # noqa: E731


def finish(r):
    """The long calls answer with a task id first; read the answer the way the page does."""
    tid = r.json()["id"]
    for _ in range(200):
        got = c.get(f"/api/tasks/{tid}").json()
        if got["status"] != "running":
            return (r.status_code, got)
        time.sleep(0.02)
    raise AssertionError("the task did not finish")


r = post("/api/ai/models", {"provider": "gemini", "key": KEY})
code, task = finish(r)
check("models are listed with a key typed but not saved yet", (code, task["status"], task["result"]["models"]), (200, "done", ["g-fast"]))
r = post("/api/ai/models", {"provider": "openai", "key": "sk-whatever-123456"})
code, task = finish(r)
check("other services list theirs, in order", task["result"]["models"], ["a-model", "b-model"])
r = post("/api/ai/models", {"provider": "gemini", "key": "wrong-key-000000000000"})
code, task = finish(r)
check("a refused key is explained without repeating it", (task["status"], "wrong-key-000000000000" in task["error"]), ("error", False))
check("a refused key still starts a task cleanly", code, 200)
put({"provider": "gemini", "models": "g-fast", "key": KEY})
code, task = finish(post("/api/ai/test"))
check(
    "the test asks the saved model and reports back",
    (code, task["status"], task["result"]["model"], task["result"]["ok"]),
    (200, "done", "g-fast", True),
)
put({"provider": "gemini", "models": "g-fast", "key": "wrong-key-000000000000"})
code, task = finish(post("/api/ai/test"))
check("a failed test says why, without the key", (task["status"], "wrong-key-000000000000" in task["error"]), ("error", False))

print("ALL PASSED" if not fails else f"{len(fails)} failed")
import shutil  # noqa: E402

db.engine.dispose()
shutil.rmtree(os.environ["RONBCLIPS_DATA"], ignore_errors=True)
sys.exit(1 if fails else 0)
