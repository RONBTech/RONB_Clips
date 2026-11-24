"""Creator workspaces and live recording routes."""

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from . import account, alerts, auth, config, creators, ctx, jobs, live, media, profiles, tasks
from . import api_common as common
from .models import (
    ClipNowInput,
    CreatorInput,
    DeletedView,
    GoLiveInput,
    JobView,
    OkView,
    TaskStartedView,
)

router = APIRouter()


def channel_ok(body: dict):
    links = [str(body.get("channel") or "").strip()]
    chs = body.get("channels")
    if chs is not None and not isinstance(chs, dict):
        raise HTTPException(400, "Bad channel list.")
    for plat, url in (chs or {}).items():
        url = str(url or "").strip()
        if url and creators.platform_key(url) != plat:
            raise HTTPException(400, f"That link isn't a {plat.title()} channel.")
        links.append(url)
    for ch in links:
        if ch and not media.site_allowed(live.normalize(ch)):
            raise HTTPException(400, "Channel links must be from YouTube, Twitch or Kick.")


@router.delete("/api/creators/{cid}", response_model=DeletedView, response_model_exclude_unset=True)
def delete_creator(cid: str):
    common.throttle("cdel:" + ctx.uid(), 30, 600)
    try:
        r = account.delete_creator(cid)
    except account.Busy as e:
        raise HTTPException(409, str(e)) from e
    if not r.get("ok"):
        raise HTTPException(404, "No such creator")
    return r


@router.get("/api/channel-preview", response_model=TaskStartedView, response_model_exclude_unset=True)
def channel_preview(url: str = ""):
    url = url.strip()[:300]
    if not url or not media.site_allowed(live.normalize(url)):
        raise HTTPException(400, "Paste a YouTube, Twitch or Kick channel link.")
    try:
        auth.throttle(f"chanprev:{ctx.uid()}", 12, 60)
    except auth.AuthError as e:
        raise HTTPException(429, "Too many lookups at once. Wait a moment.") from e
    return common.lookup(profiles.preview, url)


@router.get("/api/creators/{cid}/avatar")
def creator_avatar(cid: str, p: str = ""):
    c = creators.get(cid)
    if not c:
        raise HTTPException(404)
    key = p if p in creators.PLATFORMS else creators.platform_key(c["channel"])
    prof = (c.get("profiles") or {}).get(key) or {}
    f = creators.root(cid) / f"avatar-{key}.img"
    if not f.is_file() or prof.get("pic") not in ("image/jpeg", "image/png", "image/webp"):
        raise HTTPException(404)
    return FileResponse(f, media_type=prof["pic"], headers={"Cache-Control": "private, max-age=86400"})


@router.get("/api/creators", response_model=list[dict[str, Any]], response_model_exclude_unset=True)
def list_creators(limit: int = 100, offset: int = 0):
    limit, offset = common.bounds(limit, offset)
    rows = common.creator_rows()
    return rows[offset : offset + limit]


@router.post("/api/creators", response_model=dict[str, Any], response_model_exclude_unset=True)
def new_creator(body: CreatorInput):
    data = body.model_dump(exclude_unset=True)
    channel_ok(data)
    if len(creators.all_creators()) >= config.MAX_CREATORS:
        raise HTTPException(400, f"This server allows {config.MAX_CREATORS} creators per account.")
    try:
        c = creators.create(data)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if c.get("channels"):
        profiles.refresh(c["id"], force=True)
    return c


@router.put("/api/creators/{cid}", response_model=dict[str, Any], response_model_exclude_unset=True)
def edit_creator(cid: str, body: CreatorInput):
    data = body.model_dump(exclude_unset=True)
    common.throttle("cedit:" + ctx.uid(), 60, 600)
    channel_ok(data)
    try:
        c = creators.update(cid, data)
    except KeyError as e:
        raise HTTPException(404, "No such creator") from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    s = live.active_for(cid)
    if s:
        s.settings = c  # new settings apply to the running recording straight away
    if "channels" in data or "channel" in data:
        profiles.refresh(cid, force=True)
    if data.get("auto_watch") and c.get("auto_watch") and not s:
        live.check_now(cid)  # if they are live right now, recording starts within seconds
    return c


@router.post("/api/creators/{cid}/live", response_model=JobView, response_model_exclude_unset=True)
def go_live(cid: str, body: GoLiveInput = GoLiveInput()):
    try:
        c = creators.get(cid)
    except ValueError:
        c = None
    if not c:
        raise HTTPException(404, "No such creator")
    # the stream the user is watching, else where they are live right now, else the channel chosen in Setup
    on_air = [p["channel"] for k, p in (c.get("profiles") or {}).items() if p.get("live") and c["channels"].get(k) == p.get("channel")]
    asked = body.channel
    url = asked if asked in c["channels"].values() else c["channel"] if c["channel"] in on_air or not on_air else on_air[0]
    try:
        return jobs.public(live.start_session(c, url))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/api/live/{jid}/stop", response_model=OkView, response_model_exclude_unset=True)
def stop_live(jid: str):
    s = live.mine(jid)
    if not s:
        raise HTTPException(404, "This recording has already ended")
    s.stop()
    return {"ok": True}


@router.post("/api/live/{jid}/clip", response_model=OkView, response_model_exclude_unset=True)
def clip_now(jid: str, body: ClipNowInput):
    s = live.mine(jid)
    if not s or s.ended:
        raise HTTPException(404, "This recording has already ended")
    common.throttle("clipnow:" + ctx.uid(), 6, 60, "Give it a moment: your last clips are still being made.")
    if len(s.manual) >= 3:
        raise HTTPException(429, "Give it a moment: your last clips are still being made.")
    try:
        s.clip_last(max(5, min(180, float(body.seconds))))
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True}


def _send_test_alert():
    """The webhook itself; any failure comes back through the task's error field."""
    problems = alerts.send("RONB Clips: test message. Alerts are working.")
    if problems:
        raise RuntimeError("; ".join(problems))
    return {"ok": True}


@router.post("/api/alerts/test", response_model=TaskStartedView, response_model_exclude_unset=True)
def test_alert():
    if not alerts.configured():
        raise HTTPException(400, "Save a Discord webhook or Telegram details first.")
    problem = None
    try:
        auth.throttle("alert:" + ctx.uid(), 10, 600)
    except auth.AuthError as e:
        problem = str(e)
    if problem:
        raise HTTPException(429, problem)
    return {"id": tasks.submit("Send test alert", _send_test_alert)}
