"""Typed shapes for the forms the dashboard sends, and for the jobs and clips it works with.

Forms are checked by Pydantic at the door. Jobs and clips are Pydantic records while the app is running; JSON is
only the way they are stored between restarts. The records also behave like dictionaries, so the old call sites
can keep reading and updating them without losing the type checking.
"""

import copy
from collections.abc import MutableMapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------- forms the dashboard sends
class CreatorInput(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str = Field(min_length=1, max_length=120)
    channel: str = Field(default="", max_length=300)
    channels: dict[str, str] | None = None
    privacy: Literal["public", "unlisted", "private"] = "public"
    autopost: bool = False
    accounts: list[str] = Field(default_factory=list)
    clip_length: float | None = None


class AISettingsInput(BaseModel):
    provider: str
    models: str | list[str]
    base_url: str = ""
    key: str | None = None
    remove_key: bool = False


class AIModelsInput(BaseModel):
    provider: str = ""
    base_url: str = ""
    key: str | None = None


class StorageInput(BaseModel):
    path: str = ""
    move: bool = True
    cancel: bool = False


class PostInput(BaseModel):
    job: str
    clip: str
    accounts: list[str] = Field(default_factory=list)
    now: bool = False


class JobInput(BaseModel):
    """Paste a link to make clips. Options are checked again by jobs.clean_options."""

    model_config = ConfigDict(extra="allow")
    url: str = ""
    creator: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)


class UploadStartInput(BaseModel):
    """The first step of a piece-by-piece upload."""

    name: str = "video.mp4"
    size: int
    creator: str = ""
    options: dict[str, Any] = Field(default_factory=dict)


class ClipEditInput(BaseModel):
    """Re-cut or restyle a clip. Values stay loose here because the clip code explains what is wrong with them."""

    model_config = ConfigDict(extra="allow")
    start: Any = None
    end: Any = None
    captions: Any = None
    hook: Any = None
    title: Any = None
    description: Any = None
    style: Any = None
    layout: Any = None
    hashtags: Any = None


class ClipTextInput(BaseModel):
    """Save a finished clip's words without rendering it again."""

    model_config = ConfigDict(extra="allow")
    title: Any = None
    description: Any = None
    hashtags: Any = None


class BulkDeleteInput(BaseModel):
    videos: Any = None
    clips: Any = None


class FeedbackInput(BaseModel):
    kind: str = ""
    message: str = ""
    page: str = ""
    context: str = ""


class GoLiveInput(BaseModel):
    channel: str = ""


class ClipNowInput(BaseModel):
    seconds: Any = 30


class SettingsInput(BaseModel):
    """The settings form. These are the only keys the app stores; unknown ones are ignored."""

    discord_webhook: str = ""
    telegram_token: str = ""
    telegram_chat: str = ""
    paused: bool = False
    digest_hour: int = 8
    last_digest: str = ""
    tour_done: bool = False


class UploadOptions(BaseModel):
    """Clip options for a direct upload, sent as a JSON form field. The clip code checks the values again."""

    max_clips: int | None = None
    min_len: int | None = None
    max_len: int | None = None
    style: str | None = None
    layout: str | None = None
    quality: int | None = None
    range_start: float | None = None
    range_end: float | None = None
    captions: bool | None = None


class AppInput(BaseModel):
    platform: str = ""
    client_id: str = ""
    client_secret: str = ""


