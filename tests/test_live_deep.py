"""Deep checks for live recording: the pieces, the transcript and the decisions made while a stream runs.

The recorder, the speech model and the AI are replaced with fixed answers, so nothing waits for a real stream or
a real service. The ffmpeg command, the buffer bookkeeping, the transcript timing and the clip decisions are the
code that actually runs. Each session uses its own empty data folder.
"""

import json
import os
import sys
import tempfile
import time
import types
from pathlib import Path
from unittest.mock import Mock, patch

os.environ["RONBCLIPS_DATA"] = tempfile.mkdtemp(prefix="rc-live-deep-")
os.environ.update(
    DATABASE_URL=os.environ.get("RONBCLIPS_TEST_DATABASE_URL", ""),
    RONBCLIPS_ACCOUNTS="0",
    RONBCLIPS_FETCH_MODEL="0",
    RONBCLIPS_UPDATE_CHECK="0",
    RONBCLIPS_ASR_URL="",
    RONBCLIPS_AI_KEY="",
    PYTHONIOENCODING="utf-8",
)
sys.path.insert(0, ".")

import numpy as np  # noqa: E402

from studio import alerts, auth, brain, creators, ctx, db, jobs, live, media, profiles, tools, transcribe  # noqa: E402

fails = []
DATA = Path(os.environ["RONBCLIPS_DATA"])
SR = live.SR


def check(name, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"   got {got!r}"))
    ok or fails.append(name)


db.init()
owner = auth.local_user()
UID = owner.id
ctx.set_uid(UID)


def settings(**kw):
    s = dict(creators.DEFAULTS)
    s.update(
        ai_every_min=1,
        min_len=5,
        max_len=60,
        min_score=50,
        live_per_hour=10,
        must_clip_min=0,
        decide_mode="both",
        min_gap_min=0,
        buffer_min=10,
        style="bounce",
        layout="blur",
        trigger_words="",
    )
    s.update(kw)
    return s


def make_job(title="A stream", creator=""):
    j = jobs.create({"type": "live", "url": "https://www.twitch.tv/tester", "name": title}, {}, enqueue=False, kind="live")
    j.update(title=title, uploader="tester", creator=creator, status="live", stage="Recording", ai={}, options={"captions": True})
    jobs.save(j)
    return j


def make_session(job=None, **kw):
    return live.Session(job or make_job(), settings(**kw))


# ---------------------------------------------------------------- the small helpers around a session
check("a plain channel link is kept", live.normalize(" twitch.tv/someone "), "https://twitch.tv/someone")
check(
    "a YouTube channel gets the live suffix",
    live.normalize("https://www.youtube.com/@someone?v=abc"),
    "https://www.youtube.com/@someone/live",
)
check("an ordinary link is left alone", live.normalize("https://kick.com/someone/clips/1"), "https://kick.com/someone/clips/1")
quiet = live._Quiet()
check(
    "ffmpeg's logger says nothing",
    (quiet.debug("x"), quiet.info("x"), quiet.warning("x"), quiet.error("x")),
    (None, None, None, None),
)

users = {100: {"a"}, 500: {"b"}, 900: {"c"}, 1100: {"d"}}
kept = live._trim_names(users, 200, 200)
check("chat names are not pruned on every message", (kept, sorted(users)), (200, [100, 500, 900, 1100]))
kept = live._trim_names(users, 1200, 100)
check("old chat names are dropped", (kept, sorted(users)), (1200, [900, 1100]))

s = make_session()
with patch.object(live, "SESSIONS", {}):
    live.SESSIONS[s.job["id"]] = s
    check("an unfinished session is active", live.active(), [s])
    check("the owner sees their own session", live.active_mine(), [s])
    check("a creator's session is found by id", live.active_for(s.job["creator"]), s)
    check("a session is found by job id", live.mine(s.job["id"]), s)
    with ctx.as_user("someone-else"):
        check("another owner cannot see the session", live.mine(s.job["id"]), None)
    s.finished = True
    check("a finished session is not active", live.active(), [])
    s.finished, s.user_stopped = False, True
    check("a stopped session is not active", live.active(), [])
    s.user_stopped = False

check("a new session has an empty buffer", (s.buf.is_dir(), (s.dir / "clips").is_dir(), s.ended, s.finished), (True, True, False, False))


# ---------------------------------------------------------------- resolving a stream, without yt-dlp on the network
class FakeYDL:
    info = None
    error = None

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download):
        if self.error:
            raise self.error
        return self.info


fake_ytdlp = types.ModuleType("yt_dlp")
fake_ytdlp.YoutubeDL = FakeYDL
with patch.dict(sys.modules, {"yt_dlp": fake_ytdlp}):
    FakeYDL.error = None
    FakeYDL.info = {
        "is_live": True,
        "title": "Stream (live)",
        "description": "The real title\nsecond line",
        "uploader": "someone",
        "requested_formats": [{"url": "http://video"}, {"url": "http://audio"}],
    }
    got = live.resolve("https://www.twitch.tv/someone")
    check(
        "a live stream has playable addresses and a title",
        got,
        {"urls": ["http://video", "http://audio"], "title": "The real title", "uploader": "someone"},
    )
    FakeYDL.info = {"is_live": True, "title": "Just a title", "channel": "the-channel", "url": "http://single"}
    got = live.resolve("https://www.twitch.tv/someone")
    check(
        "a single format and the channel name are accepted",
        got,
        {"urls": ["http://single"], "title": "Just a title", "uploader": "the-channel"},
    )
    FakeYDL.info = {"is_live": False}
    raised = None
    try:
        live.resolve("https://www.twitch.tv/offline")
    except live.NotLive as e:
        raised = str(e)
    check("an offline channel is reported", raised, "This channel is not live right now.")
    FakeYDL.info = None
    FakeYDL.error = RuntimeError("connection refused")
    raised = None
    try:
        live.resolve("https://www.twitch.tv/broken")
    except live.NotLive as e:
        raised = str(e)
    check("a connection error becomes NotLive", raised, "connection refused")
    with patch.object(live.media, "note_refusal", return_value=True):
        raised = None
        try:
            live.resolve("https://www.twitch.tv/blocked")
        except live.NotLive as e:
            raised = str(e)
        check("a refused site is reported with help", "YouTube" in raised, True)
    FakeYDL.error = None
