"""Public and app page routes."""

import re
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response

from . import config, db, health
from .models import HealthDetailView, HealthView, OkView

router = APIRouter()
WEB = config.ROOT / "web"


def page(name: str):
    return FileResponse(WEB / name, headers={"Cache-Control": "private, no-cache"})


@router.get("/")
def home():
    return page("index.html")


@router.get("/robots.txt")
def robots():
    return Response(
        f"User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /oauth/\nDisallow: /media/\n\nSitemap: {config.PUBLIC_URL}/sitemap.xml\n",
        media_type="text/plain",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/sitemap.xml")
def sitemap():
    day = time.strftime("%Y-%m-%d")
    loc = f"{config.PUBLIC_URL}/"
    body = f"<url><loc>{loc}</loc><lastmod>{day}</lastmod><changefreq>weekly</changefreq><priority>1.0</priority></url>"
    return Response(
        '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + body + "</urlset>",
        media_type="application/xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/videos")
@router.get("/usage")
@router.get("/settings")
@router.get("/settings/{rest:path}")
@router.get("/creators")
@router.get("/creators/{rest:path}")
def app_page(rest: str = ""):
    return page("index.html")


@router.get("/healthz", response_model=OkView, response_model_exclude_unset=True)
def healthz():
    probe = db.ping()
    if probe["ok"]:
        return {"ok": True}
    return JSONResponse({"ok": False, "status": "degraded", "backend": probe["backend"], "error": probe["error"]}, 503)


@router.get("/livez", response_model=HealthView, response_model_exclude_unset=True)
def livez():
    return {"ok": True, "status": "alive", "shutting_down": health.shutting_down()}


@router.get("/readyz", response_model=HealthView, response_model_exclude_unset=True)
def readyz():
    probe = db.ping()
    if probe["ok"] and not health.shutting_down():
        return {"ok": True, "status": "ready"}
    detail = "Shutting down" if health.shutting_down() else "Database is not ready"
    return JSONResponse({"detail": detail, "ok": False, "status": "degraded", "database": probe["backend"], "error": probe["error"]}, 503)


@router.get("/health/detail", response_model=HealthDetailView, response_model_exclude_unset=True)
def health_detail():
    body, status = health.detail()
    return JSONResponse(body, status_code=status)


@router.get("/brand/{name}")
def brand(name: str):
    if not re.fullmatch(r"[\w\-]+\.(svg|png|ico|webp|jpg|mp4)", name):
        raise HTTPException(404)
    file = WEB / "brand" / name
    if not file.is_file():
        raise HTTPException(404)
    return FileResponse(
        file,
        headers={"Cache-Control": "public, max-age=604800, stale-while-revalidate=86400"},
    )


@router.get("/assets/{name}")
def asset(name: str):
    if not re.fullmatch(r"[\w\-]+\.(js|css)", name):
        raise HTTPException(404)
    file = WEB / "assets" / name
    if not file.is_file():
        raise HTTPException(404)
    media_type = "text/css" if name.endswith(".css") else "text/javascript"
    return FileResponse(
        file,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=86400, stale-while-revalidate=604800"},
    )


@router.get("/favicon.ico")
def favicon():
    return RedirectResponse("/brand/favicon.svg", 308)
