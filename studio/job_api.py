"""Video and clip routes: making clips, editing them, and reading them back."""

import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from . import account, auth, clip_api, ctx, jobs, live, media
from . import api_common as common
from .models import (
    BulkDeleteInput,
    ClipEditInput,
    ClipTextInput,
    DeletedCountsView,
    JobInput,
    JobView,
    OkView,
    SyncView,
    TaskStartedView,
)

router = APIRouter()


@router.get("/api/sync", response_model=SyncView, response_model_exclude_unset=True)
def sync(v: str = ""):
    return common.sync_response(v)


@router.get("/api/jobs", response_model=list[JobView], response_model_exclude_unset=True)
def list_jobs(limit: int = 100, offset: int = 0):
    limit, offset = common.bounds(limit, offset)
    items = jobs.list_jobs()
    return [jobs.public(j) for j in items[offset : offset + limit]]


@router.get("/api/jobs/{jid}", response_model=JobView, response_model_exclude_unset=True)
def get_job(jid: str):
    return jobs.public(common.job_or_404(jid))


@router.get("/api/preview", response_model=TaskStartedView, response_model_exclude_unset=True)
def link_preview(url: str = ""):
    url = url.strip()[:500]
    if not media.site_allowed(url):
        raise HTTPException(400, "Paste a full link to a YouTube, Twitch or Kick video.")
    try:
        auth.throttle(f"preview:{ctx.uid()}", 12, 60)
    except auth.AuthError as e:
        raise HTTPException(429, "Too many links at once. Wait a moment.") from e
    return common.lookup(media.preview, url)


@router.post("/api/jobs", response_model=JobView, response_model_exclude_unset=True)
def create_from_link(body: JobInput):
    from . import creators

    common.quota()
    common.throttle("newjob:" + ctx.uid(), 30, 600)
    url = body.url.strip()[:500]
    if not media.site_allowed(url):
        raise HTTPException(400, "Paste a full link to a YouTube, Twitch or Kick video.")
    try:
        media.check_youtube(url)  # YouTube is refusing us right now: say so, rather than queue a job that fails
    except media.YouTubeBlocked as e:
        raise HTTPException(503, str(e)) from e
    creator = body.creator or None
    if creator and not creators.get(creator):
        raise HTTPException(404, "No such creator")
    try:
        with common._create_lock:
            common.job_slots()
            job = jobs.create({"type": "url", "url": url, "name": url}, body.options, creator=creator)
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e) if isinstance(e, ValueError) else "Those options aren't valid.") from e
    seen = media._PREVIEWS.get(url)  # already looked up while the user was typing: show it straight away
    if seen:
        job.update(title=seen["title"], uploader=seen["uploader"], duration=seen["duration"])
        if media.save_thumb(jobs.job_dir(job["id"]), url=url):
            job["thumb"] = True
        jobs.save(job)
    return jobs.public(job)


@router.post("/api/jobs/{jid}/clips/{cid}/render", response_model=JobView, response_model_exclude_unset=True)
def rerender(jid: str, cid: str, body: ClipEditInput):
    return clip_api.rerender(jid, cid, body.model_dump(exclude_unset=True), common.job_or_404, common.throttle)


@router.put("/api/jobs/{jid}/clips/{cid}/text", response_model=JobView, response_model_exclude_unset=True)
def clip_text(jid: str, cid: str, body: ClipTextInput):
    """Save a clip's title, description and hashtags without re-rendering the video."""
    return clip_api.clip_text(jid, cid, body.model_dump(exclude_unset=True), common.job_or_404, common.throttle)


@router.post("/api/jobs/{jid}/clips/{cid}/write", response_model=TaskStartedView, response_model_exclude_unset=True)
def clip_write(jid: str, cid: str):
    """Ask the AI for fresh title, description and hashtags for a finished clip."""
    return clip_api.clip_write(jid, cid, common.job_or_404, common.throttle)


@router.delete("/api/jobs/{jid}", response_model=OkView, response_model_exclude_unset=True)
def delete_job(jid: str):
    job = common.job_or_404(jid)
    if job["status"] in ("queued", "running", "live") or jid in live.SESSIONS:
        raise HTTPException(409, "This is still being worked on. Stop it or wait for it to finish first.")
    jobs.delete(jid)
    return {"ok": True}


@router.delete("/api/jobs/{jid}/clips/{cid}", response_model=OkView, response_model_exclude_unset=True)
def delete_clip(jid: str, cid: str):
    common.job_or_404(jid)
    try:
        if not account.delete_clip(jid, cid):
            raise HTTPException(404, "No such clip")
    except account.Busy as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True}


@router.post("/api/bulk/delete", response_model=DeletedCountsView, response_model_exclude_unset=True)
def bulk_delete(body: BulkDeleteInput):
    common.throttle("bulkdel:" + ctx.uid(), 30, 600)
    videos = body.videos
    vids = [x for x in (videos if isinstance(videos, list) else [])[:300] if isinstance(x, str) and re.fullmatch(r"[\w\-]{8,60}", x)]
    return account.delete_many(vids, common.pairs(body.clips))


@router.get("/api/clips.zip")
def clips_zip(ids: str = ""):
    """Many clips as one download. `ids` is video.clip pairs separated by commas."""
    common.throttle("zip:" + ctx.uid(), 12, 600, "Give it a moment: you just downloaded several groups of clips.")
    common.quota()  # a ZIP is a full copy of those clips: it needs room like anything else
    pairs = common.pairs([p.split(".", 1) for p in ids.split(",") if "." in p][:300])
    try:
        f = account.zip_clips(pairs)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    from starlette.background import BackgroundTask

    return FileResponse(
        f,
        filename=f"ronbclips-clips-{len(pairs)}.zip",
        media_type="application/zip",
        headers={"Cache-Control": "private, no-store"},
        background=BackgroundTask(lambda: f.unlink(missing_ok=True)),
    )


@router.get("/media/{jid}/{name}")
def media_file(jid: str, name: str, download: int = 0):
    if not re.fullmatch(r"[\w\-]+\.(mp4|jpg)", name):
        raise HTTPException(404)
    f = Path(common.job_or_404(jid)["dir"]) / "clips" / name
    if not f.is_file():
        raise HTTPException(404)
    return FileResponse(f, filename=name if download else None, headers={"Cache-Control": "private, max-age=300"})