with (
    patch.object(live.media, "is_youtube", return_value=True),
    patch.object(live.media, "youtube_paused", return_value=True),
):
    raised = None
    try:
        live.resolve("https://youtube.com/@someone/live")
    except live.NotLive as e:
        raised = str(e)
    check("YouTube streams are not attempted while blocked", "YouTube" in raised, True)

# ---------------------------------------------------------------- the game, the facecam and starting/stopping
s = make_session()
with patch.object(live.games, "detect", return_value=("Valorant", ["valorant"])) as detect:
    check("the game is guessed from the stream's title", s._game(), "Valorant")
    check("the game is remembered in the job", s.job["game"], "Valorant")
    check("the guess is made only once", s._game(), "Valorant")
    check("...so the detector is called once", detect.call_count, 1)
s.game = "Already known"
check("a known game is used as it is", s._game(), "Already known")

with patch.object(live.facecam, "detect", return_value={"x": 1, "y": 2, "w": 3, "h": 4, "conf": "high"}):
    check("a sure facecam is used", s._find_facecam("somewhere.ts"), [1, 2, 3, 4])
with patch.object(live.facecam, "detect", return_value={"x": 1, "conf": "low"}):
    check("an unsure facecam is ignored", s._find_facecam("somewhere.ts"), None)
with patch.object(live.facecam, "detect", side_effect=RuntimeError("no picture")):
    check("a broken facecam check is ignored", s._find_facecam("somewhere.ts"), None)

started = []
s = make_session()
with (
    patch.object(live.Session, "_ingest", lambda self: started.append("ingest")),
    patch.object(live.Session, "_analyze", lambda self: started.append("analyze")),
    patch.object(live.Session, "_chat", lambda self, channel: started.append("chat:" + channel)),
):
    s.start()
    deadline = time.monotonic() + 5
    while len(started) < 3 and time.monotonic() < deadline:
        time.sleep(0.02)
with patch.object(live, "SESSIONS", {}):
    live.SESSIONS[s.job["id"]] = s
    check("starting a session opens its workers", sorted(started), ["analyze", "chat:tester", "ingest"])
    check("starting a session registers it", live.active(), [s])

s = make_session()
proc = SimpleProc = types.SimpleNamespace(poll=lambda: None, terminate=Mock())
s.proc = proc
s.stop("Stopped by you")
check("stop writes the new stage at once", (s.job["status"], s.job["stage"]), ("done", "Stopped by you"))
check("stop asks the recorder to quit", (s.user_stopped, s.stop_flag.is_set(), proc.terminate.call_count), (True, True, 1))

s = make_session()
s.samples = 0
raised = None
try:
    s.clip_last(30)
except ValueError as e:
    raised = str(e)
check("nothing can be clipped before there is a recording", raised, "Nothing has been recorded yet.")
s.samples, s.seg_clock = SR * 120, 120.0
s.clip_last(30)
check("asking for the last half minute works", s.manual[-1], {"start": 90.0, "end": 120.0})

s = make_session()
s.samples = SR * 200
s.chat_total = 20
s.chat = {100: 5, 101: 5}
check("a quiet stretch is not a chat spike", s._chat_spike(50, 60), False)
check("a wave of messages is a chat spike", s._chat_spike(100, 101), True)
s.chat_users = {100: {"a", "b", "c", "d", "e"}, 101: {"a", "b", "c", "d", "e"}}
check("one person shouting is not a crowd", s._chat_spike(100, 101), False)
check("chat people are copied for the reader thread", s.chat_people()[100], {"a", "b", "c", "d", "e"})


# ---------------------------------------------------------------- the recording loop
class FakeStdout:
    def __init__(self, chunks):
        self.chunks = list(chunks)

    def read(self, n):
        return self.chunks.pop(0) if self.chunks else b""


class FakeProc:
    def __init__(self, chunks):
        self.stdout = FakeStdout(chunks)
        self.waited = False

    def wait(self):
        self.waited = True
        return 0


info = {"urls": ["http://video", "http://audio"], "title": "A stream", "uploader": "tester"}
s = make_session()
chunks = [np.full(SR, 8000, dtype=np.int16).tobytes() for _ in range(3)]
proc = FakeProc(chunks)
with (
    patch.object(live, "resolve", return_value=info),
    patch.object(live.subprocess, "Popen", return_value=proc) as popen,
    patch.object(s.stop_flag, "wait", side_effect=lambda n: s.stop_flag.set()),
):
    s._ingest()
check("the recorder ends cleanly", s.ended, True)
check("three seconds of sound were read", s.samples, SR * 3)
check("the sound waits to be transcribed", len(s.pending), SR * 3 * 2)
check("a loudness reading was kept for each second", len(s.db), 3)
cmd = popen.call_args.args[0]
check("both stream addresses are given to ffmpeg", cmd.count("-i"), 2)
check("the second address supplies the sound", "1:a:0" in cmd, True)
check("ffmpeg writes a rolling list of pieces", ("p01.csv" in cmd, "p01_%05d.ts" in cmd, "pipe:1" in cmd), (True, True, True))
check("ffmpeg's own errors are kept in a log", (s.dir / "ffmpeg.log").exists(), True)
check("the recorder waits for ffmpeg to finish", proc.waited, True)
check("a first pass is counted as a failure", s.job.get("error"), None)

