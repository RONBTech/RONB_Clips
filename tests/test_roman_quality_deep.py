"""Deep checks of Roman-letter captions, ad-break detection and game names.

Everything here is local: transliteration is pure text, ffmpeg's motion report and AI answers are replaced with
fixed values, and all files are throwaway. Runs against the code directly, with its own empty data folder.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

DATA = Path(tempfile.mkdtemp(prefix="ronbclips-roman-quality-deep-"))
os.environ["RONBCLIPS_DATA"] = str(DATA)
os.environ.update(
    DATABASE_URL=os.environ.get("RONBCLIPS_TEST_DATABASE_URL", ""),
    RONBCLIPS_FETCH_MODEL="0",
    RONBCLIPS_UPDATE_CHECK="0",
    PYTHONIOENCODING="utf-8",
)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):  # these captions are not printable on a Western Windows console
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from studio import config, games, quality, roman  # noqa: E402

fails = []


def check(name, got, expected):
    ok = got == expected
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  got {got!r}, expected {expected!r}"))
    if not ok:
        fails.append(name)


# =========================================================================== roman
check("Devanagari is noticed", roman.has_hindi("नेपाल"), True)
check("Latin text is not Devanagari", roman.has_hindi("namaste"), False)
check("a caption font can draw Latin, accents and digits", roman.drawable("café 12!"), True)
check("a caption font cannot draw Devanagari", roman.drawable("हिन्दी"), False)
check("a caption font cannot draw Cyrillic", roman.drawable("Привет"), False)
check("Russian and Korean letters need Roman letters too", (roman.needs_roman("Привет"), roman.needs_roman("안녕")), (True, True))
check("an accented Latin word is left alone", roman.needs_roman("café"), False)

# Everyday spellings, long vowels, nukta letters, viramas and the word list.
for source, want in (
    ("नेपाल", "nepaal"),
    ("काम", "kaam"),
    ("खाना", "khana"),
    ("जान्छु", "jaanchhu"),
    ("ॐ", "om"),
    ("१२३", "123"),
    ("॥", "."),
    ("क्", "k"),
    ("ज़", "za"),
    ("क़", "qa"),
    ("अ", "a"),
    ("ऋ", "ri"),
    ("अँ", "an"),
    ("सं", "san"),
    ("हूँ", "hoon"),
    ("चूना", "chuna"),
    ("कहना", "kahna"),
    ("बात", "baat"),
    ("हमारा", "hamara"),
    ("अरेचूना", "arechuna"),
    ("क्या", "kya"),
):
    check(f"{source} is spelled {want}", roman.to_roman(source), want)

check("Hindi guide spellings are only used for a Hindi video", (roman.to_roman("ज्यादा"), roman.to_roman("ज्यादा", "hi")), ("jyada", "zyada"))
check("Hindi guide spellings use everyday words too", roman.to_roman("सच में", "hi"), "sach mein")
check("Nepali has its own everyday spellings", roman.to_roman("पानी", "ne"), "paani")
check("Nepali words with a final න් are spelled out", roman.to_roman("तपाईंलाई", "ne"), "tapailai")
check("a full stop stuck to a word is kept", roman.to_roman("लाग्यो।"), "laagyo.")
check("a double danda becomes a full stop", roman.to_roman("बात॥"), "baat.")
check("a nasal with no letter before it is a consonant", roman._word("ं"), "n")
check("a stray vowel sign is ignored", roman._word("़क"), "ka")
check("a letter unknown to the rules is left in place", roman.to_roman("क©"), "ka©")
check("an unknown mark inside a Hindi word is kept", roman._word("क॰"), "ka॰")
check("a capital Cyrillic word keeps its capitals in Roman letters", roman.to_roman("ПРИВЕТ"), "PRIVET")
check("a mark in a Cyrillic run that has no rule is kept", roman._letters("A!"), "A!")
check("a joiner inside a verb does not split it", roman.to_roman("गर्‍यौ", "ne"), "garyau")

check("a Roman-script word is left alone", roman.to_roman("Hello there, 25 cars!"), "Hello there, 25 cars!")
check("a language tag with a country still selects the guide", roman.to_roman("ज्यादा", "hi-IN"), "zyada")
check("Nepali tags with a country still select the guide", roman.to_roman("पानी", "ne-IN"), "paani")
check("a language tag is case-insensitive", roman.to_roman("ज्यादा", "HI"), "zyada")

check("mixed scripts are all spelled, one by one", roman.to_roman("GTA में 1000 से MORE"), "GTA mein 1000 se MORE")
check("Hindi and Urdu in one line both come out", roman.to_roman("नमस्ते یار"), "namaste yaar")
check(
    "Hindi, Greek, Russian, Korean and Urdu in one line",
    roman.to_roman("नमस्ते Привет Αθήνα 안녕 مرحبا"),
    "namaste Privet Athina annyeong marhba",
)
check("a script nobody has rules for is left alone", roman.to_roman("中文ok"), "中文ok")
check("a Cyrillic soft sign disappears", roman.to_roman("Привет"), "Privet")
check("a Greek accent is dropped", roman.to_roman("Αθήνα"), "Athina")
check("a Korean syllable is spelled in parts", roman.to_roman("안녕하세요"), "annyeonghaseyo")
check("empty text is empty", roman.to_roman(""), "")
check("None is empty text", roman.to_roman(None), "")

# Urdu: the guide's words, the letter rules, marks, digits and odd letters.
for source, want in (
    ("گھر", "ghar"),
    ("کہ", "ke"),
    ("بچہ", "bacha"),
    ("گئی", "gayi"),
    ("وہ", "wo"),
    ("ایک", "ek"),
    ("دو", "do"),
    ("تین", "teen"),
    ("چار", "char"),
    ("پانچ", "panch"),
    ("کتاب", "katab"),
    ("علی", "li"),
    ("پھر", "phir"),
    ("ں", "n"),
):
    check(f"Urdu {source} is spelled {want}", roman.to_roman(source), want)

check("Roman Urdu follows its guide's everyday spellings", (roman.to_roman("کیسے"), roman.to_roman("کیسے", "ur")), ("keese", "kaise"))
check("Roman Urdu has its own word for very", (roman.to_roman("بہت"), roman.to_roman("بہت", "ur")), ("bahut", "bohat"))
check("an Urdu language tag with a country works", roman.to_roman("بہت", "ur-PK"), "bohat")
check("Urdu punctuation and digits become plain ones", roman.to_roman("ہاں، ۱۰۰۰ روپے؟"), "haan, 1000 rupay?")
check("Eastern Arabic-Indic digits are read too", roman._urdu_word("۱۲۳"), "123")
check("a diacritic is spelled as its sound", roman._urdu_word("کَتَب"), "katab")
check("a final nasal mark is spelled", roman._urdu_word("کتابًا"), "katabana")
check("a shadda and a sukun do not add letters", roman._urdu_word("شَدّ"), "shad")
check("a letter with no known sound is left alone", roman._urdu_word("汇款"), "汇款")
check("a consonant after another gets the short a", roman._urdu_word("چکل"), "chakl")
check("a chashmi he is the h of a breathed consonant", roman._urdu_word("بھاگ"), "bhag")
check("a w at the start of a word is a w", roman._urdu_word("واقف"), "waqf")
check("a w after a consonant is an o", roman._urdu_word("دوست"), "dost")
check("a y at the start of a word is a y", roman._urdu_word("یعنی"), "yni")
check("a word of consonants only gets the vowel Urdu leaves out", roman._urdu_word("کھ"), "kah")
check("the guide's spellings are used on free-standing words", roman._urdu_word("پاکستان", "ur"), "Pakistan")

# The word list carries timings through unchanged.
timed = [
    {"w": "नमस्ते", "s": 1.25, "e": 1.75, "p": 0.9},
    {"w": "hello", "s": 1.75, "e": 2.0},
    {"w": "یار", "s": 2.0, "e": 2.4},
]
out = roman.words(timed)
check(
    "caption words are spelled without changing their times",
    [(w["w"], w["s"], w["e"]) for w in out],
    [("namaste", 1.25, 1.75), ("hello", 1.75, 2.0), ("yaar", 2.0, 2.4)],
)
check("other word details are kept", out[0]["p"], 0.9)
check("Nepali words use their own spellings", roman.words([{"w": "पानी", "s": 0, "e": 1}], "ne")[0]["w"], "paani")
check("a Latin word is the same object as before", roman.words(timed)[1] is timed[1], True)
check("an empty list stays empty", roman.words([]), [])

# =========================================================================== quality
FFMPEG_OUT = """some other metadata, not a measurement
frame:0 pts_time:0.0
lavfi.signalstats.YAVG=0.25
frame:1 pts_time:0.5
lavfi.signalstats.YAVG=1.0
frame:2 pts_time:1.0
lavfi.signalstats.YAVG=2.0
frame:3 pts_time:1.5
lavfi.signalstats.YAVG=4.0
frame:4 pts_time:2.0
lavfi.signalstats.YAVG=6.0
"""


def fake_motion(out=FFMPEG_OUT, error=None):
    def run(cmd, **kw):
        if error:
            raise error
        return SimpleNamespace(stdout=out)

    return run


with patch.object(quality.subprocess, "run", fake_motion()):
    check("the motion map buckets frames by time", quality.motion_map("x", bucket=1.0), [(0.0, 1.0), (1.0, 3.0), (2.0, 6.0)])
    check("a bigger bucket joins the measurements", quality.motion_map("x", bucket=3.0), [(0.0, (1.0 + 2.0 + 4.0 + 6.0) / 4)])

one = "frame:0 pts_time:1.0\nlavfi.signalstats.YAVG=3.0\n"
with patch.object(quality.subprocess, "run", fake_motion(one)):
    check("a single measurement is not dropped as the first frame", quality.motion_map("x"), [(0.0, 3.0)])
with patch.object(quality.subprocess, "run", fake_motion("frame:1 pts_time:1.0\n")):
    check("no luma measurements give no map", quality.motion_map("x"), [])
with patch.object(quality.subprocess, "run", fake_motion(error=OSError("no ffmpeg"))):
    check("no ffmpeg gives no map", quality.motion_map("x"), [])
with patch.object(quality.subprocess, "run", side_effect=fake_motion()) as motion_run:
    quality.motion_map("the video.mp4")
    check(
        "ffmpeg reads the whole video quietly",
        (motion_run.call_args.args[0][0], "-loglevel" in motion_run.call_args.args[0], motion_run.call_args.kwargs["timeout"]),
        ("ffmpeg", True, 900),
    )

pairs = [(0.0, 0.1), (2.0, 0.1), (4.0, 0.1), (6.0, 2.0), (8.0, 2.0), (10.0, 0.1)]
with patch.object(quality, "motion_map", return_value=pairs):
    check(
        "a slate ends where the picture starts moving again",
        quality.static_ranges("x", bucket=2.0, threshold=1.0, min_seconds=2.0),
        [(0.0, 6.0), (10.0, 12.0)],
    )
    check(
        "a stretch shorter than the minimum is ignored",
        quality.static_ranges("x", bucket=2.0, threshold=1.0, min_seconds=3.0),
        [(0.0, 6.0)],
    )
    check(
        "the configured threshold is used when none is given",
        quality.static_ranges("x", bucket=2.0, min_seconds=2.0)[0][0] in (0.0, 2.0, 4.0),
        True,
    )
with patch.object(quality, "motion_map", return_value=[]):
    check("an unreadable video has no static stretches", quality.static_ranges("x"), [])
with patch.object(quality, "motion_map", return_value=[(0.0, 0.1), (2.0, 0.1)]):
    check("the configured minimum comes from config", quality.static_ranges("x"), [] if config.STATIC_MIN_SECONDS > 2 else [(0.0, 4.0)])
with patch.object(quality, "motion_map", return_value=[(0.0, 2.0), (2.0, 3.0)]):
    check("a video that never freezes has no slate", quality.static_ranges("x"), [])

ranges = [(2.0, 5.0), (8.0, 10.0)]
check("a clip covering two slates is mostly static", quality.static_overlap(0.0, 12.0, ranges), 5.0)
check("a clip between the slates is clean", quality.static_overlap(5.0, 8.0, ranges), 0.0)
check("a clip crossing a slate counts only the overlap", quality.static_overlap(3.0, 9.0, ranges), 3.0)
check("no slates mean no overlap", quality.static_overlap(0.0, 12.0, []), 0.0)
check("a clip that ends before the slate does not overlap", quality.static_overlap(0.0, 2.0, ranges), 0.0)

# =========================================================================== games
check("an empty title names no game", games.detect(), (None, []))
check("None and empty text name no game", games.detect(None, ""), (None, []))
check("several pieces of text are searched together", games.detect("streaming", "some GTA6 gameplay")[0], "Grand Theft Auto VI")
check("a title with no game in it has no tags either", games.detect("just chatting with friends"), (None, []))
check("punctuation does not hide a game", games.detect("Valorant!")[0], "Valorant")
check("a tag is enough to name a game", games.name_of("GTA6"), "Grand Theft Auto VI")
check("a spelling is enough to name a game", games.name_of("gta vi"), "Grand Theft Auto VI")
check("no name is empty", games.name_of(None), "")
check("a long unknown name is cut short", len(games.name_of("A" * 60)), 40)
check("no game has no tags", games.tags_of(None), [])
check("a tag finds the game's tags", games.tags_of("MinecraftGame"), ["Minecraft", "MinecraftGame"])
check("a name with punctuation still finds its tags", games.tags_of("Cyberpunk-2077!"), ["Cyberpunk2077", "NightCity"])
check("an unknown game gets one clean tag", games.tags_of("!!!"), [])
check("an unknown game with a name gets a short tag", games.tags_of("My New Game"), ["MyNewGame"])
check("normalising nothing gives nothing", games._norm(None), "")

shutil.rmtree(DATA, ignore_errors=True)
print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
