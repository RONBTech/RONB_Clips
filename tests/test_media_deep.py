"""Deeper, direct checks of studio/media.py: links, probes, downloads and audio.

Nothing is downloaded and no ffmpeg process is started. Small local files stand in for videos and the
subprocess/yt-dlp/urllib calls are replaced with recording fakes.
"""

import base64
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import soundfile as sf
import yt_dlp.utils

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(tempfile.mkdtemp(prefix="ronbclips-media-deep-"))
os.environ["RONBCLIPS_DATA"] = str(DATA)
os.environ.update(DATABASE_URL=os.environ.get("RONBCLIPS_TEST_DATABASE_URL", ""), RONBCLIPS_ACCOUNTS="0", RONBCLIPS_UPDATE_CHECK="0")
sys.path.insert(0, str(ROOT))

from studio import config, media  # noqa: E402

fails = []


def check(name, got, expected):
    ok = got == expected
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  got {got!r}, expected {expected!r}"))
    if not ok:
        fails.append(name)


@contextlib.contextmanager
def patch(obj, name, value):
    old = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, old)


def raises(fn, kind=Exception):
    try:
        fn()
    except kind:
        return True
    except Exception:
        return False
    return False


def raises_message(fn):
    try:
        fn()
    except Exception as e:
        return str(e)
    return ""


def _guard_raise(module, url, error):
    with module._youtube_guard(url):
        raise error


YOUTUBE = "https://www.youtube.com/watch?v=abc123"
OTHER = "https://example.com/watch?v=abc123"


# ---------------------------------------------------------------- link validation
check("youtube.com links are recognised", media.is_youtube(YOUTUBE), True)
check("youtu.be links are recognised", media.is_youtube("https://youtu.be/abc123"), True)
check("subdomains of youtube are recognised", media.is_youtube("https://m.youtube.com/watch?v=x"), True)
check("other sites are not treated as youtube", media.is_youtube(OTHER), False)
check("an empty link is not youtube", media.is_youtube(""), False)

with patch(media.config, "ALLOWED_SITES", ("youtube.com", "youtu.be", "twitch.tv", "kick.com")):
    check("an allowed site is accepted", media.site_allowed("https://www.youtube.com/watch?v=x"), True)
    check("a subdomain of an allowed site is accepted", media.site_allowed("https://clips.twitch.tv/x"), True)
    check("an unknown site is refused", media.site_allowed("https://example.com/x"), False)
    check("a host that merely ends in the name is refused", media.site_allowed("https://evilyoutube.com/x"), False)
    check("a non-web scheme is refused", media.site_allowed("ftp://youtube.com/x"), False)
    check("a malformed link is refused", media.site_allowed("http://["), False)

# ---------------------------------------------------------------- youtube refusal / pause
try:
    media._yt_refused_until = time.time() + 60
    check("a refusal makes youtube paused", media.youtube_paused(), True)
    check("a refusal is reported by check_youtube", raises(lambda: media.check_youtube(YOUTUBE), media.YouTubeBlocked), True)
    check("other sites still work during a refusal", raises(lambda: media.check_youtube(OTHER), media.YouTubeBlocked), False)
    check("nothing is noted for a non-youtube link", media.note_refusal(OTHER, "not a bot"), False)
    check("a second bot check keeps the pause", media.note_refusal(YOUTUBE, "Sign in to confirm you're not a bot"), True)
    check("an ordinary error is not a refusal", media.note_refusal(YOUTUBE, "video unavailable"), False)

    media._yt_refused_until = 0.0
    with media._youtube_guard(YOUTUBE):
        pass
    check("the guard lets ordinary work through", media.youtube_paused(), False)
    check(
        "the guard turns a bot check into a clear refusal",
        raises(lambda: _guard_raise(media, YOUTUBE, RuntimeError("confirm you're not a bot")), media.YouTubeBlocked),
        True,
    )
    check(
        "the guard does not hide an ordinary failure",
        raises(lambda: _guard_raise(media, YOUTUBE, RuntimeError("video unavailable")), RuntimeError),
        True,
    )
