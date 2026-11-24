"""A live clip must contain the moment that caused it, not only the reaction after it.

The bug this covers: a silent play (a landing, a goal, a jump scare) leaves a gap in the transcript, the stream
gets loud, and the AI picks the first word spoken after it. The clip then opens on the shout, with the play
itself left out. Every clip triggered by a jump is pulled back to start before it.

Runs against the code directly, with its own empty data folder and the AI replaced by a fixed answer."""

import os
import sys
import tempfile

os.environ["RONBCLIPS_DATA"] = tempfile.mkdtemp(prefix="pc-live-")
os.environ["RONBCLIPS_AI_KEY"] = ""
sys.path.insert(0, ".")
from unittest.mock import patch

from studio import brain, live

fails = []


def check(n, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else f"   got {got!r}"))
    ok or fails.append(n)


ANCHOR = 120.0  # the second the jump was noticed at


class Fake(live.Session):
    """Just enough of a recording session for one AI check, with the clip added to a list instead of rendered."""

    def __init__(self):
        self.settings = {
            "ai_every_min": 1,
            "min_len": 15,
            "max_len": 60,
            "min_score": 65,
            "live_per_hour": 4,
            "must_clip_min": 0,
            "decide_mode": "both",
            "min_gap_min": 0,
        }
        self.words = [{"w": "we", "s": 100.0 + i, "e": 100.4 + i} for i in range(6)] + [
            {"w": "oh", "s": ANCHOR + 0.3, "e": ANCHOR + 0.6},
            {"w": "no", "s": ANCHOR + 0.7, "e": ANCHOR + 1.0},
            {"w": "way", "s": ANCHOR + 1.1, "e": ANCHOR + 1.4},
        ]
        self.job = {"clips": [], "title": "T", "uploader": "U", "creator": "c1", "ai": {"last_error": ""}}
        self.db, self.chat, self.chat_on, self.chat_total, self.chat_users = [], {}, False, 0, {}
        self.lock = live.threading.RLock()
        self.t_done, self.last_check, self.started_at = ANCHOR + 5, 0.0, 0.0
        self.skipped = self.checks = self.tokens = self.rejected = 0
        self.verdicts, self.made = [], []

    def _game(self):
        return None

    def _verdict(self, v, result):
        self.verdicts.append(dict(v, result=result))

    def _add_clip(self, c):
        self.made.append(c)


def run(session, **kw):
    # The words judge answers with the moment as the AI would see it: the first words after the silent play.
    answer = ([{"start": ANCHOR + 0.3, "end": ANCHOR + 20, "score": 90, "reason": "shouting"}], 10)
    with (
        patch.object(brain, "judge_transcript", return_value=answer),
        patch.object(brain, "judge_signals", return_value=([{"start": ANCHOR, "end": ANCHOR + 10, "score": 80, "reason": "loud"}], 10)),
    ):
        session._ai_check((ANCHOR - 30, ANCHOR + 20), "Jump: loudness +90%", **kw)


s = Fake()
run(s, anchor=ANCHOR)
check("the clip is made", len(s.made), 1)
check("...and starts before the jump, not on the reaction", s.made[0]["start"] <= ANCHOR - 11.9)
check("...while still covering the reaction", s.made[0]["end"] > ANCHOR + 0.3)

s2 = Fake()
run(s2)  # an ordinary check: no jump, so the AI's own start is kept
check("a normal check keeps the start the AI chose", s2.made and s2.made[0]["start"], ANCHOR + 0.3)

print("ALL PASSED" if not fails else f"{len(fails)} failed")
import shutil

shutil.rmtree(os.environ["RONBCLIPS_DATA"], ignore_errors=True)
sys.exit(1 if fails else 0)
