"""Upload routes. The files themselves are handled by uploads.py."""

import json

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import ValidationError

from . import api_common as common
from . import ctx, uploads
from .models import JobView, OkView, UploadOptions, UploadPartView, UploadStartInput, UploadStartView

router = APIRouter()


def typed_options(raw: str) -> dict:
    """Turn the JSON string a file upload sends into checked clip options."""
    try:
        data = json.loads(raw or "{}")
        if not isinstance(data, dict):
            raise ValueError("options must be an object")
        return UploadOptions.model_validate(data).model_dump(exclude_unset=True)
    except (ValueError, ValidationError) as error:
        raise HTTPException(400, "Those options aren't valid.") from error


@router.post("/api/upload", response_model=JobView, response_model_exclude_unset=True)
def create_from_file(file: UploadFile = File(...), options: str = Form("{}"), creator: str = Form("")):
    common.throttle("upfile:" + ctx.uid(), 30, 600)
    return uploads.create_file(file, typed_options(options), creator, lambda: None, common.job_slots)


@router.post("/api/upload/start", response_model=UploadStartView, response_model_exclude_unset=True)
def upload_start(body: UploadStartInput):
    return uploads.start(body.model_dump(), lambda: None, common.job_slots, common.quota, common.throttle)


@router.put("/api/upload/{upid}/{n}", response_model=UploadPartView, response_model_exclude_unset=True)
async def upload_part(request: Request, upid: str, n: int):
    common.throttle("uppart:" + ctx.uid(), 200, 600)
    return await uploads.part(request, upid, n)


@router.post("/api/upload/{upid}/finish", response_model=JobView, response_model_exclude_unset=True)
def upload_finish(upid: str):
    common.throttle("upfinish:" + ctx.uid(), 60, 600)
    return uploads.finish(upid, lambda: None, common.job_slots, common.forget_disk)


@router.delete("/api/upload/{upid}", response_model=OkView, response_model_exclude_unset=True)
def upload_cancel(upid: str):
    common.throttle("upcancel:" + ctx.uid(), 60, 600)
    return uploads.cancel(upid)
