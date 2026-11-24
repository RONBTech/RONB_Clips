"""Deep checks for speech-to-text: where it runs, how it slices audio and how it reads model answers.

Nothing here loads the 480 MB speech model or calls a real service: the model, the speech library and the graphics
card machine are all replaced with fixed answers. The audio is generated in memory, so the slicing and word
parsing are the code that really runs.
"""

import importlib
import os
import queue
import sys
import tempfile
import time
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ["RONBCLIPS_DATA"] = tempfile.mkdtemp(prefix="rc-transcribe-deep-")
os.environ.update(
    RONBCLIPS_ASR_PROVIDER="cpu",
    RONBCLIPS_ASR_THREADS="2",
    RONBCLIPS_ASR_COPIES="2",
    RONBCLIPS_ASR_BATCH="1",
    RONBCLIPS_ASR_REMOTE_BATCH="8",
    RONBCLIPS_ASR_REMOTE_PARALLEL="1",
    RONBCLIPS_ASR_REMOTE_INFLIGHT="2",
    RONBCLIPS_ASR_URL="",
    RONBCLIPS_ASR_TOKEN="",
    DATABASE_URL=os.environ.get("RONBCLIPS_TEST_DATABASE_URL", ""),
    RONBCLIPS_FETCH_MODEL="0",
    PYTHONIOENCODING="utf-8",
)
sys.path.insert(0, ".")

import numpy as np  # noqa: E402

from studio import config, fetch_model  # noqa: E402
from studio import transcribe as T

fails = []
SR = 16000


def check(name, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"   got {got!r}"))
    ok or fails.append(name)


def reset_pool():
    T._free = queue.LifoQueue()
    T._made = 0


def tone(seconds, at=0, amplitude=8000):
    a = np.zeros(SR * seconds, dtype=np.int16)
    a[at * SR : (at + 2) * SR] = (np.sin(np.arange(2 * SR) * 0.04) * amplitude).astype(np.int16)
    return a


def fake_decode(words_by_call):
    """A stand-in for decode_pieces that behaves like it: hands the model back after use."""
    calls = []

    def decode(pieces, rec=None):
        if rec is not None:
            T._free.put(rec)
        i = len(calls)
        calls.append(len(pieces))
        return words_by_call[min(i, len(words_by_call) - 1)]

    return decode, calls


# ---------------------------------------------------------------- where the model runs
check("the provider comes from the setting", T.PROVIDER, "cpu")
check("the thread count comes from the setting", T.THREADS, 2)
check("the number of model copies comes from the setting", T.COPIES, 2)

check("the model is ready when the download says so", T.model_ready() is fetch_model.ready(), True)
with patch.object(fetch_model, "ready", return_value=True):
    check("model_ready asks the downloader", T.model_ready(), True)

model_dir = Path(tempfile.mkdtemp(prefix="rc-asr-model-"))
for name in ("encoder", "decoder", "joiner"):
    (model_dir / f"{name}.int8.onnx").write_bytes(b"int8")
    (model_dir / f"{name}.fp16.onnx").write_bytes(b"fp16")
    (model_dir / f"{name}.onnx").write_bytes(b"full")
(model_dir / "tokens.txt").write_text("tokens", encoding="utf-8")
with patch.object(T, "PROVIDER", "cpu"):
    check(
        "processors use the compact model files",
        [Path(v).name for v in T._model_files(model_dir).values()],
        [f"{k}.int8.onnx" for k in ("encoder", "decoder", "joiner")],
    )
with patch.object(T, "PROVIDER", "cuda"):
    check("a graphics card uses the half-precision files", Path(T._model_files(model_dir)["encoder"]).name, "encoder.fp16.onnx")
small_dir = Path(tempfile.mkdtemp(prefix="rc-asr-small-"))
for name in ("encoder", "decoder", "joiner"):
    (small_dir / f"{name}.onnx").write_bytes(b"full")
check("a model with only full-precision files still works", Path(T._model_files(small_dir)["encoder"]).name, "encoder.onnx")
raised = None
try:
    T._model_files(Path(tempfile.mkdtemp(prefix="rc-asr-empty-")))
