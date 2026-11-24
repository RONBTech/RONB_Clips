"""AI, provider OAuth and connected-account routes."""

import re
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, RedirectResponse

from . import accounts, ai, auth, creators, ctx, jobs, posting, safe, tasks, usage
from . import api_common as common
from .models import (
    AccountView,
    AIModelsInput,
    AISettingsInput,
    AISettingsView,
    AppInput,
    AppView,
    OkView,
    OverviewView,
    PostInput,
    QueuedView,
    SettingsInput,
    SettingsView,
    TaskStartedView,
    UsageView,
)

router = APIRouter()


def throttle(key: str, limit: int, window: int, message: str = "Too many requests at once. Wait a moment."):
    try:
        auth.throttle(key, limit, window)
    except auth.AuthError as error:
        raise HTTPException(429, str(error) if str(error).startswith("Too many") else message) from error


@router.get("/api/usage", response_model=UsageView, response_model_exclude_unset=True)
def ai_usage(limit: int = 60, offset: int = 0):
    limit, offset = common.bounds(limit, offset, 200)
    return usage.summary({creator["id"]: creator["name"] for creator in creators.all_creators()}, limit, offset)


@router.get("/api/ai", response_model=AISettingsView, response_model_exclude_unset=True)
def ai_get():
    return ai.public()


@router.put("/api/ai", response_model=AISettingsView, response_model_exclude_unset=True)
def ai_put(body: AISettingsInput):
    throttle("aisave:" + ctx.uid(), 20, 600)
    try:
        key = body.key
        out = ai.save(
            body.provider,
            body.models,
            body.base_url,
            None if key is None or (isinstance(key, str) and not key.strip() and not body.remove_key) else str(key),
        )
    except (ValueError, TypeError) as error:
        raise HTTPException(400, str(error)) from error
    usage.forget_all()
    ctx.bump()
    return out


def _list_models(provider, base_url, key):
    """Fetch the model list; the task's result is the same {"models": [...]} the page expects."""
    try:
        return {"models": ai.list_models(provider, base_url, key)}
    except ValueError:
        raise
    except Exception as error:  # noqa: BLE001
        reason = ai.explain(error)
        raise RuntimeError(
            f"The service did not give its list of models. {reason}"
            if reason
            else "The service did not give its list of models. Check the key or enter the model manually."
        ) from error


def _test_ai():
    try:
        return ai.test()
    except ValueError:
        raise
    except Exception as error:  # noqa: BLE001
        raise RuntimeError(ai.explain(error) or "The AI did not answer.") from error


@router.post("/api/ai/models", response_model=TaskStartedView, response_model_exclude_unset=True)
def ai_models(body: AIModelsInput = AIModelsInput()):
    throttle("aimodels:" + ctx.uid(), 30, 600)
    return {"id": tasks.submit("List models", _list_models, body.provider, body.base_url, body.key)}


@router.post("/api/ai/test", response_model=TaskStartedView, response_model_exclude_unset=True)
def ai_test():
    throttle("aitest:" + ctx.uid(), 20, 600)
    return {"id": tasks.submit("Test AI", _test_ai)}


@router.get("/api/overview", response_model=OverviewView, response_model_exclude_unset=True)
def overview():
    return posting.overview()


@router.get("/api/settings", response_model=SettingsView, response_model_exclude_unset=True)
def get_settings():
    return accounts.public_settings()


@router.put("/api/settings", response_model=SettingsView, response_model_exclude_unset=True)
def put_settings(body: SettingsInput):
    try:
        accounts.save_settings(body.model_dump(exclude_unset=True))
    except (ValueError, TypeError) as error:
        raise HTTPException(400, str(error)) from error
    return accounts.public_settings()


@router.get("/api/accounts", response_model=list[AccountView], response_model_exclude_unset=True)
def list_accounts():
    return accounts.list_accounts()


