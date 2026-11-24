"""A clip really is rendered: the file, the cover and captions burned into its video frames.

Everything here is the code the programme actually runs, with ffmpeg (which RONB Clips needs anyway) and a short
made-up video. It catches what only a real render can: a broken caption line, a layout filter ffmpeg refuses, a
heading with a character that breaks the subtitle file, a clip that comes out the wrong length or shape.

Runs against the code directly, with its own empty data folder."""

import os
import re
import subprocess
import sys
import tempfile

os.environ["RONBCLIPS_DATA"] = tempfile.mkdtemp(prefix="pc-render-")
sys.path.insert(0, ".")
from pathlib import Path

from studio import render

fails = []


def check(n, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else f"   got {got!r}"))
    ok or fails.append(n)


# ---------------------------------------------------------------- ffmpeg gets a share of the processor
check("ffmpeg threads are chosen automatically", render.ffmpeg_threads() >= 1)
os.environ["RONBCLIPS_FFMPEG_THREADS"] = "3"
check("ffmpeg threads can be set by hand", render.ffmpeg_threads(), 3)
os.environ.pop("RONBCLIPS_FFMPEG_THREADS", None)

# ---------------------------------------------------------------- the caption file itself, without ffmpeg
check("a time never shows sixty seconds", render._ts(59.999), "0:01:00.00")
check("...and whole hours still line up", render._ts(3600.5), "1:00:00.50")
check("a negative time becomes the start", render._ts(-3), "0:00:00.00")

words = [
    {"w": "hello", "s": 10.0, "e": 10.4},
    {"w": "there", "s": 10.5, "e": 10.9},
    {"w": "outside", "s": 1.0, "e": 1.4},
    {"w": "later", "s": 20.0, "e": 20.3},
]
ass = render.build_ass(words, 9.0, 13.0, "He said {\\an8} hi\n there", "bounce", ["hello"], 300, 1380)
check("a heading with braces, a backslash and a line break cannot break the caption file", "{an8}" in ass or "\\an8" in ass, False)
check("...its words are still there", "He said an8 hi there" in ass, True)
check("a word from outside the clip is not drawn", "OUTSIDE" in ass, False)
check("a word after the clip is not drawn", "LATER" in ass, False)
check("the word being said is drawn", "HELLO" in ass and "THERE" in ass, True)
check("the highlighted word gets its own colour", ass.count("\\c&H") >= 3, True)


def spans(text):
    """Every Dialogue line as (from, to) in seconds, read back from the file."""
    out = []
    for line in text.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        parts = line.split(",", 9)
        for stamp in parts[1:3]:
            h, m, s = stamp.split(":")
            out.append(int(h) * 3600 + int(m) * 60 + float(s))
    return out


t = spans(ass)
check("every caption starts before it ends", all(t[i] <= t[i + 1] for i in range(0, len(t), 2)), True)
check("...and none outlasts the clip", max(t) <= 13.0 + 0.25, True)

# A long phrase must be shrunk, never run off the frame. The exact size is not the point; the line must be
# smaller than the style's own size after the shrink.
long_word = "supercalifragilisticexpialidocious"
long_ass = render.build_ass([{"w": long_word, "s": 2.0, "e": 3.0}], 2.0, 6.0, "", "hype", [], 300, 1380)
sizes = [int(x) for x in re.findall(r"\\fs(\d+)", long_ass)]
check("a very long word is made smaller to fit", min(sizes or [0]) < render.STYLES["hype"]["size"], True)

# A script the caption font cannot draw is drawn whole, centred, instead of measured word by word.
jp = [{"w": "これは", "s": 2.0, "e": 2.6}, {"w": "テスト", "s": 2.6, "e": 3.2}]
jp_ass = render.build_ass(jp, 2.0, 4.0, "hook", "bounce", [], 300, 1380)
check("a caption in another script is drawn as one line", "これは テスト" in jp_ass, True)

# ---------------------------------------------------------------- a real render
source = Path(os.environ["RONBCLIPS_DATA"]) / "source.mp4"
made = subprocess.run(
    [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=640x360:rate=30:d=8",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=8",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        str(source),
    ],
    capture_output=True,
    text=True,
)
check("a test video was made", made.returncode, 0)