# ---------------------------------------------------------------- jobs and clips while the app is running
class _Record(BaseModel, MutableMapping):
    """A typed record that can still be used like the dicts it replaced.

    Only fields that were actually set are written to JSON and sent to the page, so old job files and the exact
    response shapes stay the same. Unknown fields from an older job file are kept and written back untouched.
    """

    model_config = ConfigDict(extra="allow")

    def as_dict(self) -> dict[str, Any]:
        """Plain JSON-ready data, with nested clips converted too."""
        return self.model_dump(exclude_unset=True)

    def __getitem__(self, key: str) -> Any:
        if key not in self.model_fields_set and key not in (self.model_extra or {}):
            raise KeyError(key)  # behave like the dict it replaced, so setdefault() still writes missing fields
        return getattr(self, key)

    def __setitem__(self, key: str, value: Any):
        setattr(self, key, value)

    def __delitem__(self, key: str):
        extra = self.model_extra
        if key not in self.model_fields_set and (extra is None or key not in extra):
            raise KeyError(key)
        if extra is not None and key in extra:
            del extra[key]
        else:
            delattr(self, key)  # Pydantic leaves the field in model_fields_set, so remove it ourselves
        self.model_fields_set.discard(key)

    def __iter__(self):
        return iter(self.as_dict())

    def __len__(self) -> int:
        return len(self.as_dict())

    def __deepcopy__(self, memo=None):
        data = copy.deepcopy(self.as_dict(), memo) if memo is not None else copy.deepcopy(self.as_dict())
        return self.__class__.model_validate(data)


class ClipRecord(_Record):
    """One moment cut from a video. Extra fields from old clip files are kept."""

    id: str = ""
    start: float = 0
    end: float = 0
    score: float = 0
    category: str = ""
    hook: str = ""
    title: str = ""
    description: str = ""
    hashtags: list[str] = Field(default_factory=list)
    highlight_words: list[str] = Field(default_factory=list)
    reason: str = ""
    style: str = ""
    layout: str = ""
    captions: bool = False
    status: str = ""
    version: int = 0
    file: str = ""
    cover: str = ""
    src: str = ""
    src_offset: float = 0
    src_end: float = 0
    fixed_words: list[dict[str, Any]] = Field(default_factory=list)
    fixed_for: list[Any] = Field(default_factory=list)
    posts: dict[str, Any] = Field(default_factory=dict)
    facecam: Any = None
    game: str = ""
    made_at: float = 0
    error: Any = None
    hashtags_in_description: bool = False
    fixed_description: str = ""


class _ClipList(list):
    """A clip list that turns plain dicts from the AI into typed records as they are added."""

    def __init__(self, values=()):
        super().__init__()
        self.extend(values)  # list's own constructor would skip the conversions below

    @staticmethod
    def _one(value):
        return value if isinstance(value, ClipRecord) else ClipRecord.model_validate(value)

    def append(self, value):
        super().append(self._one(value))

    def insert(self, index, value):
        super().insert(index, self._one(value))

    def extend(self, values):
        super().extend(self._one(value) for value in values)

    def __setitem__(self, key, value):
        if isinstance(key, slice):
            value = [self._one(item) for item in value]
        else:
            value = self._one(value)
        super().__setitem__(key, value)


class JobRecord(_Record):
    """A video and everything known about it. Extra fields from old job files are kept."""

    id: str = ""
    user: str = ""
    kind: str = "video"
    creator: str | None = None
    dir: str = ""
    created: float = 0
    source: dict[str, Any] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)
    status: str = ""
    stage: str = ""
    progress: float = 0
    title: str = ""
    uploader: str = ""
    duration: float = 0
    clips: list[ClipRecord] = Field(default_factory=_ClipList)
    error: Any = None
    ai: dict[str, Any] = Field(default_factory=dict)
    width: int = 0
    height: int = 0
    lang: str = ""
    thumb: bool = False
    game: str = ""
    resume: bool = False
    remake: list[str] = Field(default_factory=list)
    done_blocks: int = 0
    source_path: str = ""
    recover: bool = False
    was_live: bool = False
    transcript_from: str = ""
    took: float = 0
    word_count: int = 0
    facecam: Any = None

    @field_validator("clips", mode="after")
    @classmethod
    def _typed_clips(cls, value):
        return value if isinstance(value, _ClipList) else _ClipList(value)

    def __setitem__(self, key: str, value: Any):
        if key == "clips" and not isinstance(value, _ClipList):
            value = _ClipList(value)
        super().__setitem__(key, value)


