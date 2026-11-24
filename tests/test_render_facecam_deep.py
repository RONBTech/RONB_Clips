"""Deep checks of the clip renderer and the facecam finder.

ffmpeg and ffprobe are replaced with fixed answers where the *command* is what matters; every piece of file
handling (fonts, the work folder, the moved clip, the cover and the sidecar) happens on real temporary files.
The picture measurements use small arrays made here, so every branch is reached without a long video.
Runs against the code directly, with its own empty data folder.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

DATA = Path(tempfile.mkdtemp(prefix="ronbclips-render-cam-deep-"))
os.environ["RONBCLIPS_DATA"] = str(DATA)
os.environ.update(
    DATABASE_URL=os.environ.get("RONBCLIPS_TEST_DATABASE_URL", ""),
    RONBCLIPS_FETCH_MODEL="0",
    RONBCLIPS_UPDATE_CHECK="0",
    PYTHONIOENCODING="utf-8",
)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from studio import facecam, load, render  # noqa: E402

fails = []


def check(name, got, expected):
    ok = got == expected
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  got {got!r}, expected {expected!r}"))
    if not ok:
        fails.append(name)


def ok(name, got):
    check(name, bool(got), True)


# --------------------------------------------------------------------------- fonts and layouts
FONTS = DATA / "fonts"


def test_install_fonts():
    with patch.object(render.config, "FONTS_DIR", FONTS):
        render.install_fonts()
        names = sorted(p.name for p in FONTS.glob("*.ttf"))
        check(
            "the bundled fonts are copied into the data folder",
            names,
            ["Anton-Regular.ttf", "ArchivoBlack-Regular.ttf", "Poppins-ExtraBold.ttf"],
        )
        (FONTS / "Anton-Regular.ttf").unlink()
        render.install_fonts()
        check("a font that went missing is put back", (FONTS / "Anton-Regular.ttf").is_file(), True)


test_install_fonts()

check("blur: the game keeps its own height in the middle", "scale=270:480" in render._filter("blur", 1080), True)
check("zoom: the game is taller", "scale=1080:1000" in render._filter("zoom", 1000), True)
check("crop: the game fills the screen", render._filter("crop", 1080).startswith("[0:v]fps=30,scale=1080:1920"), True)
check("an unknown layout falls back to the blurred frame", "boxblur=6:2" in render._filter("strange", 1080), True)
cam = [100, 50, 300, 170]
f = render._filter("gameplay", 1080, cam)
pip_h = round(render.PIP_W * 170 / 300)
check("gameplay with a facecam splits the frame", "split=2[base][fc]" in f, True)
check("...crops the facecam out of the source", f"crop=300:170:100:50,scale={render.PIP_W}:{pip_h}" in f, True)
check("...and puts it in the top-right corner", f"overlay={1080 - render.PIP_W - render.PIP_MARGIN}:{render.PIP_MARGIN}[v0]" in f, True)
check(
    "a facecam too small to crop falls back to the blurred frame",
    "boxblur=6:2" in render._filter("gameplay", 1080, [0, 0, 8, 8])
    and "split=2[base][fc]" not in render._filter("gameplay", 1080, [0, 0, 8, 8]),
    True,
)
check("gameplay without a facecam is a centre crop", "crop=1080:1920" in render._filter("gameplay", 1080, None), True)
check("a heading follows the facecam picture", render._hook_y("gameplay", 300, cam), render.PIP_MARGIN + pip_h + 32)
check("a facecam with the wrong number of numbers is ignored", render._hook_y("gameplay", 300, [0, 0, 300]), 300)
check("a facecam with no width is ignored", render._hook_y("gameplay", 300, [0, 0, 8, 170]), 300)
check("other layouts keep the heading where it was", render._hook_y("crop", 300, cam), 300)

# --------------------------------------------------------------------------- how many threads
for setting, busy, want in (("abc", False, 4), ("0", False, 1), ("3", True, 3), ("", True, 2)):
    with patch.dict(os.environ, {"RONBCLIPS_FFMPEG_THREADS": setting}), patch.object(os, "cpu_count", return_value=8):
        with patch.object(load, "busy", return_value=busy):
            check(f"ffmpeg threads for {setting!r} while busy={busy}", render.ffmpeg_threads(), want)

# --------------------------------------------------------------------------- encoder discovery
real_listed, real_dead = render._listed[0], set(render._dead)
try:
    listed = " V..... h264_nvenc\n A..... aac\nS..... subrip\nV..... libx264\nshort line\n"
    render._listed[0] = None
    with patch.object(render.subprocess, "run", return_value=SimpleNamespace(stdout=listed)) as run:
        found = render._has_encoder()
    check("the encoder list is read from ffmpeg", found, {"h264_nvenc", "aac", "subrip", "libx264"})
    check(
        "ffmpeg is asked for its encoders with a timeout",
        (run.call_args.args[0][1:], run.call_args.kwargs["timeout"]),
        (["-hide_banner", "-encoders"], 30),
    )
    check("the list is kept for later renders", render._has_encoder(), found)
    render._listed[0] = None
    with patch.object(render.subprocess, "run", side_effect=OSError("no ffmpeg")):
        check("no ffmpeg means no hardware encoders", render._has_encoder(), set())

    with patch.dict(os.environ, {"RONBCLIPS_ENCODER": "nvenc"}), patch.object(render, "_has_encoder", return_value=set()):
        with patch.object(render, "ffmpeg_threads", return_value=4):
            got = render.encoders()
        check("a forced encoder is tried first, x264 last", [k for k, _ in got], ["nvenc", "x264"])
        check("the hardware encoder gets its own flags", got[0][1][:3], ["nv12", "-c:v", "h264_nvenc"])
        check("x264 gets the thread count", got[1][1][-2:], ["-threads", "4"])
        render._dead.add("nvenc")
        check("an encoder that already failed is skipped", [k for k, _ in render.encoders()], ["x264"])
        render._dead.clear()
    with patch.dict(os.environ, {"RONBCLIPS_ENCODER": "not-an-encoder"}), patch.object(render, "_has_encoder", return_value={"h264_qsv"}):
        check("an unknown forced name is ignored", [k for k, _ in render.encoders()], ["qsv", "x264"])
    with patch.dict(os.environ, {"RONBCLIPS_ENCODER": "  X264  "}), patch.object(render, "_has_encoder", return_value=set()):
        check("the forced name is trimmed and lowercased", [k for k, _ in render.encoders()], ["x264"])
finally:
    render._listed[0] = real_listed
    render._dead.clear()
    render._dead.update(real_dead)

# --------------------------------------------------------------------------- the temporary folder
check("two paths on the same drive share a disk", render._same_disk(DATA, DATA / "clips"), True)
with patch.object(render.os, "stat", side_effect=OSError("gone")):
    check("a path that cannot be measured is treated as the same disk", render._same_disk(DATA / "one", DATA / "two"), True)
with patch.object(render.os, "stat", side_effect=lambda p: SimpleNamespace(st_dev=1 if "one" in str(p) else 2)):
    check("two different drives are seen", render._same_disk(DATA / "one", DATA / "two"), False)

# --------------------------------------------------------------------------- render_clip's choices
choices = []


def fake_render_in(work, src, out_dir, name, start, end, layout, fg_h, ass, face=None):
    (work / "was-here").write_text("yes", encoding="utf-8")
    choices.append(
        {
            "work": work,
            "layout": layout,
            "fg_h": fg_h,
            "ass": ass,
            "face": face,
            "start": start,
            "end": end,
            "captions": ",Cap," in ass,
        }
    )
    return {"file": name + ".mp4", "layout": layout}


with patch.object(render, "_render_in", fake_render_in):
    src = DATA / "source.mp4"
    out = DATA / "clips"
    clip = {"start": 1.0, "end": 3.0, "hook": "Hi", "captions": True, "layout": "blur"}
    got = render.render_clip(src, out, "one", clip, [{"w": "hello", "s": 1.1, "e": 1.5}], 640, 360)
    check("render_clip reports the file and layout", (got["file"], got["layout"]), ("one.mp4", "blur"))
    check("the output folder is made", out.is_dir(), True)
    check("a horizontal source keeps the blur layout", choices[-1]["layout"], "blur")
    check("spoken words are in the caption file", choices[-1]["captions"], True)
    check("the caption gets the clip's own times", (choices[-1]["start"], choices[-1]["end"]), (1.0, 3.0))
    check("the short work folder is removed afterwards", choices[-1]["work"].exists(), False)

    render.render_clip(src, out, "two", dict(clip, layout="gameplay"), [], 640, 360)
    check("gameplay without a facecam becomes a crop", choices[-1]["layout"], "crop")
    import re

    def heading_y(ass):
        return int(re.findall(r"\\pos\(540,(-?\d+)\)", ass)[0])

    plain_heading = heading_y(choices[-1]["ass"])
    render.render_clip(src, out, "three", dict(clip, layout="gameplay", facecam=cam), [], 640, 360)
    check("gameplay with a facecam keeps the facecam", (choices[-1]["layout"], choices[-1]["face"]), ("gameplay", cam))
    check("the heading follows the facecam picture", plain_heading - heading_y(choices[-1]["ass"]), 300 - (render.PIP_MARGIN + pip_h + 32))
    render.render_clip(src, out, "four", dict(clip, captions=False), [{"w": "hello", "s": 1.1, "e": 1.5}], 640, 360)
    check("captions switched off leave no caption lines", choices[-1]["captions"], False)
    render.render_clip(src, out, "five", clip, [], 500, 900)
    check("a vertical source is cropped instead of blurred", choices[-1]["layout"], "crop")

    boom = []

    def failing(work, *a, **k):
        boom.append(work)
        raise RuntimeError("the encoder broke")

    with patch.object(render, "_render_in", failing):
        try:
            render.render_clip(src, out, "six", clip, [], 640, 360)
        except RuntimeError as e:
            check("a failed render passes the error on", str(e), "the encoder broke")
        check("its work folder is removed too", boom[0].exists(), False)

# --------------------------------------------------------------------------- _render_in, with ffmpeg replaced
WORK = DATA / "work"
WORK.mkdir(exist_ok=True)
FONT_DIR = DATA / "render-fonts"
FONT_DIR.mkdir(exist_ok=True)
for font in ("One.ttf", "Two.ttf"):
    (FONT_DIR / font).write_bytes(b"not a real font, only a placeholder")

commands = []
covers = []


def good_ffmpeg(cmd, **kw):
    commands.append((cmd, kw))
    if "-filter_complex" in cmd:
        Path(kw["cwd"], cmd[-1]).write_bytes(b"video")
    return SimpleNamespace(returncode=0, stderr="")


def good_cover(cmd, **kw):
    covers.append((cmd, kw))
    Path(kw["cwd"], cmd[-1]).write_bytes(b"cover")
    return SimpleNamespace(returncode=0, stderr="")


with (
    patch.object(render.config, "FONTS_DIR", FONT_DIR),
    patch.object(render, "encoders", return_value=[("x264", ["-c:v", "libx264"])]),
    patch.object(render.subprocess, "run", good_ffmpeg),
    patch.object(render, "run", good_cover),
):
    ass_text = "[Script Info]\nDialogue: test\n"
    result = render._render_in(WORK, src, out, "clip", 1.0, 4.0, "blur", 1080, ass_text, face=None)
    check("_render_in reports the clip, cover and layout", result, {"file": "clip.mp4", "cover": "clip.jpg", "layout": "blur"})
    check(
        "the clip and its cover and sidecar are moved out",
        [(out / f"clip.{e}").is_file() for e in ("mp4", "jpg", "ass")],
        [True, True, True],
    )
    check("the sidecar kept its text", (out / "clip.ass").read_text(encoding="utf-8"), ass_text)
    cmd, kw = commands[-1]
    check("ffmpeg is given the clip's own time and length", (cmd[cmd.index("-ss") + 1], cmd[cmd.index("-t") + 1]), ("1.00", "3.00"))
    check("the filter joins the caption sidecar", "ass=clip.ass:fontsdir=f" in cmd[cmd.index("-filter_complex") + 1], True)
    check(
        "the video and the loudness-normalised audio are mapped",
        (cmd[cmd.index("-map") + 1], cmd[cmd.index("-map", cmd.index("-map") + 2) + 1]),
        ("[v]", "0:a:0"),
    )
    check("audio is normalised", any("loudnorm=I=-14" in x for x in cmd), True)
    check("the video is written into the work folder, then moved", (kw["cwd"], cmd[-1]), (WORK, "clip.mp4"))
    check("the cover is taken inside the clip", covers[-1][0][covers[-1][0].index("-ss") + 1], "1.20")
    check("the fonts are next to the sidecar", sorted(p.name for p in (WORK / "f").glob("*.ttf")), ["One.ttf", "Two.ttf"])

# A drive that cannot hard-link still gets its fonts: they are copied.
shutil.rmtree(WORK)
WORK.mkdir()
with (
    patch.object(render.config, "FONTS_DIR", FONT_DIR),
    patch.object(render, "encoders", return_value=[("x264", ["-c:v", "libx264"])]),
    patch.object(render.subprocess, "run", good_ffmpeg),
    patch.object(render, "run", good_cover),
    patch.object(render.os, "link", side_effect=OSError("no links here")),
):
    render._render_in(WORK, src, out, "copied", 0.5, 2.5, "crop", 1080, ass_text)
    check("a font that cannot be linked is copied", (WORK / "f" / "One.ttf").read_bytes(), b"not a real font, only a placeholder")

# A long path (or another disk) makes ffmpeg write straight into the finishing folder.
shutil.rmtree(WORK)
WORK.mkdir()
with (
    patch.object(render.config, "FONTS_DIR", FONT_DIR),
    patch.object(render, "encoders", return_value=[("x264", ["-c:v", "libx264"])]),
    patch.object(render, "subprocess", create=True),
    patch.object(render, "_same_disk", return_value=False),
    patch.object(render.subprocess, "run", good_ffmpeg),
    patch.object(render, "run", good_cover),
):
    render._render_in(WORK, src, out, "direct", 0.0, 2.0, "crop", 1080, ass_text)
    check("with no short path available ffmpeg writes to the final folder", commands[-1][0][-1], str(out / "direct.mp4"))
    check("only the fonts are left in the work folder", sorted(p.name for p in WORK.iterdir()), ["f"])
    check("the cover and sidecar still arrive", (out / "direct.jpg").is_file() and (out / "direct.ass").is_file(), True)

# Every encoder failing is an error, and a half-written clip is not left behind.
shutil.rmtree(WORK)
WORK.mkdir()
real_dead = set(render._dead)
try:
    render._dead.clear()

    def bad_ffmpeg(cmd, **kw):
        commands.append((cmd, kw))
        if "-filter_complex" in cmd:
            Path(cmd[-1]).write_bytes(b"half a clip")
        return SimpleNamespace(returncode=1, stderr="the encoder refused " + "x" * 1400)

    with (
        patch.object(render.config, "FONTS_DIR", FONT_DIR),
        patch.object(render, "encoders", return_value=[("x264", ["-c:v", "libx264"])]),
        patch.object(render.subprocess, "run", bad_ffmpeg),
        patch.object(render, "_same_disk", return_value=False),
    ):
        raised = ""
        try:
            render._render_in(WORK, src, out, "failed", 0.0, 2.0, "crop", 1080, ass_text)
        except RuntimeError as e:
            raised = str(e)
        check("a failed encode says why", raised.startswith("Render failed: "), True)
        check("a very long ffmpeg error is cut from the front, not the end", "encoder refused" in raised, False)
        check("only the end of a long ffmpeg error is kept", len(raised), len("Render failed: ") + 1200)
        check("a half-written clip is removed", (out / "failed.mp4").exists(), False)

    # A hardware encoder is marked dead and x264 is tried next.
    render._dead.clear()
    answers = [
        SimpleNamespace(returncode=1, stderr="no graphics card"),
        SimpleNamespace(returncode=0, stderr=""),
    ]

    def fallback(cmd, **kw):
        commands.append((cmd, kw))
        if "-filter_complex" in cmd:
            Path(kw["cwd"], cmd[-1]).write_bytes(b"video")
        return answers.pop(0)

    with (
        patch.object(render.config, "FONTS_DIR", FONT_DIR),
        patch.object(render, "encoders", side_effect=lambda: [("nvenc", ["-c:v", "h264_nvenc"]), ("x264", ["-c:v", "libx264"])]),
        patch.object(render.subprocess, "run", fallback),
        patch.object(render, "run", good_cover),
    ):
        shutil.rmtree(WORK)
        WORK.mkdir()
        got = render._render_in(WORK, src, out, "fallback", 0.0, 2.0, "crop", 1080, ass_text)
        check("x264 takes over when the card cannot encode", got["file"], "fallback.mp4")
        check("the dead encoder is remembered for later clips", "nvenc" in render._dead, True)
finally:
    render._dead.clear()
    render._dead.update(real_dead)

shutil.rmtree(WORK, ignore_errors=True)

# =========================================================================== facecam
W, H = facecam.W, facecam.H


def frames(n=6, value=100):
    return np.full((n, H, W), value, dtype=np.uint8)


# Reading a video: unreadable, empty, short and normal.
with patch.object(facecam, "_duration", return_value=10.0), patch.object(facecam.subprocess, "run", side_effect=OSError("no ffmpeg")):
    check("no ffmpeg gives no frames", facecam._frames("x").shape, (0, H, W))
with (
    patch.object(facecam, "_duration", return_value=0.0),
    patch.object(facecam.subprocess, "run", return_value=SimpleNamespace(stdout=b"\x00" * (W * H * 5))) as run,
):
    check("a video of unknown length still gets frames", facecam._frames("x").shape, (5, H, W))
    check("the fallback frame rate is used", "fps=0.2000" in run.call_args.args[0][6], True)
with (
    patch.object(facecam, "_duration", return_value=10.0),
    patch.object(facecam.subprocess, "run", return_value=SimpleNamespace(stdout=b"\x00" * (W * H * 3))),
):
    check("fewer than four frames are not enough", facecam._frames("x").shape, (0, H, W))
with (
    patch.object(facecam, "_duration", return_value=10.0),
    patch.object(facecam.subprocess, "run", return_value=SimpleNamespace(stdout=b"\x00" * (W * H * 6 + 7))),
):
    check("a few extra bytes are ignored", facecam._frames("x").shape, (6, H, W))
with patch.object(facecam.subprocess, "run", return_value=SimpleNamespace(stdout="12.5\n")):
    check("the duration is read from ffprobe", facecam._duration("x"), 12.5)
with patch.object(facecam.subprocess, "run", return_value=SimpleNamespace(stdout="")):
    check("no duration means zero", facecam._duration("x"), 0.0)
with patch.object(facecam.subprocess, "run", side_effect=OSError("no ffprobe")):
    check("no ffprobe means zero", facecam._duration("x"), 0.0)

vx, vy = facecam._profiles(frames(2))
check("too few frames give flat profiles", (vx.shape, vy.shape, vx.max(), vy.max()), ((W,), (H,), 0.0, 0.0))
f = frames(5)
f[:, :, 10] = 0
vx, vy = facecam._profiles(f)
check("a straight vertical line shows in the column profile", int(vx.argmax()), 9)
check("the horizontal profile stays flat", float(vy.max()), 0.0)
check("a profile too short has no peaks", facecam._peaks(np.array([0.0, 1.0]), 10), [])
check("a flat profile has no peaks", facecam._peaks(np.zeros(10), 10), [])
peaks = facecam._peaks(np.array([0.0, 0.0, 10.0, 0.0, 0.0, 5.0, 0.0]), 2)
check("only the strongest peaks are kept, in order", [p[0] for p in peaks], [2, 5])

with patch.object(facecam.subprocess, "run", return_value=SimpleNamespace(stdout="width=640\nheight=360\nother=1\nwidth=nope\n")):
    check("the source size is read from ffprobe", facecam._size("x"), (640, 360))
with patch.object(facecam.subprocess, "run", return_value=SimpleNamespace(stdout="width=\nheight=\n")):
    check("an unreadable source size is zero", facecam._size("x"), (0, 0))
check("a still picture has no movement", float(facecam._stillness(frames(6)).max()), 0.0)

# Is this rectangle a webcam picture?
still = np.zeros((H, W), dtype=np.float32)
light = np.full((H, W), 100.0, dtype=np.float32)
check("a quiet, lit box of the right shape is plausible", facecam._plausible(40, 30, 140, 100, still, light, 10.0), 0.0)
check("a box that is too small is refused", facecam._plausible(0, 0, 20, 20, still, light, 10.0), None)
check("a box that is too large is refused", facecam._plausible(0, 0, 250, 150, still, light, 10.0), None)
check("a box with the wrong aspect is refused", facecam._plausible(0, 0, 40, 160, still, light, 10.0), None)
moving = np.full((H, W), 50.0, dtype=np.float32)
check("a box that moves with the game is refused", facecam._plausible(40, 30, 140, 100, moving, light, 10.0), None)
part = np.zeros((H, W), dtype=np.float32)
part[30:100, 40:88] = 50.0
check("a box with the game showing through is refused", facecam._plausible(40, 30, 140, 100, part, light, 10.0), None)
dark = np.full((H, W), 100.0, dtype=np.float32)
dark[30:100, 40:140] = 10.0
check("a black hole is refused", facecam._plausible(40, 30, 140, 100, still, dark, 10.0), None)
check("an empty rectangle is refused", facecam._plausible(170, 100, 170, 100, still, light, 10.0), None)

boxes = facecam._local([(50, 3.0), (200, 3.0)], [(30, 3.0), (120, 3.0)], still, light)
ok("strong lines make candidate boxes", boxes)
check("the candidates are sorted best first", all(boxes[i]["key"] >= boxes[i + 1]["key"] for i in range(len(boxes) - 1)), True)
check(
    "each candidate has its four borders and a score",
    all(set(("x", "y", "x1", "y1", "score", "moving", "key")) <= set(b) for b in boxes),
    True,
)
check("a box with only one border of its own is not a candidate", facecam._local([(50, 3.0)], [], still, light), [])

same = {"x": 0, "y": 0, "x1": 100, "y1": 100}
check("the same box overlaps completely", round(facecam._overlap(same, (0, 0, 100 / W, 100 / H)), 2), 1.0)
check("boxes that do not touch do not overlap", facecam._overlap(same, (0.8, 0.8, 0.1, 0.1)), 0.0)
check("half a box is half an overlap", round(facecam._overlap(same, (0, 0, 200 / W, 100 / H)), 2), 0.5)

# One full-size grey frame: where the real borders are measured.
tiny = np.arange(12, dtype=np.uint8).tobytes()
with patch.object(facecam, "_size", return_value=(0, 0)):
    check("no source size gives no full frame", facecam._full_frame("x"), None)
with patch.object(facecam, "_size", return_value=(4, 3)), patch.object(facecam, "_duration", return_value=0.0):
    with patch.object(facecam.subprocess, "run", return_value=SimpleNamespace(stdout=tiny)):
        check("a full frame has the source's shape", facecam._full_frame("x").shape, (3, 4))
    with patch.object(facecam.subprocess, "run", return_value=SimpleNamespace(stdout=tiny[:5])):
        check("a cut-off frame is refused", facecam._full_frame("x"), None)
    with patch.object(facecam.subprocess, "run", side_effect=OSError("no ffmpeg")):
        check("ffmpeg failing gives no frame", facecam._full_frame("x"), None)

profile = np.array([0.0, 0.0, 9.0, 0.0, 4.0, 0.0])
check("the brightest line near a guess wins", facecam._best(profile, 3.5, 1), 2.0)
check("a guess out of range is left alone", facecam._best(profile, 100, 1), 100.0)

big = np.zeros((360, 640), dtype=np.float32)
big[:, 200] = 255.0
big[100, :] = 255.0
with patch.object(facecam, "_full_frame", return_value=big):
    snapped = facecam._snap("x", (0, 0, 100, 60), 640, 360)
    check("an edge stays on the edge", (snapped[0], snapped[1]), (0.0, 0.0))
    check("a border is snapped to the bright line", abs(snapped[2] - 200) <= 2, True)
    check("a guessed line is searched nearby", abs(snapped[3] - 120) <= 4, True)
    edge = facecam._snap("x", (0, 0, 318, 60), 640, 360)
    check("a side close to the frame edge is the edge", edge[2], 640.0)
with patch.object(facecam, "_full_frame", return_value=None):
    check("no full frame gives no snapped box", facecam._snap("x", (0, 0, 10, 10), 640, 360), None)

for box, corner in (
    ((0, 0, 50, 50), "top-left"),
    ((270, 0, 320, 50), "top-right"),
    ((0, 130, 50, 180), "bottom-left"),
    ((270, 130, 320, 180), "bottom-right"),
    ((300, 60, 320, 120), "right"),
    ((0, 60, 20, 120), "left"),
    ((60, 170, 200, 180), "bottom"),
    ((60, 0, 200, 10), "top"),
    ((60, 40, 200, 120), "middle"),
):
    check(f"a box at {box} is {corner}", facecam._corner(*box, W, H), corner)

found = {"x": 40, "y": 30, "x1": 140, "y1": 100, "score": 2.5}
with patch.object(facecam, "_snap", return_value=(0.0, 0.0, 100.0, 70.0)):
    done = facecam._finish("x", found, W, H, still)
    check(
        "a finished box has the source pixels and its corner",
        (done["x"], done["y"], done["w"], done["h"], done["corner"]),
        (0, 0, 100, 70, "top-left"),
    )
    check("a quiet picture is a confident find", (done["conf"], done["score"]), ("high", 2.5))
    low = facecam._finish("x", found, W, H, moving)
    check("a picture that moves with the game is less certain", low["conf"], "low")
with patch.object(facecam, "_snap", return_value=(0.0, 0.0, 10.0, 10.0)):
    check("a box smaller than the smallest webcam is refused", facecam._finish("x", found, W, H, still), None)
with patch.object(facecam, "_snap", return_value=None):
    check("no snapped borders means no box", facecam._finish("x", found, W, H, still), None)

# One frame shown to the model, and its answer checked before it is believed.
with (
    patch.object(facecam, "_duration", return_value=20.0),
    patch.object(facecam.subprocess, "run", return_value=SimpleNamespace(stdout=b"jpeg bytes")) as run,
):
    check("the model gets a small jpeg", facecam._frame_jpeg("x"), b"jpeg bytes")
    check("the jpeg is 768 pixels wide", "-vf" in run.call_args.args[0] and "scale=768:-2" in run.call_args.args[0], True)
with patch.object(facecam.subprocess, "run", side_effect=OSError("no ffmpeg")):
    check("no frame for the model is empty bytes", facecam._frame_jpeg("x"), b"")
with patch.object(facecam, "_frame_jpeg", return_value=b""):
    check("an empty frame is not sent to the model", facecam._asked("x", lambda j: {"looked": True, "box": (0, 0, 1, 1)}), (False, None))
with patch.object(facecam, "_frame_jpeg", return_value=b"jpeg"):
    check("a model that throws is ignored", facecam._asked("x", lambda j: (_ for _ in ()).throw(RuntimeError("blind"))), (False, None))
    check("a model that did not look is ignored", facecam._asked("x", lambda j: None), (False, None))
    check(
        "a model's box is handed on",
        facecam._asked("x", lambda j: {"looked": True, "box": (0.1, 0.2, 0.3, 0.4)}),
        (True, (0.1, 0.2, 0.3, 0.4)),
    )

hint = (0.1, 0.2, 0.3, 0.3)
with patch.object(facecam, "_snap", return_value=None):
    check("a hint with no borders there is refused", facecam._seen("x", hint, W, H, still, light), None)
with patch.object(facecam, "_snap", return_value=(0.0, 0.0, 10.0, 10.0)):
    check("a hint that snaps too small is refused", facecam._seen("x", hint, W, H, still, light), None)
with (
    patch.object(facecam, "_snap", return_value=(0.0, 0.0, 100.0, 100.0)),
    patch.object(facecam, "_plausible", return_value=None),
):
    check("a hint on a moving game picture is refused", facecam._seen("x", hint, W, H, still, light), None)
with (
    patch.object(facecam, "_snap", return_value=(20.0, 30.0, 120.0, 100.0)),
    patch.object(facecam, "_plausible", return_value=1.0) as plausible,
    patch.object(facecam, "_finish", return_value={"from": "model"}) as finish,
):
    check("a good hint becomes a box", facecam._seen("x", hint, W, H, still, light), {"from": "model"})
    check("the hint was checked with a generous share", plausible.call_args.kwargs["share"], 1.0)
    check("the measured box is passed on to be finished", finish.call_args.args[1]["score"], 0.0)

# detect(): each way a model hint can end up used or ignored.
picture = frames(6)
with (
    patch.object(facecam, "_frames", return_value=picture),
    patch.object(facecam, "_size", return_value=(0, 0)),
):
    check("a video with no size has no facecam", facecam.detect("x"), None)
with (
    patch.object(facecam, "_frames", return_value=picture),
    patch.object(facecam, "_size", return_value=(W, H)),
    patch.object(facecam, "_asked", return_value=(True, None)),
):
    check("a model that looked and saw no webcam ends the search", facecam.detect("x", ask=lambda j: {}), None)
with (
    patch.object(facecam, "_frames", return_value=picture),
    patch.object(facecam, "_size", return_value=(W, H)),
    patch.object(facecam, "_profiles", return_value=(np.zeros(W), np.zeros(H))),
    patch.object(facecam, "_peaks", return_value=[(10, 3.0)]),
    patch.object(facecam, "_local", return_value=[found]),
    patch.object(facecam, "_asked", return_value=(True, (0.12, 0.17, 0.31, 0.39))),
    patch.object(facecam, "_finish", return_value={"from": "match"}) as finish,
):
    check("a hint that matches a measured box uses it", facecam.detect("x", ask=lambda j: {}), {"from": "match"})
    check("the matching candidate was finished", finish.call_args.args[1]["score"], 2.5)
with (
    patch.object(facecam, "_frames", return_value=picture),
    patch.object(facecam, "_size", return_value=(W, H)),
    patch.object(facecam, "_profiles", return_value=(np.zeros(W), np.zeros(H))),
    patch.object(facecam, "_peaks", return_value=[(10, 3.0)]),
    patch.object(facecam, "_local", return_value=[found]),
    patch.object(facecam, "_asked", return_value=(True, (0.7, 0.5, 0.2, 0.2))),
    patch.object(facecam, "_seen", return_value={"from": "seen"}),
):
    check("a hint that matches nothing is measured on its own", facecam.detect("x", ask=lambda j: {}), {"from": "seen"})
with (
    patch.object(facecam, "_frames", return_value=picture),
    patch.object(facecam, "_size", return_value=(W, H)),
    patch.object(facecam, "_profiles", return_value=(np.zeros(W), np.zeros(H))),
    patch.object(facecam, "_peaks", return_value=[(10, 3.0)]),
    patch.object(facecam, "_local", return_value=[]),
):
    check("nothing measured means no webcam", facecam.detect("x"), None)
with (
    patch.object(facecam, "_frames", return_value=picture),
    patch.object(facecam, "_size", return_value=(W, H)),
    patch.object(facecam, "_profiles", return_value=(np.zeros(W), np.zeros(H))),
    patch.object(facecam, "_peaks", return_value=[(10, 3.0)]),
    patch.object(facecam, "_local", return_value=[found]),
    patch.object(facecam, "_finish", return_value={"from": "measurement"}),
):
    check("without a model the best measured box wins", facecam.detect("x"), {"from": "measurement"})

shutil.rmtree(DATA, ignore_errors=True)
print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