except FileNotFoundError as e:
    raised = str(e)
check("a missing model says where it looked", "No speech model files in" in str(raised), True)

sentinel = object()
T._lib["mod"] = None
with patch.object(T, "_load_sherpa", return_value=sentinel) as loader:
    check("the speech library is loaded once", T._sherpa() is sentinel, True)
    check("the second ask uses the loaded library", T._sherpa() is sentinel, True)
    check("...without loading it again", loader.call_count, 1)
T._lib["mod"] = None
fake_lib = types.ModuleType("sherpa_onnx")
with patch.dict(sys.modules, {"sherpa_onnx": fake_lib}):
    check("a plain import finds the speech library", T._load_sherpa() is fake_lib, True)
with patch.dict(sys.modules, {"sherpa_onnx": None}), patch.object(T.os, "name", "posix"):
    raised = None
    try:
        T._load_sherpa()
    except ImportError:
        raised = "ImportError"
    check("an unavailable speech library is not hidden", raised, "ImportError")

recognizer = Mock(name="recognizer")
sherpa = SimpleNamespace(OfflineRecognizer=SimpleNamespace(from_transducer=Mock(return_value=recognizer)))
with (
    patch.object(T, "_sherpa", return_value=sherpa),
    patch.object(fetch_model, "ensure") as ensure,
    patch.object(config, "ASR_MODEL_DIR", model_dir),
):
    made = T._new()
    check("_new builds a recognizer", made is recognizer, True)
    check("_new makes sure the model is downloaded", ensure.call_count, 1)
    kwargs = sherpa.OfflineRecognizer.from_transducer.call_args.kwargs
    check(
        "the recognizer is given the model, tokens and thread count",
        (kwargs["num_threads"], kwargs["sample_rate"], kwargs["provider"]),
        (T.THREADS, 16000, "cpu"),
    )
    check(
        "the recognizer is given all three model files",
        [kwargs[k] for k in ("encoder", "decoder", "joiner")],
        [str(model_dir / f"{k}.int8.onnx") for k in ("encoder", "decoder", "joiner")],
    )

# ---------------------------------------------------------------- the pool of model copies
reset_pool()
with patch.object(T, "_new", side_effect=[object(), object(), object()]) as new:
    first, second = T._take(), T._take()
    check("two copies are made when two are allowed", new.call_count, 2)
    check("a third copy cannot be made right now", T._take_now(), None)
    T._free.put(first)
    check("a finished copy is handed to the next caller", T._take() is first, True)
    T._free.put(first)
    T._free.put(second)
    warm = T._recognizer()
    check("warming up borrows a copy and puts it back", warm in (first, second) and T._free.qsize() == 2, True)
    with patch.object(T, "COPIES", 3):
        T._free = queue.LifoQueue()  # every copy is busy elsewhere
        extra = T._recognizer()
        check("a spare slot makes another copy", new.call_count, 3)
        check("the new copy is returned to the pool", T._free.qsize(), 1)

reset_pool()
with patch.object(T, "COPIES", 1), patch.object(T, "_new", side_effect=RuntimeError("model broke")):
    raised = None
    try:
        T._take_now()
    except RuntimeError:
        raised = "RuntimeError"
    check("_take_now passes on a failed model load", raised, "RuntimeError")
    check("a failed model load does not use the slot for ever", T._made, 0)
    raised = None
    try:
        T._take()
    except RuntimeError:
        raised = "RuntimeError"
    check("_take passes on a failed model load too", raised, "RuntimeError")
    check("...and also releases its slot", T._made, 0)

# waiting for a copy is exercised with a real thread that frees one shortly afterwards
reset_pool()
with patch.object(T, "COPIES", 1), patch.object(T, "_new", return_value="only-copy"):

    def give_back():
        time.sleep(0.05)
        T._free.put("only-copy")

    import threading

    thread = threading.Thread(target=give_back, daemon=True)
    thread.start()
    check("a caller waits for a busy copy", T._take(), "only-copy")
    thread.join()