# The rest of the app still imports these two names; they are the typed records now, not bare dictionaries.
Clip = ClipRecord
Job = JobRecord


# ---------------------------------------------------------------- what the browser reads back
# These are the shapes of the JSON answers. Routes ask FastAPI to check them with response_model=. Fields that a
# job or clip may not have yet have defaults; routes use response_model_exclude_unset=True so an answer still only
# contains the keys the app actually put in it.
class AiBriefView(BaseModel):
    ready: bool = False
    provider: str = ""
    model: str = ""
    today_calls: int = 0
    today_tokens: int = 0
    problem: str = ""


class AiJobView(BaseModel):
    tokens: int = 0
    note: str = ""
    used: bool = False


class AiProviderView(BaseModel):
    id: str
    label: str
    address: str
    needs_key: bool
    key_url: str
    example: str


class AISettingsView(BaseModel):
    provider: str = ""
    models: list[str] = Field(default_factory=list)
    base_url: str = ""
    has_key: bool = False
    key_hint: str = ""
    ready: bool = False
    label: str = ""
    saved_here: bool = False
    providers: list[AiProviderView] = Field(default_factory=list)


class ClipView(BaseModel):
    id: str
    start: float = 0
    end: float = 0
    score: float = 0
    category: str = ""
    hook: str = ""
    title: str = ""
    description: str = ""
    hashtags: list[str] = Field(default_factory=list)
    highlight_words: list[str] = Field(default_factory=list)
    reason: str = ""
    style: str = ""
    layout: str = ""
    captions: bool = False
    status: str = ""
    version: int = 0
    file: str = ""
    cover: str = ""
    src_offset: float = 0
    src_end: float = 0
    posts: dict[str, Any] = Field(default_factory=dict)
    facecam: Any = None
    game: str = ""
    made_at: float = 0
    error: str | None = None


class JobView(BaseModel):
    id: str
    kind: str = ""
    creator: str | None = None
    created: float = 0
    source: dict[str, Any] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)
    status: str = ""
    stage: str = ""
    progress: float = 0
    title: str = ""
    uploader: str = ""
    duration: float = 0
    clips: list[ClipView] = Field(default_factory=list)
    error: str | None = None
    ai: AiJobView = Field(default_factory=AiJobView)
    width: int = 0
    height: int = 0
    lang: str = ""
    thumb: bool = False
    game: str = ""
    resume: bool = False
    remake: list[str] = Field(default_factory=list)
    done_blocks: int = 0


class OwnerView(BaseModel):
    id: str
    name: str


class SocialView(BaseModel):
    instagram: str = ""
    facebook: str = ""


class LinksView(BaseModel):
    repo: str = ""
    follow: str = ""
    socials: SocialView = Field(default_factory=SocialView)


class StatusView(BaseModel):
    user: OwnerView
    update: dict[str, Any] | None = None
    disk_full: bool = False
    no_ffmpeg: bool = False
    links: LinksView = Field(default_factory=LinksView)
    ai: AiBriefView = Field(default_factory=AiBriefView)
    unread: int = 0
    tour: bool = False
    youtube: bool = False
    connect: dict[str, bool] = Field(default_factory=dict)
    max_upload_mb: int = 0
    upload_part_mb: int = 0
    max_batch: int = 0
    styles: dict[str, str] = Field(default_factory=dict)
    layouts: dict[str, str] = Field(default_factory=dict)


class ModelsView(BaseModel):
    models: list[str] = Field(default_factory=list)


class QueuedView(BaseModel):
    queued: int


class OkView(BaseModel):
    ok: bool = True


class HealthView(BaseModel):
    ok: bool = True
    status: str = ""
    shutting_down: bool = False


