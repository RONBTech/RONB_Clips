"""Real end-to-end browser test for the no-build frontend (web/index.html).

It starts a throwaway RONB Clips server on a spare port, opens the real app in
headless Chromium, clicks through it, checks for JavaScript errors, and cleans
everything up afterwards. No test doubles: the page talks to the real server.

SETUP (once)
------------
The Python package and the Chromium binary are installed separately. Pin the
version that matches the browser builds already in
``%LOCALAPPDATA%\\ms-playwright`` (chromium-1217 / chromium-1243)::

    python -m pip install playwright==1.63.0
    python -m playwright install chromium

The second command is only needed if Playwright cannot find a browser on this
machine. It looks in the usual Playwright folder by itself; to force a specific
binary, pass ``--chromium C:\\path\\to\\chrome.exe``.

RUN
---
::

    python scripts/e2e_browser.py
    python scripts/e2e_browser.py --port 8797 --slowmo-ms 150
    python scripts/e2e_browser.py --headed
    python scripts/e2e_browser.py --chromium "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"

The script prints PASS with a list of the interactions it performed, or FAIL
with the reason, the server log and a non-zero exit code. It never touches the
project's real ``data`` folder.
"""

import argparse
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

PINNED_PLAYWRIGHT = "1.63.0"
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PORT = 8797
HEALTH_TIMEOUT_S = 60.0
PAGE_TIMEOUT_MS = 15000
NAV_TIMEOUT_MS = 20000

# These are harmless in a sandboxed test run. They are reported in the summary
# so a real failure can never hide behind the allow-list.
IGNORED_CONSOLE = (
    "favicon.ico",
    "fonts.googleapis.com",
    "fonts.gstatic.com",
)

# A deliberately small environment: no inherited AI keys, data folder, database
# address or other RONB settings can leak into the throwaway server. Accounts
# are off in this version of RONB Clips anyway (studio/auth.py uses one local
# owner), but clearing ACCOUNTS keeps the intent explicit.
KEEP_ENV = (
    "PATH",
    "SYSTEMROOT",
    "WINDIR",
    "TEMP",
    "TMP",
    "TMPDIR",
    "HOME",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "LOCALAPPDATA",
    "APPDATA",
    "COMSPEC",
    "PATHEXT",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "LANG",
    "LC_ALL",
    "PYTHONUTF8",
    "PYTHONIOENCODING",
)


class CheckFailed(Exception):
    """An assertion about the page failed."""


def check(ok, message):
    if not ok:
        raise CheckFailed(message)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--headed", action="store_true", help="show the browser window (default: headless)")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help="port to try first (default: %(default)s)")
    p.add_argument("--chromium", default=None, help="explicit path to a Chromium/Chrome executable")
    p.add_argument("--slowmo-ms", type=int, default=0, help="slow Playwright down by this many ms per action")
    return p.parse_args(argv)


def free_port(start):
    """Return the first bindable port at or after ``start``."""
    for port in range(start, min(start + 100, 65536)):
        with socket.socket() as sock:
            sock.settimeout(0.2)
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"No free port found at or above {start}.")


def server_env(data_dir, port):
    env = {k: os.environ[k] for k in KEEP_ENV if k in os.environ}
    env.update(
        {
            "RONBCLIPS_DATA": str(data_dir),
            "RONBCLIPS_PORT": str(port),
            "RONBCLIPS_HOST": "127.0.0.1",
            "RONBCLIPS_PUBLIC_URL": f"http://127.0.0.1:{port}",
            "RONBCLIPS_FETCH_MODEL": "0",  # no 480 MB speech model download during a UI test
            "RONBCLIPS_YT_TRANSCRIPT": "0",
            "PYTHONUNBUFFERED": "1",
            "ACCOUNTS": "0",
        }
    )
    return env


def wait_health(port, proc, deadline):
    url = f"http://127.0.0.1:{port}/healthz"
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"The server exited early with code {proc.returncode}.")
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except Exception:
            pass
        time.sleep(0.25)
    raise RuntimeError(f"The server did not answer {url} with 200 within {HEALTH_TIMEOUT_S:.0f} seconds.")


