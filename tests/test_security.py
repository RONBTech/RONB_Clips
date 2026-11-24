"""Security headers on ordinary answers, on requests the gate refuses, and on 404s. Runs against the code directly,
with its own empty data folder: nothing outside it is touched.

HSTS is the one header that depends on the setting in force: the gate adds it only when RONBCLIPS_PUBLIC_URL is an
https address (config.HTTPS). The check below follows that setting, so this file works for both http and https.
"""

import os
import shutil
import sys
import tempfile

os.environ["RONBCLIPS_DATA"] = tempfile.mkdtemp(prefix="pc-security-")
os.environ.update(
    DATABASE_URL=os.environ.get("RONBCLIPS_TEST_DATABASE_URL", ""),
    RONBCLIPS_ACCOUNTS="0",
    RONBCLIPS_PUBLIC_URL="http://127.0.0.1:8765",
    RONBCLIPS_FETCH_MODEL="0",
    RONBCLIPS_UPDATE_CHECK="0",
)
sys.path.insert(0, ".")
from fastapi.testclient import TestClient  # noqa: E402

from studio import config, db, server  # noqa: E402

db.init()
fails = []
BASE = "http://127.0.0.1:8765"
ORIGIN = {"Origin": BASE}
c = TestClient(server.app, base_url=BASE, follow_redirects=False)


def check(name, got, expected):
    ok = expected(got) if callable(expected) else got == expected
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"   got {got!r}"))
    if not ok:
        fails.append(name)


HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "strict-origin-when-cross-origin",
    "permissions-policy": "camera=(), microphone=(), geolocation=()",
}


def check_common(label, response):
    for name, want in HEADERS.items():
        check(f"{label}: {name}", response.headers.get(name), want)
    check(f"{label}: content-security-policy", "default-src 'self'" in response.headers.get("content-security-policy", ""), True)
    check(f"{label}: x-request-id", bool(response.headers.get("x-request-id")), True)


# ---- an ordinary 200 answer from an API route
ok = c.get("/api/status")
check("the status route opens", ok.status_code, 200)
check_common("200", ok)
check("API answers are never stored by a cache", "no-store" in ok.headers.get("cache-control", ""), True)
check("a page response has a request id too", bool(c.get("/").headers.get("x-request-id")), True)
check("HSTS appears only when HTTPS is configured", ("strict-transport-security" in ok.headers) == bool(config.HTTPS), True)

# ---- a refusal from the gate, before the app itself runs
denied = c.post("/api/creators", json={"name": "Not allowed"}, headers={"Origin": "https://elsewhere.example"})
check("a change from another website is refused", denied.status_code, 403)
check_common("403", denied)
check("a refusal is never stored by a cache", "no-store" in denied.headers.get("cache-control", ""), True)

# ---- a 404 from the app, which still goes through the gate on the way out
missing = c.get("/no-such-page")
check("an unknown address is 404", missing.status_code, 404)
check_common("404", missing)
check("a missing page is never stored by a cache", "no-store" in missing.headers.get("cache-control", ""), True)

print("ALL PASSED" if not fails else f"{len(fails)} failed")
db.engine.dispose()
shutil.rmtree(os.environ["RONBCLIPS_DATA"], ignore_errors=True)
sys.exit(1 if fails else 0)
