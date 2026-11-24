"""Clip text, re-render and AI rewrite endpoints."""

from fastapi import HTTPException

from . import brain, ctx, jobs, tasks, usage


def rerender(jid: str, cid: str, body: dict, job_or_404, throttle):
    job = job_or_404(jid)
    clip = next((c for c in job["clips"] if c["id"] == cid), None)
    if not clip:
        raise HTTPException(404, "No such clip")
    if job["status"] in ("queued", "running") or clip.get("status") in ("queued", "rendering"):
        raise HTTPException(409, "This clip is still being made. Try again when it has finished.")
    throttle("render:" + ctx.uid(), 20, 600, "That's a lot of re-makes in a short time. Wait a few minutes.")
    try:
        jobs.request_render(jid, cid, body)
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))
    return jobs.public(job)


def clip_text(jid: str, cid: str, body: dict, job_or_404, throttle):
    job = job_or_404(jid)
    clip = next((c for c in job["clips"] if c["id"] == cid), None)
    if not clip:
        raise HTTPException(404, "No such clip")
    throttle("cliptext:" + ctx.uid(), 120, 600)
    try:
        jobs.save_post_text(job, clip, body)
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))
    return jobs.public(job)


def clip_write(jid: str, cid: str, job_or_404, throttle):
    job = job_or_404(jid)
    clip = next((c for c in job["clips"] if c["id"] == cid), None)
    if not clip:
        raise HTTPException(404, "No such clip")
    throttle("clipwrite:" + ctx.uid(), 20, 600, "That's a lot of rewrites in a short time. Wait a few minutes.")
    usage.set_context(job.get("creator"), job.get("title") or "")
    return {"id": tasks.submit("Write post text", _write_post_text, job, clip)}


def _write_post_text(job, clip):
    """The AI call itself; its answer or error is read through /api/tasks/{id}."""
    return brain.post_text(jobs.spoken_in(job, clip), job.get("title") or "", job.get("uploader") or "", game=job.get("game") or None)
