"""RONB Clips web server. Run with:  python -m studio.server

The root opens the local owner's app. Provider OAuth under /oauth connects posting accounts.
Everything under /api, /media and /oauth needs the local owner context.
The routes live in small files by subject; this file puts the app together and guards every request.
"""

import ipaddress
import os
import threading
import time
from urllib.parse import urlparse

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, RedirectResponse

from . import (
    api_common as common,
)
from . import (
    app_api,
    auth,
    clip_api,
    config,
    creator_api,
    ctx,
    db,
    health,
    job_api,
    jobs,
    live,
    logs,
    pages,
    posting,
    providers,
    render,
    settings,
    sync_api,
    tasks,
    transcribe,
    upload_api,
)
from . import tools as _tools
from .models import CreatorInput


def _quiet(fn):
    try:
        fn()
    except Exception as e:  # noqa: BLE001 - said again, in plain words, when a video needs it
        logs.emit("startup.task_failed", "error", msg=str(e))


app = FastAPI(
    title="RONB Clips",
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
    swagger_ui_parameters={"supportedSubmitMethods": []},
)
app.include_router(pages.router)
app.include_router(providers.router)
app.include_router(settings.router)
app.include_router(app_api.router)
app.include_router(job_api.router)
app.include_router(upload_api.router)
app.include_router(creator_api.router)

WEB = config.ROOT / "web"
OPEN = ("/brand/", "/assets/", "/healthz", "/livez", "/readyz", "/favicon.ico", "/robots.txt", "/sitemap.xml")
PAGES = {"/": "index.html", "/videos": "index.html", "/usage": "index.html", "/settings": "index.html", "/creators": "index.html"}
CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
    "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; "
    "frame-src https://player.twitch.tv https://www.youtube-nocookie.com https://player.kick.com; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self' https://accounts.google.com"
)


@app.on_event("startup")
async def _more_threads():
    import anyio.to_thread

    cpus = os.cpu_count() or 4
    default_threads = str(max(16, min(64, cpus * 4)))  # enough small requests without a huge thread pool on a small machine
    anyio.to_thread.current_default_thread_limiter().total_tokens = int(config.env("RONBCLIPS_REQUEST_THREADS", default_threads))


@app.on_event("startup")
def _startup():
    db.init()
    for f in (config.TMP_DIR / "uploads").glob("*"):  # uploads cut short by a restart
        try:
            f.unlink()
        except OSError:
            pass
    render.install_fonts()
    if _tools.missing():
        logs.emit("startup.tools_missing", "warning", msg="Some tools are missing.", help=_tools.HELP.strip()[:1000])
    if (
        not transcribe.REMOTE and config.env("RONBCLIPS_FETCH_MODEL", "1") != "0"
    ):  # the speech model: fetched now, in the background, if it was never downloaded
        from . import fetch_model

        threading.Thread(target=lambda: _quiet(fetch_model.ensure), daemon=True).start()
    jobs.start()
    live.start()
    posting.start()
    sync_api.start_background_refresh()
    from . import updates

    updates.start()  # looks for a newer RONB Clips every minute, in the background
    health.mark_started()
    logs.emit("server.ready", msg="RONB Clips is ready.")


@app.on_event("shutdown")
def _shutdown():
    health.mark_shutting_down()
    logs.emit("server.stopping", msg="RONB Clips is closing; queued work gets a chance to finish.")
    live.stop_all()
    posting.stop()
    report = jobs.stop(wait=True)
    tasks.stop(wait=True, timeout=5)
    report = report if isinstance(report, dict) else {}
    logs.emit(
        "server.stopped",
        msg="Shutdown finished.",
        drained=report.get("drained", 0),
        abandoned=report.get("abandoned", 0),
    )


