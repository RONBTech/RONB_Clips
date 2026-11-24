"""Long calls to outside services, run off the request threads.

A provider can take minutes to answer: listing models, testing a key, looking up a site, rewriting post text or
sending a webhook. Running those on a web request holds a thread open and makes the page look stuck. Here each call
is handed to a small pool of background threads, with the user's identity carried across. The route answers with a
task id straight away and the page asks /api/tasks/{id} for the result or the error.
"""

import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ALL_COMPLETED, Future, ThreadPoolExecutor
from concurrent.futures import wait as wait_for_futures

from . import config, ctx, logs

_pool = ThreadPoolExecutor(max_workers=max(2, min(8, (config.JOB_WORKERS or 4) // 2)), thread_name_prefix="provider")
_tasks: dict[str, dict] = {}
_lock = threading.Lock()
MAX_TASKS = 500  # keep old finished answers for a while, but never let the list grow without end


def submit(name: str, fn: Callable, *a, **k) -> str:
    """Start a long call in the background and return its id."""
    tid = uuid.uuid4().hex
    with _lock:
        _tasks[tid] = {"id": tid, "name": name, "status": "running", "result": None, "error": "", "started": time.time()}
        while len(_tasks) > MAX_TASKS:
            old, old_item = next(iter(_tasks.items()))
            if old == tid:  # MAX_TASKS is at least one; keep the new task even if someone changes it at runtime
                break
            _tasks.pop(old, None)
            old_future = old_item.get("future")
            if isinstance(old_future, Future):
                old_future.cancel()  # an evicted queued call never starts; a running one simply has no reader
    origin = ctx.request_id() or logs.background_id("task", tid)
    bound = ctx.bind(fn) if ctx.uid_or_none() else fn

    def work():
        with ctx.as_context(ctx.uid_or_none(), request_id=origin):
            logs.emit("task.started", msg="Long call started.", task_id=tid, name=name)
            try:
                out = bound(*a, **k)
            except Exception as e:  # noqa: BLE001 - the caller sees the same error through the status route
                logs.emit("task.failed", "error", msg="Long call failed.", task_id=tid, name=name, error=str(e)[:200])
                with _lock:
                    item = _tasks.get(tid)
                    if item:
                        item.update(status="error", error=str(e), finished=time.time())
                raise
            with _lock:
                item = _tasks.get(tid)
                if item:
                    item.update(status="done", result=out, finished=time.time())
            logs.emit("task.finished", msg="Long call finished.", task_id=tid, name=name)
            return out

    future = _pool.submit(work)
    with _lock:
        if tid in _tasks:
            _tasks[tid]["future"] = future
    return tid


def get(tid: str) -> dict:
    """Read the state of a task started with submit()."""
    with _lock:
        item = _tasks.get(tid)
        if not item:
            return {"id": tid, "status": "missing", "result": None, "error": "No such task"}
        out = {key: value for key, value in item.items() if key != "future"}
        future = item.get("future")
    if isinstance(future, Future) and future.done() and out["status"] == "running":
        try:
            out["result"] = future.result()
            out["status"] = "done"
        except Exception as e:  # noqa: BLE001
            out.update(status="error", error=str(e))
    return out


def stop(wait: bool = True, timeout: float | None = None):
    """Let in-flight provider calls finish, but never hold up shutdown forever."""
    if not wait:
        _pool.shutdown(wait=False, cancel_futures=True)
        return
    if timeout is None:
        _pool.shutdown(wait=True)
        return
    with _lock:
        futures = [item["future"] for item in _tasks.values() if isinstance(item.get("future"), Future)]
    wait_for_futures(futures, timeout=timeout, return_when=ALL_COMPLETED)
    _pool.shutdown(wait=False, cancel_futures=True)