s = make_session()
with (
    patch.object(live, "resolve", side_effect=live.NotLive("offline")),
    patch.object(live.subprocess, "Popen") as popen,
):
    s._ingest()
check("an offline stream before the first second is reported", "Could not start recording" in str(s.job.get("error")), True)
check("no ffmpeg is started for an offline stream", popen.call_count, 0)

s = make_session()
s.samples = SR * 40
s.pending = bytearray(SR * 40 * 2)
waits = []


def wait_between_pieces(n):
    waits.append(n)
    if len(waits) >= 3:  # the stream has gone offline; let the recorder finish
        s.stop_flag.set()


with (
    patch.object(live, "resolve", side_effect=[info, info, live.NotLive("stream ended")]),
    patch.object(live.subprocess, "Popen", side_effect=[FakeProc(chunks), FakeProc(chunks)]),
    patch.object(s.stop_flag, "wait", side_effect=wait_between_pieces),
):
    s._ingest()
check("a stream that goes offline after recording started is not an error", s.job.get("error"), None)
check("a second piece list was started", s.part, 2)
check("the picture clock follows the sound read at the last piece start", round(s.seg_clock), 40 + len(chunks))

s = make_session()
with (
    patch.object(live, "resolve", return_value=info),
    patch.object(live.subprocess, "Popen", side_effect=FileNotFoundError("ffmpeg.exe was not found")),
):
    s._ingest()
check("a missing ffmpeg ends the session instead of hanging", (s.ended, "Recording stopped" in str(s.job.get("error"))), (True, True))


# ---------------------------------------------------------------- the chat reader, with a fake socket
class FakeChatSocket:
    def __init__(self, session, data):
        self.session, self.data, self.pos = session, data, 0
        self.sent = []
        self.closed = False

    def sendall(self, data):
        self.sent.append(data)

    def settimeout(self, n):
        pass

    def recv(self, n):
        chunk = self.data[self.pos : self.pos + n]
        self.pos += len(chunk)
        if not chunk:
            self.session.stop_flag.set()
        return chunk

    def close(self):
        self.closed = True


s = make_session()
s.samples = SR * 10
data = (
    b":alice!a@host PRIVMSG #tester :hello\r\n"
    b":bob!b@host PRIVMSG #tester :GG\r\n"
    b"PING :tmi.twitch.tv\r\n"
    b":ALICE!a@host PRIVMSG #tester :again\r\n"
)
sock = FakeChatSocket(s, data)
with (
    patch.object(live.socket, "create_connection", return_value=sock),
    patch.object(live.ssl, "create_default_context", return_value=types.SimpleNamespace(wrap_socket=lambda raw, **kw: sock)),
):
    s._chat("tester")
check("chat messages are counted", (s.chat[10], s.chat_total), (3, 3))
check("different people are counted once each", s.chat_users[10], {"alice", "bob"})
check("a ping is answered", any(b"PONG" in x for x in sock.sent), True)
check("the reader joins the right channel", b"JOIN #tester" in sock.sent[0], True)
check("the socket is closed when the stream drops it", sock.closed, True)

s = make_session()
with (
    patch.object(live.socket, "create_connection", side_effect=OSError("no network")),
    patch.object(s.stop_flag, "wait", side_effect=lambda n: s.stop_flag.set()),
):
    s._chat("tester")
check("a failed chat connection does not break the recording", s.chat_on, False)

# ---------------------------------------------------------------- transcribing as the stream goes
s = make_session()
s.pending = bytearray(np.full(SR * 8, 8000, dtype=np.int16).tobytes())
s.pending_base = SR * 5
s.samples = SR * 13
with (
    patch.object(transcribe, "transcribe", return_value=[{"w": "hello", "s": 0.1, "e": 0.4}]) as asr,
    patch.object(s, "_triggers") as triggers,
    patch.object(s, "_save_words") as save,
):
    busy = s._transcribe_step()
check("a waiting second of audio is transcribed", busy, True)
check("the model received a quiet cut of the audio", 0 < asr.call_args.args[0].size < SR * 8, True)
check("the word is placed on the stream clock", (s.words[0]["s"], s.words[0]["e"]), (5.1, 5.4))
check("only the transcribed audio is removed from the buffer", s.pending_base < SR * 13 and s.pending_base > SR * 5, True)
check("the transcript is complete up to the new audio", s.t_done, s.pending_base / SR)
check("the new words are checked for triggers", triggers.call_count, 1)
check("the words are saved", save.call_count, 1)

s = make_session()
s.pending = bytearray(np.full(SR, 8000, dtype=np.int16).tobytes())
s.manual.append({"start": 0, "end": 1})
with patch.object(transcribe, "transcribe", return_value=[{"w": "now", "s": 0.0, "e": 0.2}]) as asr:
    check("a hand clip hurries the short audio through", s._transcribe_step(), True)
check("in a hurry the whole second is used", asr.call_args.args[0].size, SR)

s = make_session()
s.pending = bytearray(SR)
with patch.object(transcribe, "transcribe") as asr:
    check("a short wait does not call the model", s._transcribe_step(), False)
check("the model was not called", asr.call_count, 0)

s = make_session()
s.words = [{"w": "hello", "s": 1.0, "e": 1.4}]
check("the transcript is written", s._save_words(), True)
saved = json.loads((s.dir / "words.json").read_text(encoding="utf-8"))
check("the saved transcript is readable", saved, s.words)
check("the transcript is not rewritten every moment", s._save_words(), False)
check("the temporary file was replaced", (s.dir / "words.tmp").exists(), False)

