"""Turns a source range into a vertical clip: layout, ffmpeg encode, cover and ASS sidecar."""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import captions as _captions
from . import config, load
from .media import NO_WINDOW, run

W, H = config.OUT_W, config.OUT_H
STYLES = _captions.STYLES
HOOK = _captions.HOOK
FONT_FILES = _captions.FONT_FILES
CAP_MAX_W = _captions.CAP_MAX_W
CAPTION_LEAD = _captions.CAPTION_LEAD
_Font = _captions.Font

# Compatibility names: caption construction lives in captions.py, while these names remain available to callers.
_ts = _captions.ts
_clean = _captions.clean
_key = _captions.key
_groups = _captions.groups
geometry = _captions.geometry
_hook_events = _captions.hook_events
build_ass = _captions.build_ass


LAYOUTS = {
    "blur": "Full frame + blurred fill",
    "zoom": "Zoomed in + blurred fill",
    "crop": "Fill the screen (centre crop)",
    "gameplay": "Gameplay fills the screen, facecam in the corner",
}

PIP_W = 380
PIP_MARGIN = 26
RENDER_SECONDS = 600


def install_fonts():
    """Copy bundled caption fonts next to the data folder."""
    src = Path(__file__).resolve().parent / "fonts"
    for font in src.glob("*.ttf"):
        if not (config.FONTS_DIR / font.name).exists():
            shutil.copyfile(font, config.FONTS_DIR / font.name)


def _filter(layout, fg_h, facecam=None):
    if layout == "gameplay" and facecam:
        fx, fy, fw, fh = (int(v) for v in facecam)
        if fw > 8 and fh > 8:
            pip_h = max(2, int(round(PIP_W * fh / fw)))
            return (
                f"[0:v]fps={config.OUT_FPS},split=2[base][fc];"
                f"[base]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1[b];"
                f"[fc]crop={fw}:{fh}:{fx}:{fy},scale={PIP_W}:{pip_h},setsar=1[p];"
                f"[b][p]overlay={W - PIP_W - PIP_MARGIN}:{PIP_MARGIN}[v0];"
            )
    if layout == "crop" or (layout == "gameplay" and not facecam):
        return f"[0:v]fps={config.OUT_FPS},scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1[v0];"
    return (
        f"[0:v]fps={config.OUT_FPS},split=2[a][b];"
        f"[a]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=6:2,"
        f"scale={W}:{H},eq=brightness=-0.14:saturation=1.15[bg];"
        f"[b]scale={W}:{fg_h}:force_original_aspect_ratio=increase,crop={W}:{fg_h}[fg];"
        f"[bg][fg]overlay=0:(H-h)/2,setsar=1[v0];"
    )


def _hook_y(layout, hook_y, facecam=None):
    if layout == "gameplay" and facecam and len(facecam) == 4 and facecam[2] > 8:
        return PIP_MARGIN + int(round(PIP_W * facecam[3] / facecam[2])) + 32
    return hook_y


ENCODERS = {
    "nvenc": ("h264_nvenc", "nv12", ["-preset", "p4", "-rc", "vbr", "-cq", "23", "-b:v", "0"]),
    "qsv": ("h264_qsv", "nv12", ["-global_quality", "23", "-preset", "fast"]),
    "amf": ("h264_amf", "nv12", ["-rc", "cqp", "-qp_i", "23", "-qp_p", "23"]),
    "videotoolbox": ("h264_videotoolbox", "yuv420p", ["-q:v", "58"]),
    "x264": ("libx264", "yuv420p", ["-crf", "21", "-preset", "veryfast"]),
}
ENCODER_ORDER = ("nvenc", "qsv", "amf", "videotoolbox")
_dead: set = set()
_listed: list = [None]