def stop_server(proc):
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)


def print_log(path, limit=120):
    if not path or not path.exists():
        return
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    print(f"\n--- server log (last {min(limit, len(lines))} lines) ---")
    for line in lines[-limit:]:
        print(line)
    print("--- end of server log ---")


def main(argv=None):
    args = parse_args(argv)
    temp = None
    proc = None
    log_path = None
    log_file = None
    pw_manager = None
    browser = None
    context = None
    failure = None
    steps = []
    console_errors = []
    page_errors = []
    ignored = []
    expected_failed_urls = []

    try:
        # Lazy import: this script can be compiled and listed without Playwright
        # installed, and the error below explains exactly what to install.
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Playwright is not installed. Install it with:\n"
                f"    python -m pip install playwright=={PINNED_PLAYWRIGHT}\n"
                "    python -m playwright install chromium\n"
                "Then run this script again."
            ) from exc

        temp = tempfile.TemporaryDirectory(prefix="ronbclips-e2e-")
        tmp = Path(temp.name)
        data_dir = tmp / "data"
        data_dir.mkdir()
        log_path = tmp / "server.log"
        port = free_port(args.port)
        print(f"Starting RONB Clips on port {port} with data in {data_dir} ...")

        log_file = log_path.open("w", encoding="utf-8", errors="replace")
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)  # Windows: no orphan on Ctrl+C
        proc = subprocess.Popen(
            [sys.executable, "-m", "studio.server"],
            cwd=str(ROOT),
            env=server_env(data_dir, port),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            creationflags=flags,
        )
        wait_health(port, proc, time.monotonic() + HEALTH_TIMEOUT_S)
        base = f"http://127.0.0.1:{port}"
        print(f"Server is healthy at {base}")

        pw_manager = sync_playwright()
        playwright = pw_manager.start()
        launch_options = {
            "headless": not args.headed,
            "slow_mo": args.slowmo_ms,
        }
        if args.chromium:
            launch_options["executable_path"] = args.chromium
        browser = playwright.chromium.launch(**launch_options)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        page.set_default_timeout(PAGE_TIMEOUT_MS)
        page.set_default_navigation_timeout(NAV_TIMEOUT_MS)

        def abort_api(route):
            expected_failed_urls.append(route.request.url)
            route.abort()

        def on_console(msg):
            if msg.type != "error":
                return
            text = msg.text or ""
            url = (msg.location or {}).get("url", "") or ""
            detail = f"{text} [{url}]" if url else text
            if "net::ERR_FAILED" in text and any(url in detail for url in expected_failed_urls):
                ignored.append(detail)
                return
            for pattern in IGNORED_CONSOLE:
                if pattern in text or pattern in url:
                    ignored.append(detail)
                    return
            console_errors.append(detail)

        page.on("console", on_console)
        page.on("pageerror", lambda error: page_errors.append(str(error)))

        schema_response = page.request.get(base + "/api/openapi.json")
        check(schema_response.status == 200, "The generated OpenAPI schema is not served.")
        schema = schema_response.json()
        check("/api/status" in schema.get("paths", {}), "The generated API schema is missing the status route.")
        docs_response = page.request.get(base + "/api/docs")
        check(
            docs_response.status == 200 and "swagger-ui" in docs_response.text().lower(), "The generated API reference page is not served."
        )
        steps.append("checked the generated OpenAPI schema and API reference page")

        # ---- home page -------------------------------------------------------
        page.goto(base, wait_until="domcontentloaded")
        title = (page.title() or "").strip()
        check(bool(title), "The page title is empty.")
        # Real static elements from web/index.html.
        for selector in ("#app", "#nav", "#title", "#view"):
            check(page.locator(selector).count() == 1, f"Missing element from index.html: {selector}")
        # Real elements produced by web/assets/shell.js and main.js at startup.
        page.wait_for_selector('#nav .nav-item[data-act="go"][data-v="library"]', state="visible")
        page.wait_for_timeout(750)
        check(
            page.locator("#ov .tiles").count() == 1,
            "Overview did not render; #ov="
            + (page.locator("#ov").inner_html() if page.locator("#ov").count() else "missing")
            + "; global="
            + page.locator("#global-state").inner_text(),
        )
        check((page.locator("#title span").inner_text() or "").strip() == "Home", "The home page header is not 'Home'.")
        check(page.locator("#addform.composer").count() == 1, "The add/composer form was not drawn.")
        check(page.locator("#global-state").get_attribute("aria-live") == "polite", "Global loading status is not announced politely.")
        # Basic accessible-name gate across visible controls, plus a useful ARIA snapshot.
        unnamed = page.locator("button:visible, a:visible, input:visible, select:visible, textarea:visible").evaluate_all(
            "els => els.filter(e => !e.disabled && !(e.getAttribute('aria-label') || e.getAttribute('aria-labelledby') || e.title || e.innerText.trim() || (e.labels && [...e.labels].some(l => l.innerText.trim())))).map(e => e.outerHTML)"
        )
        check(not unnamed, "Visible interactive controls need accessible names: " + " | ".join(unnamed))
        nav_snapshot = page.locator("#nav").aria_snapshot()
        check("Home" in nav_snapshot and "All videos" in nav_snapshot, "Main navigation ARIA snapshot is missing named destinations.")
        steps.append("checked visible controls for accessible names and inspected the main navigation ARIA snapshot")
        # Tab focus must be visible, and native Enter/Space activation must work.
        page.locator("#collapseBtn").focus()
        focus_style = page.locator("#collapseBtn").evaluate("e => getComputedStyle(e).outlineStyle")
        check(focus_style != "none", "Keyboard focus is not visibly outlined on the sidebar control.")
        page.keyboard.press("Space")
        check("mini" in (page.locator("#app").get_attribute("class") or ""), "Space did not activate the sidebar collapse button.")
        page.keyboard.press("Space")
        check("mini" not in (page.locator("#app").get_attribute("class") or ""), "Space did not restore the sidebar.")
        steps.append("verified visible keyboard focus and Space activation")
        steps.append(f"opened home page (title {title!r}) and waited for the shell drawn by shell.js")

        # A fresh data folder shows the guided tour after ~500 ms. Close it with
        # its real close button so it cannot intercept later clicks.
        tour = page.locator("#tour .tr-x")
        try:
            tour.wait_for(state="visible", timeout=3000)
        except Exception:
            pass  # the tour is only shown once, on a fresh data folder
        if tour.count():
            tour.click()
            page.wait_for_selector("#tour", state="detached", timeout=8000)
            steps.append("closed the first-run tour overlay")

        # Failure states stay visible until their request succeeds.
        page.route("**/api/status", abort_api)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#global-state .failed [data-act='retrystatus']", state="visible")
        page.unroute("**/api/status")
        page.click("#global-state [data-act='retrystatus']")
        page.wait_for_function("() => !document.querySelector('#global-state [data-act=\"retrystatus\"]')")
        steps.append("verified /api/status failure is announced and can be retried")

        page.route("**/api/sync**", abort_api)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#global-state .failed [data-act='retrysync']", state="visible")
        page.unroute("**/api/sync**")
        page.click("#global-state [data-act='retrysync']")
        page.wait_for_function("() => !document.querySelector('#global-state [data-act=\"retrysync\"]')")
        steps.append("verified /api/sync failure is announced and can be retried")

        page.route("**/api/overview", abort_api)
        page.click('#nav .nav-item[data-act="go"][data-v="overview"]')
        page.wait_for_selector("#ov .failed [data-act='retryload']", state="visible")
        page.unroute("**/api/overview")
        page.click("#ov [data-act='retryload']")
        page.wait_for_selector("#ov .tiles", state="visible")
        steps.append("verified overview failure has an announced retry")

        # ---- keyboard-operated creator picker and Settings -------------------
        picker = page.locator("#o_creator").locator("xpath=..")
        picker_button = page.locator("#o_creator").locator("xpath=..//button[contains(@class,'pick-btn')]")
        picker_button.focus()
        page.keyboard.press("Enter")
        page.wait_for_selector(".pick.open .pick-opt", state="visible")
        page.keyboard.press("ArrowDown")
        check(page.locator(".pick.open .pick-opt:focus").count() == 1, "ArrowDown did not move focus within the picker options.")
        page.keyboard.press("Escape")
        check(
            page.locator(".pick.open").count() == 0 and picker_button.evaluate("e => e === document.activeElement"),
            "Escape did not close the picker and restore focus.",
        )
        steps.append("verified Enter, arrow-key navigation and Escape in the creator picker")
        page.locator("#foot .me-btn").click()
        page.wait_for_selector("#sform .set-layout", state="visible")
        page.wait_for_selector("#setbody .set-sec h2", state="visible")
        check("/settings" in page.url, f"Expected a settings URL, got {page.url}.")
        check(
            (page.locator("#setbody .set-sec h2").first.inner_text() or "").strip() == "AI", "The first settings panel is not the AI panel."
        )
        steps.append("opened Settings from the sidebar footer and checked its first panel")

        # ---- interaction 2: switch settings section --------------------------
        page.click("#sform .set-nav .set-link[data-k='apps']")
        page.wait_for_function(
            "() => { const h = document.querySelector('#setbody .set-sec h2'); return h && h.textContent.trim() === 'Connected apps'; }"
        )
        check(page.locator("#setbody .appcard").count() >= 1, "Connected apps cards did not render.")
        steps.append("clicked the Connected apps settings tab; its cards rendered")

        # ---- Usage failure and retry -----------------------------------------
        page.route("**/api/usage", abort_api)
        page.locator("#foot .ai-card").click()
        page.wait_for_selector("#usage .failed [data-act='retryload']", state="visible")
        page.unroute("**/api/usage")
        page.click("#usage [data-act='retryload']")
        page.wait_for_selector("#usage .tiles", state="visible")
        steps.append("verified AI usage failure has an announced retry")

        # ---- Escape closes notification dialog -------------------------------
        page.locator("#foot [data-act='bell']").click()
        page.wait_for_selector("#bellpop:not([hidden])", state="visible")
        page.keyboard.press("Escape")
        check(
            "open" not in (page.locator("#bellpop").get_attribute("class") or "").split(), "Escape did not close the notifications popover."
        )
        steps.append("verified Escape closes notifications")

        # ---- interaction 3: All videos in the sidebar ------------------------
        page.click('#nav .nav-item[data-act="go"][data-v="library"]')
        page.wait_for_selector("#view #addform.composer", state="visible")
        check(page.locator("#jobs").count() == 1, "The All videos job list was not rendered.")
        check((page.locator("#title span").inner_text() or "").strip() == "All videos", "The All videos header is not correct.")
        check(page.url.rstrip("/").endswith("/videos"), f"Expected a /videos URL, got {page.url}.")
        steps.append("clicked All videos in the sidebar; the video view rendered with no navigation error")

        # ---- errors ----------------------------------------------------------
        check(not page_errors, "Uncaught page errors: " + " | ".join(page_errors))
        check(not console_errors, "Console errors: " + " | ".join(console_errors))

    except Exception as exc:
        failure = exc
        if page_errors:
            print("Uncaught page errors: " + " | ".join(page_errors))
        if console_errors:
            print("Console errors: " + " | ".join(console_errors))
    finally:
        for closer in (
            lambda: context.close() if context else None,
            lambda: browser.close() if browser else None,
            lambda: pw_manager.__exit__(None, None, None) if pw_manager else None,
        ):
            try:
                closer()
            except Exception as exc:
                print(f"Warning while closing the browser: {exc}")
        stop_server(proc)
        if log_file is not None:
            log_file.close()
        if failure is not None:
            print_log(log_path)
        if temp is not None:
            temp.cleanup()

    if failure is None:
        print("\nPASS - the browser drove the real RONB Clips frontend.")
        for step in steps:
            print(f"  - {step}")
        if ignored:
            print("Ignored benign console messages:")
            for message in ignored:
                print(f"  - {message}")
        print("Summary: PASS")
        return 0

    print(f"\nFAIL: {failure}")
    if ignored:
        print("Ignored benign console messages:")
        for message in ignored:
            print(f"  - {message}")
    print("Summary: FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