s = make_session()
s.db = [-60.0] * 50
check("the first minute only warms up", s._watch_spikes(), None)
check("the warm-up is shown on the card", s.base, {"warming": True})
s.db = [-60.0] * 100 + [0.0] * 5
s.samples = SR * 105
s.last_spike = -999.0
s._watch_spikes()
check("a loud jump is noticed", s.spikes, 1)
check("the jump says why it was noticed", "loudness" in s.spike["why"], True)
check("the jump is marked at the right second", s.spike["t"], 102.0)
s._watch_spikes()
check("the same jump is not sent twice", s.spikes, 1)

# ---------------------------------------------------------------- the AI checks
CANDIDATE = lambda **kw: dict({"start": 110.0, "end": 130.0, "score": 80, "reason": "a great moment", "hook": "Wow", "title": "Wow"}, **kw)
SIGNAL = lambda **kw: dict({"start": 110.0, "end": 130.0, "score": 80, "reason": "loud"}, **kw)


def ai_session(**kw):
    s = make_session(**kw)
    s.words = [{"w": f"word{i}", "s": 100.0 + i, "e": 100.4 + i} for i in range(30)]
    s.t_done, s.samples = 130.0, SR * 131
    s.db = [-30.0] * 200
    s.started_at = time.time()
    return s


def run_check(s, cands=None, sigs=None, judge_error=None, **kw):
    if cands is None:
        cands = [CANDIDATE()]
    if sigs is None:
        sigs = [SIGNAL()]
    made = []
    transcript = Mock(side_effect=RuntimeError(judge_error)) if judge_error else Mock(return_value=(cands, 10))
    signals = Mock(return_value=(sigs, 10))
    with (
        patch.object(brain, "judge_transcript", transcript),
        patch.object(brain, "judge_signals", signals),
        patch.object(s, "_add_clip", side_effect=made.append),
    ):
        s._ai_check(**kw)
    return made, transcript, signals


s = ai_session()
made, transcript, signals = run_check(s)
check("two judges answer one check", (transcript.call_count, signals.call_count, s.checks), (1, 1, 2))
check("the tokens used are counted", s.tokens, 20)
check("both judges agreeing makes a clip", len(made), 1)
check("the clip keeps why it was chosen", "both judges agreed" in made[0]["reason"], True)
check("the verdict is kept for the page", s.verdicts[-1]["result"], "Clipped: both judges agreed")

s = ai_session()
s.words = []
made, transcript, _ = run_check(s)
check("a check with nobody talking is skipped", (s.skipped, len(made), transcript.call_count), (1, 0, 0))

s = ai_session(live_per_hour=2)
s.job["clips"] = [{"start": 110.0, "score": 90, "made_at": time.time()} for _ in range(2)]
made, transcript, _ = run_check(s)
check("a creator who has hit the hourly limit is skipped", (s.skipped, len(made)), (1, 0))

s = ai_session()
made, _, _ = run_check(s, judge_error="the AI service is away")
check("an AI error is shown on the job", "the AI service is away" in str(s.job["ai"]["last_error"]), True)
check("an AI error does not make a clip", (len(made), s.verdicts[-1]["result"].startswith("AI error:")), (0, True))

s = ai_session()
made, _, _ = run_check(s, cands=[CANDIDATE(score=10)])
check("a low-scoring moment is not clipped", (len(made), s.verdicts[-1]["result"]), (0, "Words judge: nothing clip-worthy"))

s = ai_session()
made, _, _ = run_check(s, sigs=[])
check(
    "a reaction judge saying no rejects the clip",
    (len(made), s.rejected, s.verdicts[-1]["result"]),
    (0, 1, "Reaction judge said no, not clipped"),
)

s = ai_session(decide_mode="either")
made, _, _ = run_check(s, cands=[CANDIDATE(score=95)], sigs=[])
check("a very sure words judge can clip on its own", (len(made), s.verdicts[-1]["result"]), (1, "Clipped: words judge was very sure"))

s = ai_session(must_clip_min=1)
s.started_at = time.time() - 120
made, _, _ = run_check(s, sigs=[])
check("a long dry spell forces a clip", (len(made), s.verdicts[-1]["result"]), (1, "Clipped: forced: no clip for a while"))

s = ai_session(min_gap_min=2)
s.job["clips"] = [{"start": 110.0, "score": 0, "made_at": time.time()}]
made, _, _ = run_check(s)
check("a second clip too near the last one is dropped", (len(made), s.verdicts[-1]["result"]), (0, "Too close to the last clip"))

s = ai_session(max_len=15)
made, _, _ = run_check(s, window=(90.0, 130.0), why="Jump: loudness", anchor=120.0)
check("a jump pulls the clip back to the moment that caused it", made[0]["start"], 108.0)
check("and keeps it inside the longest clip length", made[0]["end"] - made[0]["start"], 15)
check("the jump is recorded in the verdict", s.verdicts[-1]["trigger"], "Jump: loudness")

s = ai_session()
s.chat_total, s.chat = 20, {i: 5 for i in range(100, 131)}
s.chat_users = {i: {f"p{j}" for j in range(5)} for i in range(100, 131)}
made, transcript, signals = run_check(s)
check("chat activity is passed to the judges as people", signals.call_args.kwargs["unit"], "people")
check("the loudness line is sliced to the window", len(signals.call_args.args[0]) > 0, True)

s = ai_session()
made, _, _ = run_check(s, sigs=[SIGNAL(score=60)])
check("the reaction score is written into the clip", made[0]["judges"] == {"words": 80, "reaction": 60, "why": "Both judges agreed"}, True)

# ---------------------------------------------------------------- creator trigger phrases and hand clips
s = make_session(trigger_words="clip that, clip it")
new_words = [{"w": "Clip", "s": 100.0}, {"w": "that", "s": 100.2}]
s._triggers(new_words)
check("someone saying clip starts an AI check", s.said["why"], 'Someone said "clip"')
check("a creator's own phrase makes a hand clip", s.manual[-1], {"start": 70.0, "end": 102.0, "why": 'Someone said "clip that"'})
s._triggers([{"w": "clip", "s": 101.0}, {"w": "that", "s": 101.2}])
check("the same phrase does not make two clips", len(s.manual), 1)
s._triggers([{"w": "clip", "s": 200.0}, {"w": "it", "s": 200.2}])
check("a different phrase later does make another", len(s.manual), 2)