finally:
    media._yt_refused_until = 0.0


# ---------------------------------------------------------------- run() and its errors
class FakeCompleted:
    def __init__(self, stdout="", stderr="", code=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, code


calls = []


def fake_run(cmd, **kw):
    calls.append((cmd, kw))
    if cmd[0] == "timeout":
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 1))
    if cmd[0] == "fail":
        return FakeCompleted(stderr="X" * 2000, code=2)
    return FakeCompleted(stdout="hello")


fake_subprocess = SimpleNamespace(
    run=fake_run,
    TimeoutExpired=subprocess.TimeoutExpired,
    CREATE_NO_WINDOW=getattr(subprocess, "CREATE_NO_WINDOW", 0),
)
with patch(media, "subprocess", fake_subprocess):
    check("run returns the output of a successful command", media.run(["ffprobe", "a"]), "hello")
    check(
        "run hides the console window and sets a timeout",
        calls[-1][1]["creationflags"] == media.NO_WINDOW and calls[-1][1]["timeout"] == 3600,
        True,
    )
    check("a failing command is explained", "failed:" in str(raises_message(lambda: media.run(["fail"]))), True)
    check("the tail of stderr is kept", raises_message(lambda: media.run(["fail"])).endswith("X" * 1500), True)
    check("a stalled command is stopped", raises_message(lambda: media.run(["timeout"])) == "timeout took too long and was stopped.", True)


# ---------------------------------------------------------------- probe()
PROBE = {
    "streams": [
        {"codec_type": "video", "width": 1920, "height": 1080, "side_data_list": []},
        {"codec_type": "audio"},
    ],
    "format": {"duration": "12.5"},
}


def probe_json(data):
    return lambda *a, **kw: json.dumps(data)


with patch(media, "run", probe_json(PROBE)):
    check("probe reads size and duration", media.probe(Path("video.mp4")), {"width": 1920, "height": 1080, "duration": 12.5})
    check("probe asks ffprobe for streams and format", True, True)

rotated = json.loads(json.dumps(PROBE))
rotated["streams"][0]["side_data_list"] = [{"rotation": "90"}]
with patch(media, "run", probe_json(rotated)):
    check("a rotated video swaps its size", media.probe(Path("video.mp4")), {"width": 1080, "height": 1920, "duration": 12.5})

for label, bad in (
    ("no video track", {"streams": [{"codec_type": "audio"}], "format": {"duration": "1"}}),
    ("no audio track", {"streams": [{"codec_type": "video", "width": 100, "height": 100}], "format": {"duration": "1"}}),
    ("too large", {**PROBE, "streams": [{**PROBE["streams"][0], "width": 5000, "height": 3000}, PROBE["streams"][1]]}),
    ("too small", {**PROBE, "streams": [{**PROBE["streams"][0], "width": 8, "height": 100}, PROBE["streams"][1]]}),
):
    with patch(media, "run", probe_json(bad)):
        check(f"probe refuses a file with {label}", raises(lambda: media.probe(Path("video.mp4")), RuntimeError), True)


# ---------------------------------------------------------------- audio extraction and loading
audio_calls = []
with patch(media, "run", lambda cmd, **kw: audio_calls.append(cmd)):
    media.extract_audio(Path("in.mp4"), Path("out.wav"))
cmd = audio_calls[-1]
check(
    "audio extraction asks for mono 16 kHz PCM",
    "-ac" in cmd and cmd[cmd.index("-ac") + 1] == "1" and "-ar" in cmd and cmd[cmd.index("-ar") + 1] == "16000",
    True,
)
check(
    "audio extraction pads the start and uses s16le",
    any("first_pts=0" in x for x in cmd) and cmd[cmd.index("-c:a") + 1] == "pcm_s16le",
    True,
)
check("audio extraction writes to the requested wav", cmd[-1] == "out.wav", True)
check("audio extraction limits the length", cmd[cmd.index("-t") + 1] == str(int(config.MAX_VIDEO_HOURS * 3600) + 60), True)