def ffmpeg_threads() -> int:
    """Give ffmpeg a share of the processor: fewer while the machine is busy, with an optional fixed setting.

    Without this, x264 uses every core for every clip, even when several videos are being made at once. The number
    is read on each render, so a busy server really does render more gently.
    """
    set_by_hand = (config.env("RONBCLIPS_FFMPEG_THREADS", "") or "").strip()
    if set_by_hand:
        try:
            return max(1, int(set_by_hand))
        except ValueError:
            pass
    cpus = os.cpu_count() or 4
    per_render = max(2, min(8, cpus // 2))  # 4 cores -> 2 threads, 8 -> 4, 16 or more -> 8
    return max(1, per_render // 2) if load.busy() else per_render


def _has_encoder() -> set:
    if _listed[0] is None:
        try:
            out = subprocess.run(
                ["ffmpeg", "-hide_banner", "-encoders"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=NO_WINDOW,
                timeout=30,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            out = ""
        names = set()
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0].startswith(("V", "A", "S")):
                names.add(parts[1])
        _listed[0] = names
    return _listed[0]


def encoders() -> list:
    forced = (config.env("RONBCLIPS_ENCODER") or "").strip().lower()
    have = _has_encoder()
    if forced in ENCODERS:
        order = [forced]
    else:
        order = [key for key in ENCODER_ORDER if ENCODERS[key][0] in have]
    if "x264" not in order:
        order.append("x264")
    out = []
    for key in order:
        if key in _dead:
            continue
        args = [ENCODERS[key][1], "-c:v", ENCODERS[key][0], *ENCODERS[key][2]]
        if key == "x264":  # hardware encoders choose their own number of threads
            args += ["-threads", str(ffmpeg_threads())]
        out.append((key, args))
    return out


def _same_disk(a: Path, b: Path) -> bool:
    def volume(p: Path):
        p = Path(p)
        for _ in range(8):
            try:
                return os.stat(p).st_dev
            except OSError:
                if p == p.parent:
                    return None
                p = p.parent
        return None

    one, two = volume(a), volume(b)
    return one is None or two is None or one == two


def render_clip(src: Path, out_dir: Path, name: str, clip: dict, words: list, src_w: int, src_h: int) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    start, end = clip["start"], clip["end"]
    layout, fg_h, hook_y, cap_y = geometry(src_w, src_h, clip.get("layout", "blur"))
    face = clip.get("facecam")
    if layout == "gameplay" and not (face and len(face) == 4):
        layout = "crop"
    hook_y = _hook_y(layout, hook_y, face)
    ass = build_ass(
        words if clip.get("captions", True) else [],
        start,
        end,
        clip.get("hook", ""),
        clip.get("style", "bounce"),
        clip.get("highlight_words", []),
        hook_y,
        cap_y,
    )
    work = Path(tempfile.mkdtemp(prefix="pk"))
    try:
        return _render_in(work, src, out_dir, name, start, end, layout, fg_h, ass, face=face)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _render_in(work: Path, src: Path, out_dir: Path, name: str, start, end, layout, fg_h, ass: str, face=None) -> dict:
    (work / f"{name}.ass").write_text(ass, encoding="utf-8")
    (work / "f").mkdir()
    for font in config.FONTS_DIR.glob("*.ttf"):
        try:
            os.link(font, work / "f" / font.name)
        except OSError:
            shutil.copyfile(font, work / "f" / font.name)
    fc = _filter(layout, fg_h, face) + f"[v0]ass={name}.ass:fontsdir=f[v]"
    base = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-filter_threads",
        str(ffmpeg_threads()),
        "-ss",
        f"{start:.2f}",
        "-t",
        f"{end - start:.2f}",
        "-i",
        str(src),
        "-filter_complex",
        fc,
        "-map",
        "[v]",
        "-map",
        "0:a:0",
        "-af",
        "loudnorm=I=-14:TP=-1.5:LRA=11",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-ar",
        "48000",
        "-movflags",
        "+faststart",
        "-pix_fmt",
    ]
    final = out_dir / f"{name}.mp4"
    in_work = _same_disk(work, out_dir) or len(str(final)) > 180
    target = Path(f"{name}.mp4") if in_work else final
    error = None
    for label, encoder in encoders():
        proc = subprocess.run(
            base + encoder + [str(target)],
            cwd=work,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=NO_WINDOW,
            timeout=RENDER_SECONDS,
        )
        if proc.returncode == 0:
            break
        error = proc.stderr[-1200:]
        if label != "x264":
            _dead.add(label)
    else:
        if not in_work:
            final.unlink(missing_ok=True)
        raise RuntimeError(f"Render failed: {error}")
    cover_t = min(1.2, (end - start) / 2)
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{cover_t:.2f}", "-i", str(target), "-frames:v", "1", "-q:v", "3", f"{name}.jpg"], cwd=work)
    if in_work:
        shutil.move(str(work / f"{name}.mp4"), str(final))
    for ext in ("jpg", "ass"):
        shutil.move(str(work / f"{name}.{ext}"), str(out_dir / f"{name}.{ext}"))
    return {"file": f"{name}.mp4", "cover": f"{name}.jpg", "layout": layout}