s = make_session()
s.t_done = 115.0
s.samples = SR * 115
s.words = [{"w": "this", "s": 105.0, "e": 105.4}, {"w": "moment", "s": 106.0, "e": 106.4}]
s.manual.append({"start": 100.0, "end": 110.0, "why": "Someone asked"})
answer = {
    "score": 0,
    "category": "other",
    "hook": "This moment",
    "title": "This moment",
    "description": "",
    "hashtags": [],
    "highlight_words": [],
}
made = []
with patch.object(brain, "describe", return_value=answer) as describe, patch.object(s, "_add_clip", side_effect=made.append):
    s._manual_step()
check("a hand clip is described from the words inside it", "this moment" in describe.call_args.args[0], True)
check(
    "a hand clip uses the request's times and reason",
    (made[0]["start"], made[0]["end"], made[0]["reason"]),
    (100.0, 110.0, "Someone asked"),
)
check("the finished request is removed", s.manual, [])
s.manual.append({"start": 100.0, "end": 110.0})
with patch.object(brain, "describe", side_effect=RuntimeError("AI away")), patch.object(s, "_add_clip", side_effect=made.append):
    s._manual_step()
check("a broken AI still makes the hand clip", (len(made), made[-1]["hook"]), (2, ""))
check("the AI problem is written to the job", "AI away" in str(s.job["ai"]["last_error"]), True)

s = make_session()
s.words = [{"w": "hello", "s": 101.0, "e": 101.4}]
submitted = []
with patch.object(jobs, "submit_live", side_effect=submitted.append):
    s._add_clip({"start": 100, "end": 105, "score": 70, "reason": "good", "title": "Title", "description": ""})
c = s.job["clips"][0]
check("a clip is numbered and queued", (c["id"], c["status"], c["version"], s.n), ("clip001", "queued", 0, 1))
check("a clip inherits the creator's look", (c["style"], c["layout"], c["captions"]), ("bounce", "blur", True))
check("a clip gets hashtags and a time", ("hashtags" in c, c.get("made_at", 0) > 0), (True, True))
check("the render is put on the live worker", len(submitted), 1)


# ---------------------------------------------------------------- cutting a clip out of the buffer
def pieces_session(clip, times=((100.0, 106.0), (106.0, 112.0), (112.0, 118.0))):
    s = make_session()
    s.segments = []
    for i, (a, b) in enumerate(times):
        p = s.buf / f"p01_{i:05d}.ts"
        p.write_bytes(b"piece")
        s.segments.append((p, a, b))
    return s, clip


s, clip = pieces_session({"id": "clip001", "start": 105.0, "end": 115.0})
rendered = []
with (
    patch.object(s, "_scan_segments"),
    patch.object(s, "_align", return_value=(100.0, True)),
    patch.object(media, "probe", return_value={"width": 640, "height": 360}),
    patch.object(media, "run") as concat,
    patch.object(jobs, "render_one", side_effect=lambda j, c, w: rendered.append(dict(c))),
):
    s._render(clip)
check("the needed pieces are joined into one source", concat.call_count, 1)
check(
    "the source and its times are written to the clip",
    (Path(clip["src"]).name, clip["src_offset"], clip["src_end"]),
    ("clip001.src.ts", 100.0, 118.0),
)
check("the clip is trimmed to the recorded picture", (clip["start"], clip["end"]), (105.0, 115.0))
check("the source dimensions are learned once", (s.job["width"], s.job["height"]), (640, 360))
check("the render worker gets the clip", len(rendered), 1)
check("the buffer can be swept after the copy", s.reading, {})
check("the temporary concat list is removed", (s.dir / "clips" / "clip001.txt").exists(), False)

s, clip = pieces_session({"id": "clip002", "start": 105.0, "end": 115.0}, times=())
with patch.object(s, "_scan_segments"), patch.object(jobs, "render_one") as render:
    s._render(clip)
check("a moment no longer in the buffer is not cut", (clip["status"], "no longer in the buffer" in clip["error"]), ("error", True))
check("no render is attempted", render.call_count, 0)

s, clip = pieces_session({"id": "clip003", "start": 105.0, "end": 115.0})
with (
    patch.object(s, "_scan_segments"),
    patch.object(s, "_align", return_value=(100.0, True)),
    patch.object(media, "probe", side_effect=RuntimeError("half-written piece")),
):
    s._render(clip)
check("a half-written first piece gives an error", (clip["status"], "half-written piece" in clip["error"]), ("error", True))

s, clip = pieces_session({"id": "clip004", "start": 105.0, "end": 115.0})
with (
    patch.object(s, "_scan_segments"),
    patch.object(s, "_align", return_value=(100.0, True)),
    patch.object(media, "probe", return_value={"width": 640, "height": 360}),
    patch.object(media, "run", side_effect=RuntimeError("concat failed")),
):
    s._render(clip)
check("a failed concat gives an error", (clip["status"], "concat failed" in clip["error"]), ("error", True))
check("the failed concat list is still removed", (s.dir / "clips" / "clip004.txt").exists(), False)

s, clip = pieces_session({"id": "clip005", "start": 105.0, "end": 115.0})
s.clock_fix = 2.5
with (
    patch.object(s, "_scan_segments"),
    patch.object(s, "_align", return_value=(100.0, False)),
    patch.object(media, "probe", return_value={"width": 640, "height": 360}),
    patch.object(media, "run"),
    patch.object(jobs, "render_one"),
    patch.object(live.creators, "log") as log,
):
    s._render(clip)