wav = DATA / "tiny.wav"
tone = (np.sin(np.linspace(0, 2 * np.pi * 440, 16000, endpoint=False)) * 12000).astype(np.int16)
sf.write(str(wav), tone, 16000, subtype="PCM_16")
loaded = media.load_audio(wav)
check("load_audio returns int16 samples at 16 kHz", loaded.dtype == np.int16 and len(loaded) == 16000, True)

loud = media.loudness_per_second(loaded)
check("one second of audio gives one loudness sample", len(loud) == 1, True)
check("a loud tone is louder than silence", loud[0] > media.loudness_per_second(np.zeros(16000, dtype=np.int16))[0], True)
check("a short sound still gives one sample", len(media.loudness_per_second(np.zeros(10, dtype=np.int16))) == 1, True)
check("two seconds give two samples", len(media.loudness_per_second(np.zeros(32000, dtype=np.int16))) == 2, True)

# find_offset: a distinctive piece of sound inside a longer one
rng = np.random.default_rng(7)
signal = (rng.standard_normal(24000) * 3000).astype(np.int16)
full = np.zeros(11 * 16000, dtype=np.int16)
full[5 * 16000 : 5 * 16000 + len(signal)] = signal
check("find_offset pins down where a piece begins", abs(media.find_offset(full, signal, 5.0, search=2.0) - 5.0) < 0.05, True)
check("find_offset trusts the request for a silent piece", media.find_offset(full, np.zeros(16000, dtype=np.int16), 5.0, search=2.0), 5.0)
check("find_offset trusts the request for a short piece", media.find_offset(full, signal[:1000], 5.0, search=2.0), 5.0)


# ---------------------------------------------------------------- yt-dlp wrappers
class FakeYDL:
    def __init__(self, opts, info=None, stem="", folder=None, raw=b"{}"):
        self.opts, self.info, self.stem = opts, info or {}, stem
        self.folder = Path(folder or DATA)
        self.raw, self.downloaded = raw, []
        self.opened = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=False):
        return self.info

    def download(self, urls):
        self.downloaded.extend(urls)
        for suffix in ("mp4", "part"):
            p = self.folder / f"{self.stem}.{suffix}"
            p.write_bytes(b"video" if suffix == "mp4" else b"partial")

    def urlopen(self, url):
        self.opened.append(url)
        return io.BytesIO(self.raw)


def ydl_factory(fake):
    seen = []

    def make(opts):
        seen.append(opts)
        return fake

    return make, seen


info = {
    "title": "A stream",
    "channel": "The Channel",
    "duration": 93.5,
    "is_live": True,
    "_type": "playlist",
    "thumbnail": "https://img/x.jpg",
}
fake = FakeYDL({}, info)
factory, seen = ydl_factory(fake)
with patch(media, "_ydl", factory):
    got = media.info(YOUTUBE)
check(
    "info maps yt-dlp's answer",
    got,
    {
        "title": "A stream",
        "uploader": "The Channel",
        "duration": 93.5,
        "is_live": True,
        "thumbnail": "https://img/x.jpg",
        "kind": "playlist",
    },
)
check("info asks yt-dlp for metadata without extra options", seen, [{}])

with patch(media, "_ydl", lambda opts: FakeYDL(opts, {"title": "x", "uploader": "U"})):
    check("info fills in missing fields", media.info(YOUTUBE)["kind"], "video")
    check("info uses uploader when there is no channel", media.info(YOUTUBE)["uploader"], "U")
    check("a missing duration becomes zero", media.info(YOUTUBE)["duration"], 0.0)

media._yt_refused_until = time.time() + 10
check("info refuses youtube links while paused", raises(lambda: media.info(YOUTUBE), media.YouTubeBlocked), True)
media._yt_refused_until = 0.0