@router.delete("/api/accounts/{account_id}", response_model=OkView, response_model_exclude_unset=True)
def remove_account(account_id: str):
    accounts.remove(account_id)
    return {"ok": True}


def back(**query):
    return RedirectResponse("/settings/apps?" + urlencode(query), 303)


@router.get("/oauth/{platform}/start")
def connect_start(platform: str):
    throttle("connectstart:" + ctx.uid(), 20, 600)
    if platform not in accounts.PLATFORM_NAMES:
        raise HTTPException(404)
    try:
        return RedirectResponse(accounts.connect_url(platform), 303)
    except ValueError as error:
        return back(connect_error=str(error))


@router.get("/oauth/{platform}/callback")
def connect_callback(
    platform: str,
    code: str = "",
    state: str = "",
    error: str = "",
    error_description: str = "",
    error_reason: str = "",
):
    if platform not in accounts.PLATFORM_NAMES:
        raise HTTPException(404)
    name = accounts.PLATFORM_NAMES[platform]
    if error or not code:
        why = (
            "you cancelled"
            if error in ("access_denied",) or error_reason == "user_denied"
            else (error_description or error or "no sign-in code came back")
        )
        return back(connect_error=f"{name} was not connected: {why[:160]}.")
    try:
        throttle("connect:" + ctx.uid(), 30, 600)
    except HTTPException:
        raise
    return back(task=tasks.submit("Connect account", _connect_account, platform, code, state, name), platform=platform)


def _connect_account(platform: str, code: str, state: str, name: str) -> dict:
    """Trade the sign-in code for an account; the page polls the task and then shows the result."""
    try:
        account = accounts.connect_finish(platform, code, state)
    except Exception as provider_error:  # noqa: BLE001
        raise RuntimeError(f"{name} was not connected: {safe.friendly(provider_error, 240)}") from provider_error
    return {"connected": platform, "name": account["name"]}


@router.get("/api/apps", response_model=dict[str, AppView], response_model_exclude_unset=True)
def apps_get():
    return accounts.apps_public()


@router.put("/api/apps", response_model=dict[str, AppView], response_model_exclude_unset=True)
def apps_put(body: AppInput):
    throttle("appsave:" + ctx.uid(), 20, 600)
    try:
        return accounts.save_app(body.platform, body.client_id, body.client_secret)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@router.post("/api/posts", response_model=QueuedView, response_model_exclude_unset=True)
def add_posts(body: PostInput):
    job = jobs.get(body.job)
    clip = (
        next(
            (item for item in job["clips"] if item["id"] == body.clip),
            None,
        )
        if job
        else None
    )
    if not clip or clip.get("status") != "done":
        raise HTTPException(404, "That clip is not ready")
    made = posting.enqueue(job, clip, body.accounts[:20], now=body.now)
    if not made:
        raise HTTPException(400, "Nothing was queued. Pick at least one account this clip has not already gone to.")
    return {"queued": len(made)}


@router.post("/api/posts/{post_id}/{action}", response_model=OkView, response_model_exclude_unset=True)
def post_action(post_id: str, action: str):
    try:
        {"cancel": posting.cancel, "retry": posting.retry}[action](post_id)
    except KeyError as error:
        raise HTTPException(404) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return {"ok": True}


@router.get("/pub/{token}.mp4")
def public_clip(token: str):
    throttle("pubclip:" + ctx.uid(), 300, 600, "Too many clip views. Try again in a few minutes.")
    got = accounts.read_clip_link(token)
    if not got:
        raise HTTPException(404)
    uid, job_id, name = got
    with ctx.as_user(uid):
        job = jobs.get(job_id) if re.fullmatch(r"[\w\-]{8,60}", job_id) else None
        file = Path(job["dir"]) / "clips" / name if job else None
    if not file or not file.is_file():
        raise HTTPException(404)
    return FileResponse(file, media_type="video/mp4", headers={"Cache-Control": "private, no-store"})