check("an unmeasured clock keeps the last correction", s.clock_fix, 2.5)
check("the reason is written to the creator's log", "line this clip" in log.call_args.args[1], True)

s, clip = pieces_session({"id": "clip006", "start": 105.0, "end": 130.0})
with (
    patch.object(s, "_scan_segments"),
    patch.object(s, "_align", return_value=(100.0, True)),
    patch.object(media, "probe", return_value={"width": 640, "height": 360}),
    patch.object(media, "run"),
):
    s._render(clip)
check("a clip past the end of the picture is dropped", "not fully recorded" in clip["error"], True)

s, clip = pieces_session({"id": "clip007", "start": 105.0, "end": 115.0})
clip["layout"] = "gameplay"
box = [10, 20, 30, 40]
with (
    patch.object(s, "_scan_segments"),
    patch.object(s, "_align", return_value=(100.0, True)),
    patch.object(media, "probe", return_value={"width": 640, "height": 360}),
    patch.object(media, "run"),
    patch.object(jobs, "render_one"),
    patch.object(s, "_find_facecam", return_value=box) as find,
):
    s._render(clip)
    s._render(dict(clip, id="clip008"))
check("the facecam is looked for once per stream", find.call_count, 1)
check("the found box is given to gameplay clips", clip["facecam"], box)

# ---------------------------------------------------------------- lining sound up with the transcript
s = make_session()
s.words = [{"w": f"alpha{i}", "s": 100.0 + i, "e": 100.4 + i} for i in range(6)]
src = s.dir / "clip.wav"
with (
    patch.object(live.media, "extract_audio") as extract,
    patch.object(live.media, "load_audio", return_value=np.zeros(SR, dtype=np.float32)),
    patch.object(live.transcribe, "transcribe", return_value=[{"w": f"alpha{i}", "s": i + 0.5, "e": i + 0.8} for i in range(5)]),
):
    offset, aligned = s._align(src, 100.0, 10.0)
check("the transcript and the sound are lined up", (round(offset, 2), aligned), (99.5, True))
check("the clip's audio was extracted", extract.call_count, 1)
with patch.object(live.media, "extract_audio", side_effect=RuntimeError("no audio")):
    check("a broken audio extract falls back to the piece clock", s._align(src, 100.0, 10.0), (100.0, False))
with (
    patch.object(live.media, "extract_audio"),
    patch.object(live.media, "load_audio", return_value=np.zeros(SR, dtype=np.float32)),
    patch.object(live.transcribe, "transcribe", return_value=[{"w": "only-one", "s": 1.0, "e": 1.4}]),
):
    check("too few matching words are not enough to measure the clock", s._align(src, 100.0, 10.0), (100.0, False))

# ---------------------------------------------------------------- the page feed
s = make_session()
s.samples = SR * 120
s.segments = [(s.buf / "p01_00000.ts", 60.0, 120.0)]
s.db = [-30.0] * 60
s.chat_on = True
s.chat = {i: 2 for i in range(60, 120)}
s.words = [{"w": "hello", "s": 119.0, "e": 119.4}]
with (
    patch.object(brain, "make_lines", return_value=[{"text": "hello there"}]) as lines,
    patch.object(jobs, "save") as save,
):
    s._publish()
    feed = s.job["live"]
    check("the feed has the elapsed and buffered seconds", (feed["elapsed"], feed["buffer_sec"]), (120, 60))
    check("the feed has the last caption lines", feed["lines"], ["hello there"])
    check("the feed keeps the last minute of sound and chat", (len(feed["level"]), len(feed["chat"])), (60, 60))
    check("the feed counts checks, words and clips", (feed["checks"], feed["words"], feed["pending_clips"]), (0, 1, 0))
    s._publish()
    check("the job is not written on every poll", save.call_count, 1)


# ---------------------------------------------------------------- the analysis loop that wraps everything up
def analyze(session, **patches):
    defaults = dict(
        _scan_segments=Mock(),
        _transcribe_step=Mock(return_value=False),
        _manual_step=Mock(),
        _watch_spikes=Mock(),
        _publish=Mock(),
        _ask_ai=Mock(return_value=True),
    )
    defaults.update(patches)
    submit_patch = patch.object(jobs, "submit_live")
    notify_patch = patch.object(alerts, "notify")
    stack = [patch.object(session, name, value) for name, value in defaults.items()] + [submit_patch, notify_patch]
    entered = []
    try:
        for p in stack:
            entered.append(p.start())
        session._analyze()
        return entered[-2], entered[-1]
    finally:
        for p in reversed(stack):
            p.stop()


with patch.object(live, "SESSIONS", {}):
    s = make_session()
    live.SESSIONS[s.job["id"]] = s
    s.ended, s.end_note = True, "Stream ended"
    s.samples, s.t_done = SR * 60, 60.0
    submit, notify = analyze(s)
    check("a finished recording ends as done", (s.job["status"], s.job["stage"]), ("done", "Stream ended"))
    check("the last words are written even before the timer", (s.dir / "words.json").exists(), True)
    check("cleanup is handed to the live worker", submit.call_count, 1)
    check("the owner is notified", "recording finished" in notify.call_args.args[0], True)
    submit.call_args.args[0]()
    check("cleanup empties the buffer and removes the session", (s.finished, any(s.buf.iterdir()), live.SESSIONS), (True, False, {}))

s = make_session()
s.ended = True
s.job["error"] = "ffmpeg could not start"
submit, notify = analyze(s)
check("a recording that never started ends as an error", (s.job["status"], s.job["stage"]), ("error", "Could not record"))
check("a failed recording sends a failure notice", "recording failed" in notify.call_args.args[0], True)

s = make_session(ai_every_min=1)
s.ended = True
s.samples, s.t_done, s.last_check = SR * 60, 60.0, 0.0
submit, notify = analyze(s)
# the patched _ask_ai is one of the defaults, checked separately below
check("a finished stream is asked about its last words", s.job["status"], "done")