# ---------------------------------------------------------------- cutting audio
audio = tone(30, at=0) + tone(30, at=13)
chunks = list(T._chunks(audio))
check("a long recording is split into pieces", len(chunks) >= 2, True)
check("the first piece starts at zero", chunks[0][0], 0)
check("the last piece ends at the end of the audio", chunks[-1][1], len(audio))
check("the pieces do not overlap", all(chunks[i][1] == chunks[i + 1][0] for i in range(len(chunks) - 1)), True)
mid = chunks[0][1]
quiet = audio[max(0, mid - 2000) : mid + 2000].astype(np.float32) / 32768.0
check("the cut was placed in a quiet stretch", float(np.sqrt((quiet**2).mean())) < 0.01, True)
check("a tiny recording has no pieces", list(T._chunks(np.zeros(100, dtype=np.int16))), [])

# ---------------------------------------------------------------- reading words out of model answers
words = T._words([" hello", " world", "!", " again"], [0.0, 0.2, 0.4, 1.0], 0, 2 * SR)
check(
    "tokens are joined into words with times",
    [(w["w"], w["s"], w["e"]) for w in words],
    [("hello", 0.0, 0.12), ("world!", 0.2, 0.52), ("again", 1.0, 1.12)],
)
spaced = T._words(["in", " ", "120"], [0.0, 0.1, 0.2], SR, 2 * SR)
check("a space on its own starts a new word", [w["w"] for w in spaced], ["in", "120"])
check("the time offset is added", spaced[0]["s"], 1.0)
sep_mark = "\u00e2\u2013\u0081"  # the odd bytes the model puts in front of a new word
sep = T._words([sep_mark + "hello", sep_mark + "there"], [0.0, 0.5], 0, 2 * SR)
check("the model's broken separator is read as a space", [w["w"] for w in sep], ["hello", "there"])
check("no tokens give no words", T._words([], [], 0, SR), [])
check("the last word cannot run past the end of its piece", T._words([" late"], [9.9], 0, 10 * SR)[0]["e"], 10.0)


class Stream:
    def __init__(self, tokens, stamps):
        self.result = SimpleNamespace(tokens=tokens, timestamps=stamps)

    def accept_waveform(self, rate, data):
        self.rate, self.data = rate, data


class Recorder:
    def __init__(self):
        self.streams = []
        self.decoded = []

    def create_stream(self):
        st = Stream([" hi"], [0.1])
        self.streams.append(st)
        return st

    def decode_streams(self, streams):
        self.decoded.append(len(streams))


reset_pool()
rec = Recorder()
pieces = [np.zeros(100, dtype=np.float32), np.zeros(200, dtype=np.float32)]
got = T.decode_pieces(pieces, rec)
check("pieces are decoded together by one recognizer", rec.decoded, [2])
check("the answers are returned in order", got, [([" hi"], [0.1]), ([" hi"], [0.1])])
check("the waveform and rate are passed to the model", (rec.streams[0].rate, len(rec.streams[0].data)), (16000, 100))
check("the recognizer is put back in the pool", T._free.qsize(), 1)
reset_pool()
rec2 = Recorder()
with patch.object(T, "_take", return_value=rec2):
    check("a recognizer is borrowed when none is given", T.decode_pieces(pieces) == [([" hi"], [0.1])] * 2, True)
check("the borrowed recognizer is returned too", T._free.qsize(), 1)

# ---------------------------------------------------------------- the machine with the graphics card
T.REMOTE = "http://asr.invalid/"
T.REMOTE_TOKEN = "secret"
T._remote_down_until = time.time() + 60
check("a machine in cooldown is not used", T._remote_ok(), False)
T._remote_down_until = time.time() - 1
check("a machine out of cooldown is used", T._remote_ok(), True)


class Response:
    def __init__(self, pieces):
        self.payload = {"pieces": pieces}
        self.status = None

    def raise_for_status(self):
        if self.status:
            raise RuntimeError(self.status)

    def json(self):
        return self.payload


