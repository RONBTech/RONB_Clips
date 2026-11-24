"""Dashboard-wide routes: status, feedback, notifications and background tasks."""

import re

from fastapi import APIRouter, HTTPException

from . import api_common as common
from . import auth, config, ctx, feedback, load, notices, render, tasks, updates, usage
from . import tools as _tools
from .models import FeedbackInput, IdView, NoticesView, OkView, StatusView, TaskView

router = APIRouter()


@router.get("/api/status", response_model=StatusView, response_model_exclude_unset=True)
def status():
    from . import accounts

    return {
        "user": auth.public(auth.local_user()),
        "update": updates.latest(),
        "disk_full": load.disk_full(),
        "no_ffmpeg": bool(_tools.missing()),
        "links": {
            "repo": config.REPO_URL,
            "follow": config.FOLLOW_URL,
            "socials": {"instagram": config.FOLLOW_URL, "facebook": config.FACEBOOK_URL},
        },
        "ai": usage.brief(),
        "unread": notices.unread(),
        "tour": not accounts.settings()["tour_done"],
        "youtube": accounts.youtube_ready(),
        "connect": {p: accounts.can_connect(p) for p in accounts.PLATFORM_NAMES},
        "max_upload_mb": config.MAX_UPLOAD_MB,
        "upload_part_mb": config.UPLOAD_PART_MB,
        "max_batch": config.MAX_ACTIVE_JOBS_PER_USER,
        "styles": {k: v["label"] for k, v in render.STYLES.items()},
        "layouts": render.LAYOUTS,
    }


@router.post("/api/feedback", response_model=IdView, response_model_exclude_unset=True)
def send_feedback(body: FeedbackInput):
    try:
        auth.throttle("feedback:" + ctx.uid(), 10, 3600)
        return feedback.submit(ctx.uid(), body.kind, body.message, body.page, body.context)
    except auth.AuthError as e:
        raise HTTPException(429, "You've sent a lot of messages in the last hour. Try again a bit later.") from e
    except feedback.Refused as e:
        raise HTTPException(400, str(e)) from e


@router.get("/api/notifications", response_model=NoticesView, response_model_exclude_unset=True)
def notifications_list(limit: int = 40, offset: int = 0):
    limit, offset = common.bounds(limit, offset, 200)
    return notices.mine(limit, offset)


@router.get("/api/tasks/{tid}", response_model=TaskView, response_model_exclude_unset=True)
def task_status(tid: str):
    """State of a long running background call, for a page that does not want to wait for it."""
    if not re.fullmatch(r"[0-9a-f]{32}", tid):
        raise HTTPException(404, "No such task")
    return tasks.get(tid)


@router.post("/api/notifications/read", response_model=OkView, response_model_exclude_unset=True)
def notifications_read():
    notices.read_all()
    return {"ok": True}