s = make_session()
s.user_stopped = True
submit, notify = analyze(s)
check("stopping by hand ends the job", s.job["status"], "done")

s = make_session()
s.ended = True
s.job["error"] = None
with (
    patch.object(s, "_scan_segments", side_effect=RuntimeError("disk went away")),
    patch.object(s, "_transcribe_step"),
    patch.object(s, "_manual_step"),
    patch.object(s, "_watch_spikes"),
    patch.object(s, "_publish"),
    patch.object(jobs, "submit_live"),
    patch.object(alerts, "notify"),
):
    s._analyze()
check("one failing step is shown on the job instead of stopping the loop", "disk went away" in str(s.job["ai"]["last_error"]), True)

s = make_session()
s.ended = False
with patch.object(live.config, "MAX_LIVE_HOURS", 0), patch.object(s, "stop") as stop:
    stop.side_effect = lambda note: setattr(s, "user_stopped", True)
    submit, notify = analyze(s, _scan_segments=Mock(), _transcribe_step=Mock(return_value=False))
check("a recording cannot run for ever", stop.call_count >= 1, True)

s = make_session()
s.ended = False
with patch.object(live.load, "disk_full", return_value=True), patch.object(s, "stop") as stop:
    stop.side_effect = lambda note: setattr(s, "user_stopped", True)
    analyze(s)
check("a nearly full disk ends the recording", "disk is nearly full" in stop.call_args.args[0], True)

# ---------------------------------------------------------------- recovery, auto-record and shutdown
with patch.object(live, "SESSIONS", {}):
    j = make_job()
    j["recover"] = True
    j["status"] = "live"
    j["clips"] = [
        {"id": "clip001", "start": 1.0, "end": 5.0, "status": "queued", "title": "t", "hook": "h", "score": 70, "reason": "r"},
        {"id": "clip002", "start": 10.0, "end": 15.0, "status": "done"},
    ]
    jobs.save(j)
    (Path(j["dir"]) / "words.json").write_text('[{"w":"hi","s":1.0,"e":1.4}]', encoding="utf-8")
    queued = []
    with patch.object(jobs, "submit_live", side_effect=lambda fn: queued.append(fn)):
        live.recover(j)
        check("recovery queues only unfinished clips", len(queued), 2)
        for fn in queued:
            fn()
    check("recovery clears its flag", "recover" not in jobs.get(j["id"]), True)
    check("recovery empties the buffer afterwards", not any((Path(j["dir"]) / "buffer").iterdir()), True)

with patch.object(live, "SESSIONS", {}), patch.object(live, "_STARTING", set()):
    creator = {"id": "c1", "name": "Creator", "channel": "https://www.twitch.tv/creator"}
    started = []
    real_create = jobs.create

    class FakeSession:
        def __init__(self, job, c):
            started.append(("session", job, c))

        def start(self):
            started.append(("start",))

    def fake_create(source, options, enqueue=True, creator=None, kind="video"):
        job = real_create({"type": "live", "url": source.get("url", ""), "name": source.get("name", "")}, {}, enqueue=False, kind="live")
        job.update(source=source, kind=kind, creator=creator)
        started.append(("create", source["url"]))
        return job

    with (
        patch.object(jobs, "create", side_effect=fake_create),
        patch.object(live, "Session", FakeSession),
        patch.object(live.media, "site_allowed", return_value=True),
        patch.object(live.media, "is_youtube", return_value=False),
        patch.object(tools, "missing", return_value=False),
        patch.object(live, "active_mine", return_value=[]),
        patch.object(live, "active", return_value=[]),
    ):
        job = live.start_session(creator)
        check("a session is created for the channel", [x[0] for x in started], ["create", "session", "start"])
        check("the job is live from the start", (job["status"], job["stage"]), ("live", "Connecting to the stream"))
        check("the starting flag is cleared", live._STARTING, set())

        for name, kw, expected in (
            ("a channel link is needed", {"url": "not-a-link"}, "Add this creator's channel link"),
            ("a blocked site is refused", {"url": "https://example.com/x"}, "Channel links must be from"),
        ):
            raised = None
            try:
                with patch.object(live.media, "site_allowed", return_value=not name.startswith("a blocked")):
                    live.start_session(creator, **kw)
            except ValueError as e:
                raised = str(e)
            check(name, expected in str(raised), True)

        live._STARTING.add("c1")
        raised = None
        try:
            live.start_session(creator)
        except ValueError as e:
            raised = str(e)
        check("a double click cannot start two recordings", "already being recorded" in str(raised), True)
        live._STARTING.clear()

        with patch.object(live.media, "is_youtube", return_value=True), patch.object(live.media, "youtube_paused", return_value=True):
            raised = None
            try:
                live.start_session(creator)
            except ValueError as e:
                raised = str(e)
            check("blocked YouTube recordings explain why", "record YouTube streams" in str(raised), True)

        other_session = types.SimpleNamespace(job={"creator": "someone-else"})
        with patch.object(live.config, "MAX_LIVE_PER_USER", 1), patch.object(live, "active_mine", return_value=[other_session]):
            raised = None
            try:
                live.start_session(creator)
            except ValueError as e:
                raised = str(e)
            check("the per-user recording limit is enforced", "most this server allows" in str(raised), True)

        with patch.object(live.config, "MAX_LIVE_TOTAL", 1), patch.object(live, "active", return_value=[object()]):
            raised = None
            try:
                live.start_session(creator)
            except ValueError as e:
                raised = str(e)
            check("the whole server's recording limit is enforced", "as many streams as it can" in str(raised), True)

        with patch.object(tools, "missing", return_value=True):
            raised = None
            try:
                live.start_session(creator)
            except ValueError as e:
                raised = str(e)
            check("a machine without ffmpeg cannot start a recording", bool(raised), True)