answer = Response([{"tokens": [" hi"], "timestamps": [0.1]}, {"tokens": [" there"], "timestamps": [0.2]}])
httpx = SimpleNamespace(post=Mock(return_value=answer), Timeout=lambda *a, **k: ("timeout", a, k))
with patch.dict(sys.modules, {"httpx": httpx}):
    got = T._decode_remote([np.array([0.5, -0.5], dtype=np.float32), np.array([0.25], dtype=np.float32)])
    check("the remote answer is read as pieces", got, [([" hi"], [0.1]), ([" there"], [0.2])])
    call = httpx.post.call_args
    check("audio goes to the remote decode address", call.args[0], "http://asr.invalid//decode")
    expected_content = (
        (16384).to_bytes(2, "little", signed=True) + (-16384).to_bytes(2, "little", signed=True) + (8192).to_bytes(2, "little", signed=True)
    )
    check("audio is sent as 16-bit sound", call.kwargs["content"], expected_content)
    check(
        "the remote call carries the token and piece lengths",
        (call.kwargs["headers"]["Authorization"], call.kwargs["headers"]["X-Lens"]),
        ("Bearer secret", "2,1"),
    )
    answer.status = "503"
    raised = None
    try:
        T._decode_remote([np.zeros(1, dtype=np.float32)])
    except RuntimeError:
        raised = "RuntimeError"
    check("a failed remote request is not hidden", raised, "RuntimeError")
    answer.status = None
    answer.payload = {"pieces": [{"tokens": [], "timestamps": []}]}
    raised = None
    try:
        T._decode_remote([np.zeros(1, dtype=np.float32), np.zeros(1, dtype=np.float32)])
    except ValueError as e:
        raised = str(e)
    check("a short remote answer is refused", raised, "wrong number of pieces came back")

# ---------------------------------------------------------------- the whole transcribe path
T.REMOTE = ""
T.REMOTE_BATCH = 8
T.REMOTE_PARALLEL = 1
T.BATCH = 1
T._remote_down_until = 0
reset_pool()
tone_audio = tone(30, at=0) + tone(30, at=22)
local_decode, local_calls = fake_decode(
    [
        [([" hello", " world"], [0.0, 0.2])],
        [([" again"], [0.1])],
    ]
)
progress = []
with patch.object(T, "_new", return_value=object()), patch.object(T, "decode_pieces", local_decode):
    got = T.transcribe(tone_audio, progress.append)
check("a piece of silence is skipped but speech is read", [w["w"] for w in got], ["hello", "world", "again"])
check("the first words sit at the start of the stream", [w["s"] for w in got[:2]], [0.0, 0.2])
check("the later word is placed after the quiet cut", 12 < got[2]["s"] < 24, True)
check("a word lasts until the next word begins", got[0]["e"], 0.2)
check("progress is reported for each piece", progress, [0.5, 1.0])
check("the local path borrows a model copy", T._made, 1)

reset_pool()
with (
    patch.object(T, "_chunks", return_value=[(0, SR), (0, SR)]),
    patch.object(T, "decode_pieces", fake_decode([[([" same"], [0.1])], [([" same"], [0.1])]])[0]),
    patch.object(T, "_new", return_value=object()),
):
    audio2 = np.full(2 * SR, 8000, dtype=np.int16)
    got = T.transcribe(audio2)
check("the model saying the same word twice at the same time is said once", [w["w"] for w in got], ["same"])

reset_pool()
with patch.object(T, "_chunks", return_value=[(0, SR)]):
    check("an entirely silent piece needs no model", T.transcribe(np.zeros(SR, dtype=np.int16)), [])

reset_pool()
T.BATCH = 3
batch_decode, batch_calls = fake_decode([[([" hello"], [0.1])]])
with patch.object(T, "_new", return_value=object()), patch.object(T, "decode_pieces", batch_decode):
    T.transcribe(tone_audio)
check("a larger batch sends both pieces at once", batch_calls, [2])
T.BATCH = 1

# remote decoding, with a fixed answer for each piece
reset_pool()
T.REMOTE = "http://asr.invalid"
T.REMOTE_BATCH = 8
T._remote_down_until = 0
remote_progress = []
with patch.object(T, "_decode_remote", return_value=[([" said"], [0.0]), ([" remotely"], [0.3])]):
    got = T.transcribe(tone_audio, remote_progress.append)
