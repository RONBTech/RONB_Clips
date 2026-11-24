"""Chunked upload lifecycle. Pieces stay isolated until finish joins them into a video."""

import json
import os
import re
import shutil
import threading
import time
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from . import config, creators, ctx, jobs

UP_ID = re.compile(r"[0-9a-f]{24}")
lock = threading.Lock()
reserved_lock = threading.Lock()
reserved: dict = {}
create_lock = threading.Lock()


def root() -> Path:
    d = config.TMP_DIR / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def reserve(uid: str, size: int):
    with reserved_lock:
        promised = reserved.get(uid, 0)
        used = 0
        for root, _dirs, files in os.walk(config.user_dir(uid)):
            for name in files:
                try:
                    used += os.path.getsize(os.path.join(root, name))
                except OSError:
                    pass
        if used + promised + size > config.MAX_USER_GB * 1e9:
            raise HTTPException(507, "There isn't room for this file in your storage. Delete some older videos to make room.")
        reserved[uid] = promised + size


def release(uid: str, size: int):
    with reserved_lock:
        left = max(0, reserved.get(uid, 0) - size)
        if left:
            reserved[uid] = left
        else:
            reserved.pop(uid, None)


def clear_reserved(uid: str, size: int):
    release(uid, size)


def sweep():
    for d in root().glob("up-*"):
        try:
            if d.is_dir() and time.time() - d.stat().st_mtime > 6 * 3600:
                try:
                    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    meta = {}
                shutil.rmtree(d, ignore_errors=True)
                if meta.get("user"):
                    release(meta["user"], int(meta.get("size") or 0))
        except OSError:
            pass