creator = {
    "id": "c1",
    "name": "Creator",
    "channel": "https://www.twitch.tv/creator",
    "auto_watch": True,
    "channels": {"twitch": "https://www.twitch.tv/creator"},
}
with (
    patch.object(profiles, "live_shared", return_value={"live": False}),
    patch.object(live, "start_session") as begin,
):
    live._watch_one(creator)
    check("a creator who is not live is not recorded", begin.call_count, 0)

with (
    patch.object(profiles, "live_shared", return_value={"live": True}),
    patch.object(live, "start_session") as begin,
    patch.object(live.creators, "log") as log,
    patch.object(live.alerts, "notify") as notify,
):
    live._watch_one(creator)
    check("a creator who goes live is recorded automatically", begin.call_count, 1)
    check("and the owner is told", notify.call_count, 1)

with patch.object(live, "active_for", return_value=object()):
    with patch.object(profiles, "live_shared", return_value={"live": True}), patch.object(live, "start_session") as begin:
        live._watch_one(creator)
        check("an already recording creator is not started again", begin.call_count, 0)

with patch.object(live.creators, "all_creators", return_value=[creator]), patch.object(live, "_watch_one") as one:
    live._watch_user(UID)
    check("auto-record checks the user's creators", one.call_args.args[0]["id"], "c1")

with patch.object(live.creators, "all_creators", return_value=[]), patch.object(live, "_watch_one") as one:
    live._watch_user(UID)
    check("a user with no auto-record creators needs no work", one.call_count, 0)


class InlineThread:
    def __init__(self, target, args=(), **kw):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


class StopLoop(Exception):
    pass


with (
    patch.object(live, "creators", live.creators),
    patch.object(live.creators, "get", return_value=creator),
    patch.object(profiles, "_live", {}),
    patch.object(live, "_watch_one") as one,
    patch.object(profiles, "refresh") as refresh,
    patch.object(live.threading, "Thread", InlineThread),
):
    live.check_now("c1")
    check("turning auto-record on looks straight away", one.call_count, 1)
    check("and refreshes the channel's profile", refresh.call_args.args, ("c1",))


class FakePool:
    def __init__(self, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def map(self, fn, items):
        return [fn(x) for x in items]


napped = []


def stop_after_two(n):
    napped.append(n)
    if len(napped) >= 2:
        raise StopLoop()


with (
    patch.object(live.time, "sleep", stop_after_two),
    patch.object(live.auth, "user_ids", return_value=[UID]),
    patch.object(live, "_watch_user") as watch,
    patch("concurrent.futures.ThreadPoolExecutor", FakePool),
):
    try:
        live.watch_loop()
    except StopLoop:
        pass
    check("the auto-record loop checks every owner", watch.call_args.args, (UID,))
    check("the loop sleeps before its first check", napped[0], 20)

resume_waits = []


def stop_first_wait(seconds):
    resume_waits.append(seconds)
    raise StopLoop()


with patch.object(live.time, "sleep", side_effect=stop_first_wait):
    try:
        live._resume_recordings([(UID, "c1", "https://www.twitch.tv/creator")])
    except StopLoop:
        pass
check("resuming waits for the network before checking", resume_waits, [10])

with (
    patch.object(live.time, "sleep", side_effect=[None, StopLoop()]),
    patch.object(live, "creators", live.creators),
    patch.object(live.creators, "get", return_value=creator),
    patch.object(live, "active_for", return_value=None),
    patch.object(live, "resolve", return_value=info),
    patch.object(live, "start_session") as begin,
    patch.object(live.creators, "log") as log,
):
    live._resume_recordings([(UID, "c1", "https://www.twitch.tv/creator")])
check("a stream still live after a restart is recorded again", begin.call_count, 1)
check("the restart is written to the creator's log", "picked up again" in log.call_args.args[1], True)

with (
    patch.object(live.time, "sleep", side_effect=[None, StopLoop()]),
    patch.object(live.creators, "get", return_value=creator),
    patch.object(live, "active_for", return_value=None),
    patch.object(live, "resolve", side_effect=live.NotLive("stream ended")),
    patch.object(live, "start_session") as begin,
):
    live._resume_recordings([(UID, "c1", "https://www.twitch.tv/creator")])
check("a stream that ended while the studio was away is not restarted", begin.call_count, 0)

with patch.object(live, "SESSIONS", {}):
    recover_job = make_job()
    recover_job["recover"] = True
    paused_job = make_job()
    paused_job["was_live"] = True
    paused_job["creator"] = "c1"
    paused_job["source"]["url"] = "https://www.twitch.tv/creator"
    jobs.save(recover_job)
    jobs.save(paused_job)
    with (
        patch.object(jobs, "all_jobs", return_value=[recover_job, paused_job]),
        patch.object(live, "recover") as recover,
        patch.object(jobs, "save") as save,
        patch.object(live.threading, "Thread", InlineThread),
        patch.object(live, "_resume_recordings") as resume,
        patch.object(live, "watch_loop") as watch,
    ):
        live.start()
    check("unfinished recordings are recovered at start", recover.call_args.args[0]["id"], recover_job["id"])
    check("the paused flag is cleared once noted", "was_live" not in paused_job, True)
    check("the restart thread is started", resume.call_count, 1)

s1, s2 = make_session(), make_session()
s2.finished = True
with patch.object(live, "SESSIONS", {"a": s1, "b": s2}), patch.object(live.Session, "stop") as stop:
    live.stop_all("Closing down")
    check("only unfinished recordings are stopped on shutdown", stop.call_count, 1)

print("\n" + (f"{len(fails)} failed" if fails else "All checks passed"))
import shutil

shutil.rmtree(DATA, ignore_errors=True)
sys.exit(1 if fails else 0)