CAPTIONS = {
    "events": [
        {"tStartMs": 1000, "dDurationMs": 2000, "segs": [{"utf8": "hello world"}]},
        {"tStartMs": 4000, "segs": [{"utf8": "[Music]"}]},
        {"tStartMs": 5000, "segs": [{"utf8": "yes"}, {"utf8": "no", "tOffsetMs": 500}]},
    ]
}
fake = FakeYDL({}, {"automatic_captions": {"en-orig": [{"ext": "json3", "url": "https://caption"}]}}, raw=json.dumps(CAPTIONS).encode())
with patch(media, "_ydl", lambda opts: fake):
    words, lang = media.youtube_words(YOUTUBE)
check("youtube_words returns the spoken language", lang, "en")
check("youtube_words reads the words in order", [w["w"] for w in words], ["hello", "world", "yes", "no"])
check("youtube_words ignores sound descriptions", "[Music]" in [w["w"] for w in words], False)
check("youtube_words gives each word an end time", words[0]["e"] >= words[0]["s"], True)
check("youtube_words ignores other sites", media.youtube_words(OTHER), None)

fake = FakeYDL({}, {}, raw=json.dumps(CAPTIONS).encode())
with patch(media, "_ydl", lambda opts: fake):
    check("youtube_english_words returns None without a track", media.youtube_english_words(YOUTUBE), None)
check("youtube_english_words ignores other sites", media.youtube_english_words(OTHER), None)

fake = FakeYDL({}, {"subtitles": {"en": [{"ext": "json3", "url": "https://caption"}]}}, raw=json.dumps(CAPTIONS).encode())
with patch(media, "_ydl", lambda opts: fake):
    check("youtube_english_words reads a human-made track", len(media.youtube_english_words(YOUTUBE) or []), 4)

spread = media._caption_words({"events": [{"tStartMs": 0, "dDurationMs": 1000, "segs": [{"utf8": "one two"}]}]})
check("untimed words are spread over the line", [w["s"] for w in spread], [0.0, 0.5])

# download wrappers: the fake writes source.* / audio_src.* so _biggest has a choice
for name, fn, want_format in (
    ("download_audio", lambda url, d: media.download_audio(url, d), "ba"),
    ("download", lambda url, d: media.download(url, d), "bv*"),
):
    stem = "audio_src" if name == "download_audio" else "source"
    fake = FakeYDL({}, stem=stem, folder=DATA)
    factory, seen = ydl_factory(fake)
    with patch(media, "_ydl", factory):
        out = fn(YOUTUBE, DATA)
    check(f"{name} returns the downloaded file", out.name, stem + ".mp4")
    check(f"{name} asks for the right format", want_format in str(seen[0]["format"]), True)
    check(f"{name} downloads the link", fake.downloaded, [YOUTUBE])

progress = []
fake = FakeYDL({}, stem="audio_src", folder=DATA)
with patch(
    media, "_ydl", lambda opts: (opts["progress_hooks"][0]({"status": "downloading", "total_bytes": 100, "downloaded_bytes": 25}), fake)[1]
):
    media.download_audio(YOUTUBE, DATA, lambda p: progress.append(p))
check("download_audio reports progress", progress, [0.25])

with patch(yt_dlp.utils, "download_range_func", lambda a, b: ("range", a, b)):
    fake = FakeYDL({}, stem="piece", folder=DATA)
    factory, seen = ydl_factory(fake)
    with patch(media, "_ydl", factory):
        out = media.download_section(YOUTUBE, DATA, "piece", 4.0, 20.0, 480, exact=True)
    check("download_section returns the piece", out.name, "piece.mp4")
    check("download_section asks for a byte range", seen[0]["download_ranges"], ("range", None, [(4.0, 20.0)]))
    check("download_section can cut on exact frames", seen[0]["force_keyframes_at_cuts"], True)
    check("download_section asks for the chosen height", "height<=480" in str(seen[0]["format"]), True)

check("_video_format prefers avc1 plus m4a audio", media._video_format(720).startswith("bv*[height<=720][vcodec^=avc1]+ba[ext=m4a]"), True)
check("_biggest refuses an empty folder", raises(lambda: media._biggest(DATA / "nowhere", "x"), RuntimeError), True)