if made.returncode == 0:
    render.install_fonts()
    out = Path(os.environ["RONBCLIPS_DATA"]) / "clips"
    spoken = [
        {"w": "this", "s": 1.2, "e": 1.6},
        {"w": "is", "s": 1.6, "e": 1.8},
        {"w": "the", "s": 1.8, "e": 2.0},
        {"w": "moment", "s": 2.0, "e": 2.4},
    ]
    clip = {
        "start": 1.0,
        "end": 5.0,
        "hook": "It happened",
        "layout": "crop",
        "style": "bounce",
        "captions": True,
        "highlight_words": ["moment"],
    }
    got = None
    try:
        got = render.render_clip(source, out, "clip01", clip, spoken, 640, 360)
        check("the clip renders", bool(got), True)
    except Exception as e:  # noqa: BLE001 - a failed render is the thing being tested
        check("the clip renders", False, f"it failed: {e}")
    if got:
        check("the clip is named", got["file"], "clip01.mp4")
        check("...with a cover", (out / "clip01.jpg").stat().st_size > 1000, True)
        check("...and its caption file kept", (out / "clip01.ass").is_file(), True)
        check("...and it is not an empty file", (out / "clip01.mp4").stat().st_size > 10000, True)
        subtitles = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "s:0",
                "-show_entries",
                "stream=codec_type",
                "-of",
                "csv=p=0",
                str(out / "clip01.mp4"),
            ],
            capture_output=True,
            text=True,
        )
        check("captions are embedded in the video instead of a subtitle stream", subtitles.stdout.strip(), "")
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height:format=duration",
                "-of",
                "default=nw=1",
                str(out / "clip01.mp4"),
            ],
            capture_output=True,
            text=True,
        )
        values = dict(line.split("=", 1) for line in probe.stdout.split() if "=" in line)
        check("it is vertical, 1080 wide", (values.get("width"), values.get("height")), ("1080", "1920"))
        check("...and as long as the moment asked for", round(float(values.get("duration", 0)), 1), 4.0)

        # The same again with the captions switched off and a different layout: no caption lines, still a clip.
        try:
            off = render.render_clip(
                source, out, "clip02", dict(clip, start=2.0, end=4.5, captions=False, layout="blur", hook=""), spoken, 640, 360
            )
            check("a clip with no captions is made too", (out / off["file"]).stat().st_size > 10000, True)
            body = (out / "clip02.ass").read_text(encoding="utf-8")
            check("...and has no caption lines in it", ",Cap," in body, False)
        except Exception as e:  # noqa: BLE001
            check("a clip with no captions is made too", False, f"it failed: {e}")

        # The fonts are brought into the clip's work folder without copying them when the drive can do that. On a
        # drive that cannot, they are copied instead, so the clip must still be made.
        def no_links(*a, **k):
            raise OSError("this drive cannot link")

        real_link, os.link = os.link, no_links
        try:
            copied = render.render_clip(source, out, "clip03", dict(clip, start=0.5, end=2.5), spoken, 640, 360)
            check("a clip is still made where the fonts can only be copied", (out / copied["file"]).stat().st_size > 10000, True)
        except Exception as e:  # noqa: BLE001
            check("a clip is still made where the fonts can only be copied", False, f"it failed: {e}")
        finally:
            os.link = real_link

        # Proof that captions are part of the picture, not a subtitle track a player may or may not show:
        # render the same still frame twice, with captions on and off, and compare the pixels at the moment a
        # word is said. Nothing else changes between the two renders.
        still = Path(os.environ["RONBCLIPS_DATA"]) / "still.mp4"
        made = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=blue:s=640x360:r=30:d=6",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=6",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-shortest",
                str(still),
            ],
            capture_output=True,
            text=True,
        )
        check("a still test video was made", made.returncode, 0)
        if made.returncode == 0:
            same = dict(start=1.0, end=4.0, hook="", layout="crop", style="bounce", captions=True)
            on = render.render_clip(still, out, "withcaps", same, spoken, 640, 360)
            without = render.render_clip(still, out, "nocaps", dict(same, captions=False), spoken, 640, 360)
            frames = []
            for name, clip_name in (("with", on["file"]), ("without", without["file"])):
                png = Path(os.environ["RONBCLIPS_DATA"]) / f"{name}.png"
                subprocess.run(
                    ["ffmpeg", "-y", "-v", "error", "-i", str(out / clip_name), "-ss", "1.5", "-frames:v", "1", str(png)], check=True
                )
                frames.append(png)
            from PIL import Image, ImageChops

            a, b = (Image.open(p).convert("RGB") for p in frames)
            diff = ImageChops.difference(a, b).convert("L")
            w, h = a.size
            caption_area = diff.crop((0, int(h * 0.55), w, int(h * 0.95)))
            changed = sum(1 for v in caption_area.getdata() if v > 40)
            check("captions are drawn into the video's pixels", changed, lambda v: v > 500)
            top_area = diff.crop((0, 0, w, int(h * 0.45)))
            unchanged = sum(1 for v in top_area.getdata() if v > 40)
            check("nothing outside the captions changed", unchanged, lambda v: v < 200)

print("ALL PASSED" if not fails else f"{len(fails)} failed")
import shutil

shutil.rmtree(os.environ["RONBCLIPS_DATA"], ignore_errors=True)
sys.exit(1 if fails else 0)