check("the graphics card machine supplies the words", [w["w"] for w in got], ["said", "remotely"])
check("one remote batch is one progress step", remote_progress, [1.0])

# a remote machine that fails: the pieces are done on this machine instead
reset_pool()
T.REMOTE_BATCH = 1
T._remote_down_until = 0
local_decode, local_calls = fake_decode([[([" local"], [0.1])], [([" words"], [0.2])]])
with (
    patch.object(T, "_decode_remote", side_effect=RuntimeError("card is away")),
    patch.object(T, "decode_pieces", local_decode),
    patch.object(T, "_new", return_value=object()),
):
    got = T.transcribe(tone_audio)
check("a failed remote machine falls back to the processors", [w["w"] for w in got], ["local", "words"])
check("the failed machine is given a cooldown", T._remote_down_until > time.time(), True)
check("the remote slot is returned after a failure", T.REMOTE_SLOTS.acquire(blocking=False), True)
T.REMOTE_SLOTS.release()

# several pieces at once on the remote machine
reset_pool()
T.REMOTE_PARALLEL = 2
T.REMOTE_BATCH = 1
T._remote_down_until = 0


def remote_by_content(pieces):
    word = "one" if np.any(pieces[0][:100]) else "two"
    return [([" " + word], [0.0])]


with patch.object(T, "_decode_remote", side_effect=remote_by_content):
    got = T.transcribe(tone_audio)
check("parallel remote pieces come back in the original order", [w["w"] for w in got], ["one", "two"])
T.REMOTE = ""
T.REMOTE_PARALLEL = 1
T.REMOTE_BATCH = 8


# ---------------------------------------------------------------- settings computed when the module starts
def reload_asr(env, cpus):
    with patch.dict(os.environ, {}, clear=False):
        for key in [k for k in os.environ if k.startswith("RONBCLIPS_ASR")]:
            os.environ.pop(key)
        os.environ.update(env)
        with patch.object(os, "cpu_count", return_value=cpus):
            module = importlib.reload(T)
    return module


m = reload_asr({}, 16)
check("a big machine gets four threads and four copies by default", (m.PROVIDER, m.THREADS, m.COPIES), ("cpu", 4, 4))
m = reload_asr({}, 4)
check("a small machine gets one copy", (m.THREADS, m.COPIES), (2, 1))
m = reload_asr({"RONBCLIPS_ASR_PROVIDER": " CUDA "}, 16)
check("a graphics card gets one copy", (m.PROVIDER, m.COPIES), ("cuda", 1))
m = reload_asr({"RONBCLIPS_ASR_THREADS": "3", "RONBCLIPS_ASR_COPIES": "2"}, 16)
check("thread and copy counts can be set by hand", (m.THREADS, m.COPIES), (3, 2))
m = reload_asr({"RONBCLIPS_ASR_REMOTE_BATCH": "0", "RONBCLIPS_ASR_REMOTE_PARALLEL": "0", "RONBCLIPS_ASR_REMOTE_INFLIGHT": "0"}, 4)
check("batch settings are read as numbers", (m.REMOTE_BATCH, m.REMOTE_PARALLEL), (0, 0))
check("a remote address and token are read at start", (m.REMOTE, m.REMOTE_TOKEN), ("", ""))
# back to the settings this test began with, so nothing here depends on the order of the checks
reload_asr(
    {
        "RONBCLIPS_ASR_PROVIDER": "cpu",
        "RONBCLIPS_ASR_THREADS": "2",
        "RONBCLIPS_ASR_COPIES": "2",
        "RONBCLIPS_ASR_BATCH": "1",
        "RONBCLIPS_ASR_REMOTE_BATCH": "8",
        "RONBCLIPS_ASR_REMOTE_PARALLEL": "1",
        "RONBCLIPS_ASR_REMOTE_INFLIGHT": "2",
    },
    8,
)

print("\n" + (f"{len(fails)} failed" if fails else "All checks passed"))
import shutil

shutil.rmtree(os.environ["RONBCLIPS_DATA"], ignore_errors=True)
shutil.rmtree(model_dir, ignore_errors=True)
shutil.rmtree(small_dir, ignore_errors=True)
sys.exit(1 if fails else 0)