# ---------------------------------------------------------------- _ydl options
import types  # noqa: E402

made = []


class ModuleYDL:
    def __init__(self, opts):
        made.append(opts)


fake_yt = types.ModuleType("yt_dlp_fake")
fake_yt.YoutubeDL = ModuleYDL
old_yt = sys.modules.get("yt_dlp")
sys.modules["yt_dlp"] = fake_yt
try:
    media._ydl({"format": "ba", "noplaylist": False})
finally:
    if old_yt is None:
        sys.modules.pop("yt_dlp", None)
    else:
        sys.modules["yt_dlp"] = old_yt
check(
    "_ydl sets the safe defaults",
    {k: made[0][k] for k in ("quiet", "no_warnings", "extract_flat", "playlistend", "socket_timeout")},
    {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist", "playlistend": 1, "socket_timeout": 20},
)
check("_ydl lets a caller override a default", made[0]["noplaylist"], False)
check("_ydl passes the requested format", made[0]["format"], "ba")
check("_ydl uses a silent logger", isinstance(made[0]["logger"], media._Quiet), True)


# ---------------------------------------------------------------- preview and save_thumb
class FakeResponse:
    def __init__(self, data=b"JPEG", ctype="image/jpeg"):
        self.data, self.headers = data, {"Content-Type": ctype}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n):
        return self.data[:n]


import urllib.request  # noqa: E402

preview_info = {
    "title": "A very long title",
    "uploader": "Someone",
    "duration": 12,
    "is_live": False,
    "kind": "video",
    "thumbnail": "https://img/thumb.jpg",
}
info_calls = []
with (
    patch(media, "info", lambda url: (info_calls.append(url), preview_info)[1]),
    patch(urllib.request, "urlopen", lambda *a, **kw: FakeResponse()),
):
    p = media.preview("  " + YOUTUBE + "  ")
    check("preview returns a data URL for the thumbnail", p["thumb"].startswith("data:image/jpeg;base64,"), True)
    check("preview carries the video's details", (p["title"], p["uploader"], p["duration"]), ("A very long title", "Someone", 12))
    media.preview(YOUTUBE)
    check("preview is cached by link", len(info_calls), 1)

with patch(media, "info", lambda url: {**preview_info, "kind": "playlist"}):
    check("preview refuses a playlist", raises(lambda: media.preview("https://youtube.com/playlist?list=x"), ValueError), True)

with patch(media, "info", lambda url: preview_info), patch(urllib.request, "urlopen", lambda *a, **kw: FakeResponse(ctype="text/html")):
    check("preview ignores a thumbnail of the wrong type", media.preview("https://example.org/other")["thumb"], "")

media._PREVIEWS[OTHER] = {"thumb": "data:image/png;base64," + base64.b64encode(b"PNG").decode()}
check(
    "save_thumb writes a cached thumbnail",
    (media.save_thumb(DATA / "j1", url=OTHER), (DATA / "j1" / "clips" / "thumb.jpg").read_bytes()),
    (True, b"PNG"),
)
check("save_thumb is false without a thumbnail", media.save_thumb(DATA / "j2", url="https://example.org/none"), False)

thumb_calls = []


def make_thumb(cmd, **kw):
    thumb_calls.append(cmd)
    Path(cmd[-1]).write_bytes(b"frame")
    return ""


with patch(media, "probe", lambda p: {"duration": 30}), patch(media, "run", make_thumb):
    check("save_thumb can take a frame of an uploaded video", media.save_thumb(DATA / "j3", video=Path("video.mp4")), True)
check(
    "the thumbnail frame is taken near the start",
    "-ss" in thumb_calls[-1] and thumb_calls[-1][thumb_calls[-1].index("-ss") + 1] == "3.00",
    True,
)
with patch(media, "probe", lambda p: (_ for _ in ()).throw(RuntimeError("bad file"))):
    check("save_thumb returns false when ffmpeg cannot read the file", media.save_thumb(DATA / "j4", video=Path("bad.mp4")), False)


print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
