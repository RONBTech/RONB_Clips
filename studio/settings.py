"""Storage and local-settings routes."""

from fastapi import APIRouter, HTTPException, Request

from . import safe, storage
from .models import OkView, PathView, StorageInput, StorageView

router = APIRouter()


def on_this_computer(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in ("127.0.0.1", "::1", "localhost")


@router.get("/api/storage", response_model=StorageView, response_model_exclude_unset=True)
def storage_get(request: Request):
    return storage.info(on_this_computer(request))


@router.post("/api/storage/open", response_model=OkView, response_model_exclude_unset=True)
def storage_open(request: Request):
    if not on_this_computer(request):
        raise HTTPException(400, "The folder can only be opened on the computer RONB Clips runs on.")
    try:
        storage.open_folder()
    except OSError as error:
        raise HTTPException(500, "Couldn't open the folder: " + safe.friendly(error, 200)) from error
    return {"ok": True}


@router.post("/api/storage/pick", response_model=PathView, response_model_exclude_unset=True)
def storage_pick(request: Request):
    if not on_this_computer(request):
        raise HTTPException(
            400,
            "Type the folder's path instead: a folder window can only be shown on the computer RONB Clips runs on.",
        )
    return {"path": storage.pick_folder()}


@router.put("/api/storage", response_model=StorageView, response_model_exclude_unset=True)
def storage_put(request: Request, body: StorageInput):
    try:
        if body.cancel:
            storage.cancel_change()
            return storage.info(on_this_computer(request))
        storage.change(body.path, body.move)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return storage.info(on_this_computer(request))
