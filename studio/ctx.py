"""Which user the current code is running for.

Web requests get it from the session cookie (set by the middleware in server.py). Background threads have no
request, so they set it themselves from the job, recording or post they are working on. Every module that
touches user data asks here, which is what keeps one user's creators, clips and accounts away from
another's."""

import collections
import contextlib
import contextvars

_uid: contextvars.ContextVar[str | None] = contextvars.ContextVar("ronbclips_uid", default=None)
# The visitor's address, taken from the socket or (only when a trusted proxy is configured) a header. Used to keep
# rate limits per visitor instead of putting everyone in the same local-owner bucket.
_ip: contextvars.ContextVar[str] = contextvars.ContextVar("ronbclips_ip", default="")
# The id of the web request that started this work, or a made-up id for background work. It is carried into worker
# threads by bind() so their log lines can be followed back to the request (or job) that produced them.
_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("ronbclips_request_id", default="")


class NoUser(RuntimeError):
    pass


def uid() -> str:
    u = _uid.get()
    if u is None:
        raise NoUser("No signed-in user in this context")
    return u


def uid_or_none():
    return _uid.get()


def ip() -> str:
    """The address of the caller in this request, or '' for background work."""
    return _ip.get() or ""


def request_id() -> str:
    """The id to put in structured log lines for this request or worker."""
    return _request_id.get() or ""


def set_uid(u, request_id="", ip=""):
    """For a thread that works for one user for its whole life."""
    _uid.set(u)
    if request_id:
        _request_id.set(request_id)
    if ip:
        _ip.set(ip)


@contextlib.contextmanager
def as_user(u, request_id="", ip=""):
    uid_token = _uid.set(u)
    request_token = _request_id.set(request_id) if request_id else None
    ip_token = _ip.set(ip) if ip else None
    try:
        yield
    finally:
        if ip_token is not None:
            _ip.reset(ip_token)
        if request_token is not None:
            _request_id.reset(request_token)
        _uid.reset(uid_token)


@contextlib.contextmanager
def as_context(uid=None, request_id="", ip=""):
    """Set the whole identity at once, including the log correlation id."""
    with as_user(uid, request_id=request_id, ip=ip):
        yield


def bind(fn):
    """Wrap fn so it runs with the current user and log id, even on another thread (threads do not inherit these)."""
    u, rid, visitor = _uid.get(), _request_id.get(), _ip.get()

    def run(*a, **k):
        uid_token = _uid.set(u)
        request_token = _request_id.set(rid)
        ip_token = _ip.set(visitor)
        try:
            return fn(*a, **k)
        finally:
            _ip.reset(ip_token)
            _request_id.reset(request_token)
            _uid.reset(uid_token)

    return run


# A number per user that goes up whenever something they can see changes (a job, a creator, an account).
# The page sends back the number it last saw; if it is the same, the server answers "nothing new" without
# touching the disk or building anything. With many people online this is what keeps the page checks cheap.
_ver = collections.defaultdict(int)


def bump(u=None):
    u = u or _uid.get()
    if u:
        _ver[u] += 1


def version(u=None) -> int:
    return _ver[u or uid()]