def get(upid: str) -> dict:
    if not UP_ID.fullmatch(upid or ""):
        raise HTTPException(404, "No such upload")
    try:
        meta = json.loads((root() / f"up-{upid}" / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise HTTPException(404, "This upload has expired. Start it again.")
    if meta.get("user") != ctx.uid():
        raise HTTPException(404, "No such upload")
    return meta


def start(body: dict, need_verified, job_slots, quota, throttle):
    need_verified()
    throttle("upstart:" + ctx.uid(), 30, 600)  # before quota() walks the whole disk
    job_slots()
    quota()
    name = re.sub(r"[^\w.\- ]", "_", Path(str(body.get("name") or "video.mp4")).name)[:120]
    ext = Path(name).suffix.lower()
    if ext not in {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".ts", ".flv"}:
        raise HTTPException(400, "Upload a video file (mp4, mov, mkv or webm).")
    size = body.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise HTTPException(400, "Bad file size.")
    if size > config.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"That file is too big. The limit is {config.MAX_UPLOAD_MB} MB.")
    creator = str(body.get("creator") or "")
    if creator and not creators.get(creator):
        raise HTTPException(404, "No such creator")
    opts = body.get("options") if isinstance(body.get("options"), dict) else {}
    try:
        jobs.clean_options(opts)
    except (ValueError, TypeError):
        raise HTTPException(400, "Those options aren't valid.")
    sweep()
    mine = 0
    for d in root().glob("up-*"):
        try:
            mine += json.loads((d / "meta.json").read_text(encoding="utf-8")).get("user") == ctx.uid()
        except (OSError, ValueError):
            pass
    if mine >= config.MAX_ACTIVE_JOBS_PER_USER:
        raise HTTPException(429, f"You already have {mine} uploads in progress. Let one finish first.")
    part = config.UPLOAD_PART_MB * 1024 * 1024
    upid = os.urandom(12).hex()
    d = root() / f"up-{upid}"
    reserve(ctx.uid(), size)
    meta = {
        "user": ctx.uid(),
        "name": name,
        "ext": ext,
        "size": size,
        "part": part,
        "parts": -(-size // part),
        "options": opts,
        "creator": creator,
        "t": time.time(),
    }
    try:
        d.mkdir()
        (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    except OSError:
        release(ctx.uid(), size)
        shutil.rmtree(d, ignore_errors=True)
        raise HTTPException(507, "There isn't room to start this upload. Delete some older videos and try again.")
    return {"id": upid, "part": part, "parts": meta["parts"]}


def create_file(file, options: dict, creator: str, need_verified, job_slots):
    need_verified()
    job_slots()
    name = re.sub(r"[^\w.\- ]", "_", Path(file.filename or "video.mp4").name)[:120]
    ext = Path(name).suffix.lower()
    if ext not in {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".ts", ".flv"}:
        raise HTTPException(400, "Upload a video file (mp4, mov, mkv or webm).")
    if creator and not creators.get(creator):
        raise HTTPException(404, "No such creator")
    opts = options if isinstance(options, dict) else {}
    updir = config.TMP_DIR / "uploads"
    updir.mkdir(exist_ok=True)
    tmp = updir / f"{os.urandom(8).hex()}{ext}"
    limit, size = config.MAX_UPLOAD_MB * 1024 * 1024, 0
    try:
        with tmp.open("wb") as out:
            while chunk := file.file.read(4 * 1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"That file is too big. The limit is {config.MAX_UPLOAD_MB} MB.")
                out.write(chunk)
        try:
            with create_lock:
                job_slots()
                job = jobs.create({"type": "file", "path": str(tmp), "name": name}, opts, enqueue=False, creator=creator or None)
        except (ValueError, TypeError):
            raise HTTPException(400, "Those options aren't valid.")
        dest = jobs.job_dir(job["id"]) / ("source" + ext)
        shutil.move(str(tmp), dest)
    finally:
        tmp.unlink(missing_ok=True)
    job["source"]["path"] = str(dest)
    jobs.enqueue(job)
    return jobs.public(job)


async def part(request: Request, upid: str, n: int):
    meta = await run_in_threadpool(get, upid)
    if not 0 <= n < meta["parts"]:
        raise HTTPException(400, "No such piece.")
    want = meta["part"] if n < meta["parts"] - 1 else meta["size"] - meta["part"] * (meta["parts"] - 1)
    d = root() / f"up-{upid}"
    tmp = d / f"{n:05d}.{os.urandom(4).hex()}.tmp"
    got = 0
    try:
        with tmp.open("wb") as f:
            async for chunk in request.stream():
                got += len(chunk)
                if got > want:
                    raise HTTPException(400, "That piece is the wrong size.")
                await run_in_threadpool(f.write, chunk)
        if got != want:
            raise HTTPException(400, "That piece is the wrong size.")
        tmp.replace(d / f"{n:05d}.part")
    finally:
        tmp.unlink(missing_ok=True)
    return {"ok": True, "n": n}


def finish(upid: str, need_verified, job_slots, clear_disk):
    meta = get(upid)
    need_verified()
    d = root() / f"up-{upid}"
    with lock:
        if not (d / "meta.json").is_file():
            raise HTTPException(404, "This upload has expired. Start it again.")
        parts = [d / f"{i:05d}.part" for i in range(meta["parts"])]
        missing = [i for i, p in enumerate(parts) if not p.is_file()]
        if missing:
            raise HTTPException(409, f"Piece {missing[0] + 1} of {meta['parts']} has not arrived yet.")
        if sum(p.stat().st_size for p in parts) != meta["size"]:
            raise HTTPException(400, "The pieces do not add up to the file.")
        try:
            with create_lock:
                job_slots()
                job = jobs.create(
                    {"type": "file", "path": "", "name": meta["name"]}, meta["options"], enqueue=False, creator=meta["creator"] or None
                )
        except (ValueError, TypeError):
            raise HTTPException(400, "Those options aren't valid.")
        dest = jobs.job_dir(job["id"]) / ("source" + meta["ext"])
        try:
            with dest.open("wb") as out:
                for p in parts:
                    with p.open("rb") as src:
                        shutil.copyfileobj(src, out, 4 * 1024 * 1024)
        except OSError:
            jobs.delete(job["id"])
            shutil.rmtree(d, ignore_errors=True)
            release(ctx.uid(), meta["size"])
            raise HTTPException(507, "There wasn't room to save this file. Delete some older videos and try again.")
        shutil.rmtree(d, ignore_errors=True)
    release(ctx.uid(), meta["size"])
    clear_disk(ctx.uid())
    job["source"]["path"] = str(dest)
    jobs.enqueue(job)
    return jobs.public(job)


def cancel(upid: str):
    meta = get(upid)
    shutil.rmtree(root() / f"up-{upid}", ignore_errors=True)
    release(ctx.uid(), int(meta.get("size") or 0))
    return {"ok": True}