# ================================================================ the gate: who is asking, and are they allowed
class Gate:
    """Runs before every request: finds the owner, blocks cross-site form posts, caps upload size and adds headers."""

    def __init__(self, app):
        self.inner = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.inner(scope, receive, send)
        req = Request(scope)
        request_id = logs.clean_request_id(req.headers.get("x-request-id") or "")
        rid_token = ctx._request_id.set(request_id)
        try:
            started = time.perf_counter()
            body_receive = receive  # replaced by a size-counting reader when the request has a body
            path, method = scope["path"], scope["method"]

            def log_http(status):
                logs.emit(
                    "http",
                    msg=f"{method} {path} -> {status}",
                    method=method,
                    path=path,
                    status=status,
                    ms=round((time.perf_counter() - started) * 1000, 1),
                )

            def add_security_headers(headers, status):
                """Every answer gets these, including a refusal from the gate before the app runs."""
                headers += [
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"strict-origin-when-cross-origin"),
                    (b"content-security-policy", CSP.encode()),
                    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
                ]
                if config.HTTPS:
                    headers.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
                if path.startswith("/api/"):
                    headers.append((b"cache-control", b"no-store"))
                elif status >= 300 and not any(k.lower() == b"cache-control" for k, _ in headers):
                    # redirects and errors are never kept by a CDN: a "not found" for a clip that is still being
                    # made, or a sign-in redirect, must not be shown to anyone later
                    headers.append((b"cache-control", b"private, no-store"))
                if path.startswith(("/api/", "/oauth/", "/media/", "/videos", "/usage", "/settings", "/creators")):
                    headers.append((b"x-robots-tag", b"noindex, nofollow"))

            async def deny(status, msg, redirect=None):
                no_keep = {"Cache-Control": "private, no-store"}  # never kept by a browser or CDN
                resp = (
                    RedirectResponse(redirect, 303, headers=no_keep) if redirect else JSONResponse({"detail": msg}, status, headers=no_keep)
                )
                resp.headers["x-request-id"] = request_id
                log_http(status)  # refused requests get an id and a log line too

                async def send_denied(message):
                    if message["type"] == "http.response.start":
                        add_security_headers(message.setdefault("headers", []), message.get("status", status))
                    await send(message)

                await resp(scope, receive, send_denied)

            if method not in ("GET", "HEAD", "OPTIONS"):
                if not _from_here(req):
                    return await deny(403, "Cross-site requests are not allowed.")
                # Only the upload may be big. Everything else is a small form, and is cut off at 256 KB whatever its
                # headers claim, so nobody can fill the server's memory by posting junk.
                piece = method == "PUT" and path.startswith("/api/upload/")
                cap = (
                    ((min(config.MAX_UPLOAD_MB, config.UPLOAD_PART_MB) if piece else config.MAX_UPLOAD_MB) + 8) * 1024 * 1024
                    if path == "/api/upload" or piece
                    else 256 * 1024
                )
                try:
                    if int(req.headers.get("content-length") or 0) > cap:
                        return await deny(
                            413,
                            f"That file is too big. The limit is {config.MAX_UPLOAD_MB} MB."
                            if path == "/api/upload"
                            else "That request is too large.",
                        )
                except ValueError:
                    return await deny(400, "Bad request.")
                seen, inner_receive = 0, receive

                async def counted():
                    nonlocal seen
                    m = await inner_receive()
                    if m["type"] == "http.request":
                        seen += len(m.get("body", b""))
                        if seen > cap:
                            raise HTTPException(413, "That request is too large.")
                    return m

                body_receive = counted

            is_open = path in PAGES or any(path.startswith(p) for p in OPEN)
            # The page must have been asked for by its real address (a number, "localhost", or the address in
            # settings), so another website cannot reach it through a made-up name pointing at this machine.
            if not _own_address(req.headers.get("host", "")):
                return await deny(403, "Open RONB Clips by its own address.")
            user = auth._local or await run_in_threadpool(auth.local_user)
            if not user and not is_open:
                return await deny(401, "Please open RONB Clips locally.")
            scope.setdefault("state", {})["user"] = user
            if user and path in ("/api/upload", "/api/upload/start") and method == "POST":
                # decided before a single byte of the file is accepted: a free slot and room on the disk
                def allowed():
                    with ctx.as_user(user.id, request_id=request_id):
                        common.job_slots()
                        common.quota()

                try:
                    await run_in_threadpool(allowed)
                except HTTPException as e:
                    return await deny(e.status_code, e.detail)
            if user and path.startswith("/api/") and not auth.seen_recently(user.id):
                await run_in_threadpool(auth.touch, user.id)

            unchanged = False

            async def send_with_headers(message):
                nonlocal unchanged
                if unchanged:  # the browser's copy is current: send no body at all
                    if message["type"] == "http.response.body" and not message.get("more_body"):
                        await send({"type": "http.response.body", "body": b""})
                    return
                if message["type"] == "http.response.start":
                    h = message.setdefault("headers", [])
                    h.append((b"x-request-id", request_id.encode("ascii", "replace")))
                    log_http(message.get("status", 200))
                    if method == "GET" and message.get("status") == 200:
                        tag = next((v.decode("latin-1") for k, v in h if k.lower() == b"etag"), "")
                        have = req.headers.get("if-none-match", "")
                        if tag and have and tag.removeprefix("W/") in [x.strip().removeprefix("W/") for x in have.split(",")]:
                            unchanged = True
                            message["status"] = 304
                            h[:] = [(k, v) for k, v in h if k.lower() not in (b"content-length", b"content-type", b"content-encoding")]
                    add_security_headers(h, message.get("status", 200))
                await send(message)

            token = ctx._uid.set(user.id if user else None)
            ip_token = ctx._ip.set(_visitor_ip(scope, req))
            try:
                if user and path == "/api/sync" and method == "GET":
                    # By far the most common request. When nothing of this user's changed it is answered right here:
                    # no worker thread, no database, no disk. Anything else falls through to the normal route.
                    quick = common.sync_unchanged(req.query_params.get("v", ""))
                    if quick is not None:
                        return await JSONResponse(quick)(scope, body_receive, send_with_headers)
                await self.inner(scope, body_receive, send_with_headers)
            finally:
                ctx._uid.reset(token)
                ctx._ip.reset(ip_token)
        finally:
            ctx._request_id.reset(rid_token)