class HealthDetailView(BaseModel):
    ok: bool = True
    status: str = ""
    database: str = ""
    db: dict[str, Any] = Field(default_factory=dict)
    lifecycle: dict[str, Any] = Field(default_factory=dict)
    version: str = ""
    host: str = ""
    port: int = 0
    mode: str = ""
    speech_model: bool = False
    load: dict[str, Any] = Field(default_factory=dict)
    jobs: dict[str, int] = Field(default_factory=dict)
    workers: dict[str, Any] = Field(default_factory=dict)


class IdView(BaseModel):
    id: int


class PathView(BaseModel):
    path: str


class NoticeView(BaseModel):
    id: int
    t: float
    kind: str = ""
    title: str = ""
    body: str = ""
    link: str = ""
    read: bool = False


class NoticesView(BaseModel):
    unread: int = 0
    items: list[NoticeView] = Field(default_factory=list)


class TaskStartedView(BaseModel):
    """A long call has been handed to the background pool. Ask /api/tasks/{id} for its answer."""

    id: str


class TaskView(BaseModel):
    id: str
    name: str = ""
    status: str
    result: Any = None
    error: str = ""
    started: float = 0
    finished: float | None = None


class AITestView(BaseModel):
    ok: bool = True
    model: str = ""
    tokens: int = 0
    answer: str = ""


class PostTextView(BaseModel):
    title: str = ""
    description: str = ""
    hashtags: list[str] = Field(default_factory=list)


class AppView(BaseModel):
    ready: bool = False
    id_label: str = ""
    secret_label: str = ""
    id_hint: str = ""
    redirect: str = ""
    https: bool = False
    from_dashboard: bool = False


class AccountView(BaseModel):
    id: str
    platform: str
    label: str
    name: str
    added: float
    detail: str = ""


class SettingsView(BaseModel):
    # Secret settings are returned as true/false, never as the value itself.
    discord_webhook: str | bool = ""
    telegram_token: str | bool = ""
    telegram_chat: str = ""
    paused: bool = False
    digest_hour: int = 8
    last_digest: str = ""
    tour_done: bool = False


class StorageView(BaseModel):
    path: str
    drive: str = ""
    free_gb: float = 0
    total_gb: float = 0
    used_gb: float = 0
    min_free_gb: float = 0
    full: bool = False
    fixed: bool = False
    pending: str = ""
    local: bool = False


class PreviewView(BaseModel):
    title: str = ""
    uploader: str = ""
    duration: float = 0
    is_live: bool = False
    thumb: str = ""


class ChannelPreviewView(BaseModel):
    platform: str = ""
    name: str = ""
    followers: int = 0
    pic: str = ""


class UploadStartView(BaseModel):
    id: str
    part: int
    parts: int


class UploadPartView(BaseModel):
    ok: bool = True
    n: int


class DeletedView(BaseModel):
    ok: bool = True
    videos: int | None = None


class DeletedCountsView(BaseModel):
    videos: int = 0
    clips: int = 0
    skipped: int = 0


class UsageView(AiBriefView):
    calls_total: int = 0
    tokens_total: int = 0
    daily: list[dict[str, Any]] = Field(default_factory=list)
    by_purpose: list[list[Any]] = Field(default_factory=list)
    by_creator: list[list[Any]] = Field(default_factory=list)
    recent: list[dict[str, Any]] = Field(default_factory=list)


class OverviewView(BaseModel):
    paused: bool = False
    creators: list[dict[str, Any]] = Field(default_factory=list)
    posts: list[dict[str, Any]] = Field(default_factory=list)
    daily: list[dict[str, Any]] = Field(default_factory=list)
    alerts: bool = False
    recording: int = 0


class SyncView(BaseModel):
    v: str
    jobs: list[JobView] = Field(default_factory=list)
    creators: list[dict[str, Any]] = Field(default_factory=list)
    accounts: list[AccountView] = Field(default_factory=list)
    ai: AiBriefView = Field(default_factory=AiBriefView)
    unread: int = 0
    jobs_full: bool = False