app.add_middleware(Gate)


class Compress:
    """gzip for text responses. Videos and images are already compressed and need byte ranges, so they pass through."""

    def __init__(self, app):
        from starlette.middleware.gzip import GZipMiddleware

        self.plain, self.gzip = app, GZipMiddleware(app, minimum_size=800, compresslevel=6)

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "") if scope["type"] == "http" else ""
        media = path.startswith(("/media/", "/api/upload")) or path.endswith((".mp4", ".jpg", ".png", ".webp", ".ico"))
        await (self.plain if scope["type"] != "http" or media else self.gzip)(scope, receive, send)


app.add_middleware(Compress)


def _trust_proxy() -> bool:
    """RONBCLIPS_TRUST_PROXY is a yes/no switch. Writing "0" or "false" means no, not "on"."""
    v = (config.env("RONBCLIPS_TRUST_PROXY") or "").strip().lower()
    return v not in ("", "0", "false", "no", "off")


def _visitor_ip(scope, req) -> str:
    """The caller's address for rate limits. A header counts only from a configured proxy and only if it is an IP."""
    client = scope.get("client")
    peer = client[0] if client else ""
    allowed = {v.strip() for v in config.PROXY_IPS.split(",") if v.strip()}
    if _trust_proxy() and config.CLIENT_IP_HEADER and (peer in allowed or "*" in allowed):
        value = (req.headers.get(config.CLIENT_IP_HEADER.lower()) or "").split(",")[0].strip()
        try:
            return str(ipaddress.ip_address(value))  # ignore names, empty values and lists of spoofed addresses
        except ValueError:
            pass
    return peer


def _from_here(req, must_say=False) -> bool:
    """Did this change come from RONB Clips's own page, and not from another website?

    The browser's own answer is trusted first: it marks every request "same-origin" or not, and a web page cannot
    fake that mark. Comparing the page's address with the address the request arrived at is only the fallback,
    because that comparison goes wrong when something in between rewrites either of them (a proxy, an antivirus
    web shield, a browser extension), which made every save fail with "Cross-site requests are not allowed"."""
    if (req.headers.get("sec-fetch-site") or "").lower() == "same-origin":
        return True
    origin = (req.headers.get("origin") or "").rstrip("/").lower()
    if not origin:
        return not must_say
    return urlparse(origin).netloc == (req.headers.get("host") or "").lower() or origin == config.PUBLIC_URL.lower()


def _own_address(host: str) -> bool:
    """Is this the address RONB Clips is really reached by? A number (127.0.0.1, 192.168.1.20), "localhost", or the
    host in RONBCLIPS_PUBLIC_URL."""
    import ipaddress

    name = host.strip().lower()
    name = name[1 : name.index("]")] if name.startswith("[") and "]" in name else name.rsplit(":", 1)[0] if ":" in name else name
    if name in ("localhost", urlparse(config.PUBLIC_URL).hostname or "") or name.endswith(".localhost"):
        return True
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        return False


# The tests call a few route functions directly, without going through FastAPI. These shims keep their old shape.
def rerender(jid: str, cid: str, body: dict):
    return clip_api.rerender(jid, cid, body, common.job_or_404, common.throttle)


def clip_text(jid: str, cid: str, body: dict):
    return clip_api.clip_text(jid, cid, body, common.job_or_404, common.throttle)


def clip_write(jid: str, cid: str):
    return clip_api.clip_write(jid, cid, common.job_or_404, common.throttle)


def new_creator(body):
    return creator_api.new_creator(body if isinstance(body, CreatorInput) else CreatorInput.model_validate(body))


def main():
    logs.emit("server.address", msg=f"RONB Clips is running:  {config.PUBLIC_URL}", address=config.PUBLIC_URL)
    if config.HTTPS or config.HOST not in ("127.0.0.1", "localhost", "::1"):
        logs.emit(
            "server.warning",
            "warning",
            msg="There are no accounts: anyone who can reach this address can use RONB Clips and its AI key.",
        )
    if config.env("RONBCLIPS_AUTO_RELOAD") == "1":
        # restart by itself whenever the server code changes (pages in web/ never need a restart)
        uvicorn.run(
            "studio.server:app",
            host=config.HOST,
            port=config.PORT,
            log_level="warning",
            backlog=4096,
            reload=True,
            reload_dirs=[str(config.ROOT / "studio")],
            reload_includes=["*.py"],
            proxy_headers=_trust_proxy(),
            forwarded_allow_ips=config.PROXY_IPS if _trust_proxy() else "",
        )
        return
    uvicorn.run(
        app,
        host=config.HOST,
        port=config.PORT,
        log_level="warning",
        backlog=4096,
        proxy_headers=_trust_proxy(),
        forwarded_allow_ips=config.PROXY_IPS if _trust_proxy() else "",
    )


if __name__ == "__main__":
    main()
