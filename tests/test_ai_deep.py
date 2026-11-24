"""Deep tests for studio/ai.py: the provider list, settings on disk, every kind of service, parsing answers,
keys and errors, model lists, tokens and usage. All HTTP is replaced with local fakes; no key or network needed.

Run with: python tests/test_ai_deep.py
"""

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

DATA = tempfile.mkdtemp(prefix="pc-ai-deep-")
os.environ["RONBCLIPS_DATA"] = DATA
os.environ.update(
    DATABASE_URL=os.environ.get("RONBCLIPS_TEST_DATABASE_URL", ""),
    RONBCLIPS_ACCOUNTS="0",
    RONBCLIPS_FETCH_MODEL="0",
    RONBCLIPS_UPDATE_CHECK="0",
    RONBCLIPS_AI_PROVIDER="gemini",
    RONBCLIPS_AI_MODELS="gemini-a, gemini-b",
    RONBCLIPS_AI_KEY="env-key-123456789",
    RONBCLIPS_AI_BASE_URL="",
)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from studio import ai, auth, brain, config, ctx, db, secretfile, usage  # noqa: E402

db.init()
UID = auth.local_user().id
fails = []


def check(name, got, expected):
    ok = expected(got) if callable(expected) else got == expected
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"   got {got!r}, wanted {expected!r}"))
    if not ok:
        fails.append(name)
    return ok


def expect_raise(name, fn, kind=Exception, part=""):
    try:
        fn()
    except Exception as e:  # noqa: BLE001 - the test names the kind it wants
        ok = isinstance(e, kind) and (not part or part in str(e))
        check(name, ok, True if ok else f"{type(e).__name__}: {e}")
        return e if ok else None
    check(name, "no exception was raised", "an exception")
    return None


class Reply:
    def __init__(self, status=200, data=None, text=None, headers=None):
        self.status_code = status
        self._data = data
        self.text = text if text is not None else json.dumps(data if data is not None else {})
        self.headers = headers or {}

    def json(self):
        if isinstance(self._data, Exception):
            raise self._data
        return self._data


# ---------------------------------------------------------------- the fake network
posts = []
gets = []
post_replies = []
get_replies = []


def fake_post(url, json=None, headers=None, timeout=None, **kw):  # noqa: A002 - that is httpx's name
    posts.append({"url": url, "json": json, "headers": headers or {}, "timeout": timeout, **kw})
    if not post_replies:
        raise AssertionError("An unexpected AI request was made: " + url)
    r = post_replies.pop(0)
    return r(url, json, headers, timeout) if callable(r) else r


def fake_get(url, params=None, headers=None, timeout=None, **kw):
    gets.append({"url": url, "params": params, "headers": headers or {}, "timeout": timeout, **kw})
    if not get_replies:
        raise AssertionError("An unexpected model list request was made: " + url)
    r = get_replies.pop(0)
    return r(url, params, headers, timeout) if callable(r) else r


real_post, real_get = ai.httpx.post, ai.httpx.get
ai.httpx.post, ai.httpx.get = fake_post, fake_get


def use(provider, models=None, address="", key="env-key-123456789"):
    config.AI_PROVIDER, config.AI_BASE_URL = provider, address
    config.AI_MODELS = list(models if models is not None else ["gemini-a", "gemini-b"])
    os.environ["RONBCLIPS_AI_KEY"] = key
    config._kept.clear()
    ai._saved["at"] = None
    posts.clear()
    gets.clear()
    post_replies.clear()
    get_replies.clear()


# ---------------------------------------------------------------- the registry and the settings in use
check("all eight services are in the list", len(ai.PROVIDERS), 8)
check(
    "aliases point at the real service",
    [ai.ALIASES[x] for x in ("google", "grok", "claude", "local")],
    ["gemini", "xai", "anthropic", "ollama"],
)
check("local services need no key", ai.NO_KEY, {"ollama", "custom"})
check("Gemini is the default from .env", (ai.provider(), ai.models(), ai.key()), ("gemini", ["gemini-a", "gemini-b"], "env-key-123456789"))

config.AI_PROVIDER = "  Grok  "
check("a provider name is trimmed and alias-resolved", (ai.provider(), ai.label()), ("xai", "Grok"))
check("the default address of a service is used", ai.base_url(), "https://api.x.ai/v1")
config.AI_PROVIDER = "nonsense"
check("an unknown service has no label or address", (ai.label(), ai.base_url(), ai.ready()), ("", "", False))
config.AI_PROVIDER, config.AI_MODELS = "gemini", []
check("a service with no model is not ready", ai.ready(), False)
config.AI_MODELS = ["gemini-a"]
os.environ["RONBCLIPS_AI_KEY"] = ""
config._kept.clear()
check("a cloud service with no key is not ready", (ai.key(), ai.ready()), ("", False))
use("ollama", ["llama3"], key="")
check(
    "Ollama is ready without a key",
    (ai.ready(), ai.label(), ai.base_url()),
    (True, "Ollama (on this machine)", "http://127.0.0.1:11434/v1"),
)
pub = ai.public()
check(
    "the page sees the list but not the key", (pub["has_key"], pub["key_hint"], pub["ready"], pub["saved_here"]), (False, "", True, False)
)
check("the page's provider list has one row per service", len(pub["providers"]), 8)
check("local rows say no key is needed", [p["needs_key"] for p in pub["providers"] if p["id"] in ai.NO_KEY], [False, False])
use("gemini", ["a-model-with-a-long-name"])
pub = ai.public()
check("the key hint is the last four characters", pub["key_hint"], "6789")

# ---------------------------------------------------------------- settings saved from the dashboard
written = []
real_write = secretfile.write_private


def recorded_write(path, data):
    written.append((Path(path).name, data))
    return real_write(path, data)


with patch.object(secretfile, "write_private", side_effect=recorded_write):
    saved = ai.save("google", "model-1, model-2", "", "brand-new-key-abcdef")
check("save resolves the alias and reports it saved here", (saved["provider"], saved["saved_here"]), ("gemini", True))
check("the saved key is hinted, never shown", (saved["has_key"], saved["key_hint"]), (True, "cdef"))
check("the settings file was written privately", [n for n, _ in written], ["ai.json"])
raw = json.loads((Path(DATA) / "ai.json").read_text(encoding="utf-8"))
check(
    "the dashboard file holds the whole choice",
    (raw["provider"], raw["models"], raw["base_url"], raw["key"]),
    ("gemini", ["model-1", "model-2"], "", "brand-new-key-abcdef"),
)
check("the saved choice wins over .env", (ai.models(), ai.key()), (["model-1", "model-2"], "brand-new-key-abcdef"))
ai.save("gemini", ["model-1"], "", None)
check("saving again without a key keeps it", ai.key(), "brand-new-key-abcdef")
expect_raise("a cloud service without a key is refused", lambda: ai.save("gemini", ["m"], "", ""), ValueError, "Paste the key")
check("a refused save changed nothing", ai.key(), "brand-new-key-abcdef")
for bad, message in (
    (lambda: ai.save("myspace", ["m"], "", "k"), "Choose one of the AI services"),
    (lambda: ai.save("gemini", [], "", "k"), "Enter the model"),
    (lambda: ai.save("gemini", ["bad model!"], "", "k"), "model id"),
    (lambda: ai.save("openai", ["m"], "ftp://example.com", "k"), "http:// or https://"),
    (lambda: ai.save("custom", ["m"], "", "k"), "needs its address"),
):
    expect_raise("a bad save is refused: " + str(message), bad, ValueError, message or "")
ai.save("gemini", ["m"] * 8, "", "k")
check("no more than five models are kept", json.loads((Path(DATA) / "ai.json").read_text(encoding="utf-8"))["models"], ["m"] * 5)
ai.save("custom", ["local-model"], "http://127.0.0.1:1234/v1/", "")
check(
    "custom keeps its address without a trailing slash",
    (ai.provider(), ai.base_url(), ai.ready()),
    ("custom", "http://127.0.0.1:1234/v1", True),
)

# a file changed from outside is picked up when its modification time moves; broken JSON is ignored
f = Path(DATA) / "ai.json"
f.write_text(json.dumps({"provider": "openai", "models": ["outside"], "key": "outside-key-abcdef"}), encoding="utf-8")
os.utime(f, ns=(time.time_ns() + 10**10, time.time_ns() + 10**10))
check("an outside edit is read again", (ai.provider(), ai.models()), ("openai", ["outside"]))
f.write_text("{not json", encoding="utf-8")
os.utime(f, ns=(time.time_ns() + 2 * 10**10, time.time_ns() + 2 * 10**10))
check("broken saved settings fall back to .env", (ai.provider(), ai.models()), (config.AI_PROVIDER, config.AI_MODELS))
f.unlink()
ai._saved["at"] = None

# ---------------------------------------------------------------- the schema, said in words for chat models
schema = {
    "type": "ARRAY",
    "items": {"type": "OBJECT", "properties": {"n": {"type": "NUMBER"}, "xs": {"type": "ARRAY", "items": {"type": "STRING"}}}},
}
flat = ai._json_schema(schema)
check(
    "Gemini's schema is lowercased for chat models",
    (flat["type"], flat["items"]["properties"]["n"]["type"], flat["items"]["properties"]["xs"]["items"]["type"]),
    ("array", "number", "string"),
)
wrapped = ai._wrapped("Find clips.", schema)
check(
    "the schema and the JSON-only rule go into the prompt",
    "Find clips.\n\nAnswer with JSON only" in wrapped and '"type": "array"' in wrapped,
    True,
)
check("balanced JSON is found inside strings too", ai._json_chunks('hi {"a": "}"} then [1,2]'), ['{"a": "}"}', "[1,2]"])
check("unbalanced brackets do not lose the good object", ai._json_chunks('x } {"a":1} y'), ['{"a":1}'])
for text, want in (
    ('{"items":[{"hook":"A"}]}', [{"hook": "A"}]),
    ('```json\n{"items":[{"hook":"B"}]}\n```', [{"hook": "B"}]),
    ('Sure: {"a":1, "b":{"c":2}} thanks', [{"a": 1, "b": {"c": 2}}]),
    ('{"items":[{"hook":"D"}]}\n{"items":[{"hook":"E"}]}', [{"hook": "D"}]),
    ('[{"hook":"F"}, {"hook":"G"}]', [{"hook": "F"}, {"hook": "G"}]),
    ('bad {"hook":"H"}', [{"hook": "H"}]),
):
    check("JSON is read from an answer: " + text[:24], ai._parse(text), want)
for text in ("", "no JSON here", '{"items":[', '"text"', "42"):
    expect_raise("a non-list answer is refused: " + repr(text), lambda t=text: ai._parse(t), ai.AIError)

# ---------------------------------------------------------------- Gemini
use("gemini", ["gemini-a"])
post_replies.append(
    Reply(
        200,
        {
            "candidates": [{"content": {"parts": [{"text": '{"items":[{"hook":"GEMINI"}]}'}]}}],
            "usageMetadata": {"promptTokenCount": 10, "totalTokenCount": 18},
        },
    )
)
out, sent, wrote = ai.call("gemini-a", "hello", schema)
check("Gemini's answer and tokens are read", (out, sent, wrote), ([{"hook": "GEMINI"}], 10, 8))
check(
    "Gemini gets an API-key header and the schema verbatim",
    posts[-1]["headers"]["x-goog-api-key"] == "env-key-123456789" and posts[-1]["json"]["generationConfig"]["responseSchema"] is schema,
    True,
)
check(
    "Gemini is asked at its model address",
    posts[-1]["url"],
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-a:generateContent",
)
check("Gemini is told to answer with JSON", posts[-1]["json"]["generationConfig"]["responseMimeType"], "application/json")
for reason in ("MAX_TOKENS", "RECITATION"):
    post_replies.append(Reply(200, {"candidates": [{"finishReason": reason}]}))
    expect_raise(
        "a cut-off Gemini answer is refused: " + reason, lambda: ai.call("gemini-a", "hello", schema), ai.AIError, "stopped mid-answer"
    )
post_replies.append(
    Reply(200, {"candidates": [{"content": {"parts": [{"text": "[]"}]}}], "usageMetadata": {"totalTokenCount": 3, "promptTokenCount": 10}})
)
out, sent, wrote = ai.call("gemini-a", "hello", schema)
check("a total smaller than the prompt bills no written tokens", (out, sent, wrote), ([], 10, 0))
post_replies.append(Reply(429, "quota GenerateRequestsPerDay", headers={"retry-after": "12"}))
err = expect_raise("a limited Gemini call is reported", lambda: ai.call("gemini-a", "hello", schema), ai.AIError, "HTTP 429")
check("the wait and the daily flag survive the error", (err.wait, err.daily), (12.0, True))
post_replies.append(Reply(401, "bad key env-key-123456789"))
err = expect_raise("a failed Gemini call is reported", lambda: ai.call("gemini-a", "hello", schema), ai.AIError, "HTTP 401")
check("the key is never left in an error message", "env-key-123456789" in str(err), False)

# ---------------------------------------------------------------- OpenAI-compatible services
for provider, host in (
    ("openai", "api.openai.com"),
    ("xai", "api.x.ai"),
    ("groq", "api.groq.com"),
    ("openrouter", "openrouter.ai"),
):
    use(provider, ["chat-a"], key="env-key-123456789")
    post_replies.append(
        Reply(
            200,
            {
                "choices": [{"message": {"content": '```json\n{"items":[{"hook":"CHAT"}]}\n```'}}],
                "usage": {"prompt_tokens": 21, "completion_tokens": 4},
            },
        )
    )
    out, sent, wrote = ai.call("chat-a", "hello", schema)
    check(provider + ": the answer is unwrapped and tokens read", (out, sent, wrote), ([{"hook": "CHAT"}], 21, 4))
    check(
        provider + ": bearer key and model address",
        host in posts[-1]["url"] and posts[-1]["headers"]["Authorization"] == "Bearer env-key-123456789",
        True,
    )
    check(provider + ": streaming is turned off", posts[-1]["json"]["stream"], False)

use("openai", ["chat-a"])
for status in (400, 422):
    post_replies.append(Reply(status, {"error": {"message": "not supported"}}))
post_replies.append(
    Reply(200, {"choices": [{"message": {"content": '[{"hook":"OK"}]'}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}})
)
out, _s, _w = ai.call("chat-a", "hello", schema)
shapes = [p["json"] for p in posts]
check("a refused extra setting is dropped and the call is retried", (out, len(shapes)), ([{"hook": "OK"}], 3))
check(
    "the four shapes lose one extra each time",
    [("temperature" in b, "max_tokens" in b, "response_format" in b) for b in shapes],
    [(True, True, True), (False, True, True), (False, False, True)],
)

post_replies.append(
    Reply(200, {"choices": [{"message": {"content": "   ", "tool_calls": [{"function": {"arguments": '{"items":[{"hook":"TOOL"}]}'}}]}}]})
)
out, _s, _w = ai.call("chat-a", "hello", schema)
check("a model that answers with a tool call is read", out, [{"hook": "TOOL"}])
post_replies.append(Reply(200, {"choices": [{"finish_reason": "length", "message": {"content": '[{"hook":"X"}]'}}]}))
expect_raise("a length-limited answer is refused", lambda: ai.call("chat-a", "hello", schema), ai.AIError, "length limit")


# an event stream instead of JSON: text and tool-call arguments are both assembled
def event(body):
    return "data: " + json.dumps(body)


sse = "\n\n".join(
    [
        event({"choices": [{"delta": {"content": '[{"hook":"S'}}]}),
        event({"choices": [{"delta": {"content": 'TREAM"}]}'}}]}),
        event({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "{}"}}]}}]}),
        event({"usage": {"prompt_tokens": 5, "completion_tokens": 2}}),
        "data: [DONE]",
        "not an event",
    ]
)
post_replies.append(Reply(200, ValueError("not json"), text=sse))
out, sent, wrote = ai.call("chat-a", "hello", schema)
check("a streamed answer is assembled", (out, sent, wrote), ([{"hook": "STREAM"}], 5, 2))
events = ai._from_events(
    "\n".join(
        [
            event({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"a":'}}]}}]}),
            event({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "1}"}}]}}]}),
        ]
    )
)
check("tool-call pieces from a stream are joined", json.loads(events["choices"][0]["message"]["content"]), {"a": 1})
check("an event stream with nothing in it gives empty text", ai._from_events("data: [DONE]")["choices"][0]["message"]["content"], "")
use("custom", ["chat-a"], "http://127.0.0.1:1234/v1/", key="")
post_replies.append(Reply(200, {"choices": [{"message": {"content": '[{"hook":"LOCAL"}]'}}]}))
out, _s, _w = ai.call("chat-a", "hello", schema)
check(
    "a custom service uses its own address and sends no key",
    posts[-1]["url"] == "http://127.0.0.1:1234/v1/chat/completions"
    and "Authorization" not in posts[-1]["headers"]
    and out == [{"hook": "LOCAL"}],
    True,
)

# ---------------------------------------------------------------- Claude
use("anthropic", ["claude-a"])
post_replies.append(
    Reply(
        200,
        {
            "content": [{"type": "text", "text": "no: ignore\n" + json.dumps({"items": [{"hook": "CLAUDE"}]})}],
            "usage": {"input_tokens": 31, "output_tokens": 7},
        },
    )
)
out, sent, wrote = ai.call("claude-a", "hello", schema)
check("Claude's answer inside a sentence and its tokens are read", (out, sent, wrote), ([{"hook": "CLAUDE"}], 31, 7))
check(
    "Claude gets its key, version and address",
    posts[-1]["url"] == "https://api.anthropic.com/v1/messages"
    and posts[-1]["headers"]["x-api-key"] == "env-key-123456789"
    and posts[-1]["headers"]["anthropic-version"] == "2023-06-01",
    True,
)
check("Claude is given the schema in the prompt", '"type": "array"' in posts[-1]["json"]["messages"][0]["content"], True)
post_replies.append(Reply(200, {"stop_reason": "max_tokens", "content": []}))
expect_raise("a Claude answer that hit the cap is refused", lambda: ai.call("claude-a", "hello", schema), ai.AIError, "length limit")

# ---------------------------------------------------------------- one picture
JPEG = b"\xff\xd8\xff" + b"picture" * 4
use("gemini", ["gemini-a"])
check("no picture means no request", ai.see("look", b""), ("", 0, 0))
post_replies.append(
    Reply(
        200,
        {"candidates": [{"content": {"parts": [{"text": "CAM x=10%"}]}}], "usageMetadata": {"promptTokenCount": 4, "totalTokenCount": 9}},
    )
)
check("Gemini reads a picture", ai.see("look", JPEG), ("CAM x=10%", 4, 5))
check(
    "the JPEG goes as base64 inline data",
    posts[-1]["json"]["contents"][0]["parts"][1]["inline_data"],
    {"mimeType": "image/jpeg", "data": __import__("base64").b64encode(JPEG).decode()},
)
use("openai", ["chat-a"])
post_replies.append(Reply(200, {"choices": [{"message": {"content": "NO CAM"}}], "usage": {"prompt_tokens": 6, "completion_tokens": 2}}))
check("an OpenAI-compatible model reads a picture", ai.see("look", JPEG), ("NO CAM", 6, 2))
check(
    "the picture is a data URL in the message",
    posts[-1]["json"]["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,"),
    True,
)
use("anthropic", ["claude-a"])
post_replies.append(Reply(200, {"content": [{"type": "text", "text": "NO CAM"}], "usage": {"input_tokens": 8, "output_tokens": 1}}))
check("Claude reads a picture", ai.see("look", JPEG), ("NO CAM", 8, 1))
check("the picture comes before the question for Claude", posts[-1]["json"]["messages"][0]["content"][0]["type"], "image")
post_replies.append(Reply(403, "forbidden"))
expect_raise("a refused picture is reported", lambda: ai.see("look", JPEG), ai.AIError, "HTTP 403")
use("nonsense", ["x"])
check("a service that cannot look at pictures is harmless", ai.see("look", JPEG), ("", 0, 0))

# ---------------------------------------------------------------- model lists
use("gemini", ["gemini-a"])
get_replies.append(
    Reply(
        200,
        {
            "models": [
                {"name": "models/z", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/a", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/no", "supportedGenerationMethods": ["embedContent"]},
                {"name": "models/z", "supportedGenerationMethods": ["generateContent"]},
            ]
        },
    )
)
check("Gemini's usable models are sorted and unique", ai.list_models(), ["a", "z"])
check(
    "Gemini's list is asked with a key header",
    gets[-1]["headers"]["x-goog-api-key"] == "env-key-123456789" and gets[-1]["params"] == {"pageSize": 200},
    True,
)
use("anthropic", ["claude-a"])
get_replies.append(Reply(200, {"data": [{"id": "claude-b"}, "not an object", {"id": "claude-a"}]}))
check("Claude's models are read", ai.list_models(), ["claude-a", "claude-b"])
check(
    "Claude's list gets its version header",
    gets[-1]["headers"]["anthropic-version"] == "2023-06-01" and gets[-1]["url"].endswith("/models"),
    True,
)
use("openai", ["chat-a"])
get_replies.append(Reply(200, {"data": [{"id": "gpt-b"}, {"id": "gpt-a"}]}))
check(
    "OpenAI's models are read with a bearer key", ai.list_models("openai", "https://example.test/v1", "temp-key-abcdef"), ["gpt-a", "gpt-b"]
)
check(
    "the given address and key are used",
    gets[-1]["url"] == "https://example.test/v1/models" and gets[-1]["headers"]["Authorization"] == "Bearer temp-key-abcdef",
    True,
)
get_replies.append(Reply(401, "bad key temp-key-abcdef"))
err = expect_raise(
    "a refused model list is reported",
    lambda: ai.list_models("openai", "https://example.test/v1", "temp-key-abcdef"),
    ai.AIError,
    "HTTP 401",
)
check("the key is redacted from a model-list error", str(err), lambda s: "<key>" in s and "temp-key-abcdef" not in s)
expect_raise("an unknown service has no model list", lambda: ai.list_models("myspace"), ValueError, "Choose one of the AI services")
use("custom", ["local"], key="")
expect_raise("a custom service needs an address first", lambda: ai.list_models(), ValueError, "Enter the service's address")

# ---------------------------------------------------------------- the test button and errors in plain words
use("openai", ["test-model"])
post_replies.append(Reply(200, {"choices": [{"message": {"content": '["ok"]'}}], "usage": {"prompt_tokens": 3, "completion_tokens": 1}}))
check("the test button makes one small call", ai.test(), {"ok": True, "model": "test-model", "tokens": 4, "answer": "['ok']"})
expect_raise(
    "the test button needs a working setup",
    lambda: ai.save("myspace", ["m"], "", "k") or ai.test(),
    ValueError,
    "Choose one of the AI services",
)
use("gemini", ["missing-model"])
for message, expected in (
    ("HTTP 404 model not found", "doesn't know the model"),
    ("HTTP 403 forbidden", "refused the key"),
    ("HTTP 429 quota exceeded", "hit its limit"),
    ("connection refused", "couldn't reach Gemini"),
    ("something else", ""),
):
    check(
        "an error is explained: " + message,
        ai.explain(RuntimeError(message)),
        (lambda want: lambda got: want in got)(expected) if expected else "",
    )
use("ollama", ["llama3"], key="")
check("an unreachable local service also gets a hint", ai.explain(RuntimeError("connection refused")), lambda s: "Ollama is running" in s)
use("gemini", ["gemini-a"], key="env-key-123456789")
with ctx.as_user(UID):
    ai.problems.clear()
    ai.note_problem(RuntimeError("HTTP 404 not found"))
    check("a failure is remembered for this user", "doesn't know the model" in ai.problem_text(), True)
    with ctx.as_user("someone_else"):
        check("another user has no such problem", ai.problem_text(), "")
    ai.problems[UID]["at"] -= 7 * 3600
    check("an old failure is forgotten", ai.problem_text(), "")
    ai.note_problem(RuntimeError("HTTP 404 not found"))
    ai.clear_problem()
    check("a clear removes the failure", ai.problem_text(), "")
    ai.note_problem(RuntimeError("just a hiccup"))
    check("an unexplained failure is not shown", ai.problem_text(), "")

# ---------------------------------------------------------------- waits, daily limits and key redaction
err = ai.AIError("HTTP 429", wait=3.5, daily=True)
check("an AI error keeps its wait and daily flag", (err.wait, err.daily), (3.5, True))
check("a Retry-After header is read", ai.asked_wait(Reply(429, "", headers={"retry-after": "17"})), 17.0)
check("Gemini's retryDelay is read", ai.asked_wait(Reply(429, text='{"error":{"details":[{"retryDelay":"33s"}]}}')), 33.0)
check("a bad retryDelay is ignored", ai.asked_wait(Reply(429, text='{"retryDelay":"soon"}')), None)
check("a retry header with no value is ignored", ai.asked_wait(Reply(429, "", headers={"retry-after": "later"})), None)
check("the first rate-limit wait is 20 seconds", brain.retry_wait(ai.AIError("HTTP 429"), 0), 20.0)
check("a live check caps the wait", brain.retry_wait(ai.AIError("HTTP 429", wait=900), 0, timeout=45), 15.0)
check("a daily limit is not waited out", brain.retry_wait(ai.AIError("HTTP 429", daily=True), 0), None)
check("a refused key is not retried", brain.retry_wait(ai.AIError("HTTP 401"), 0), None)
check("there is no wait after the last try", brain.retry_wait(ai.AIError("HTTP 429"), brain.TRIES - 1), None)
check("an overloaded service gets a shorter wait", brain.retry_wait(ai.AIError("HTTP 503"), 0), 5.0)

# ---------------------------------------------------------------- tokens recorded for the person asking
use("gemini", ["gemini-a"])
with ctx.as_user(UID):
    usage.set_context(None, "AI deep video")
    with patch.object(ai, "call", return_value=([{"hook": "USED"}], 12, 5)) as called, patch.object(ai, "clear_problem") as cleared:
        out, model, tokens = brain.ask_ai("find clips", purpose="Deep: test")
    check("ask_ai returns the answer, model and tokens", (out, model, tokens), ([{"hook": "USED"}], "gemini-a", 17))
    check("the AI call got the schema when none was given", called.call_args.args[2] is brain.SCHEMA, True)
    check("a good answer clears the last failure", cleared.call_count, 1)
    s = usage.summary({})
    check(
        "the call is in this user's usage",
        (s["calls_total"], s["tokens_total"], s["recent"][0]["where"], s["recent"][0]["purpose"]),
        (1, 17, "AI deep video", "Deep: test"),
    )
    with (
        patch.object(ai, "call", side_effect=RuntimeError("The model is down")),
        patch.object(brain.time, "sleep"),
        patch.object(ai, "note_problem") as noted,
    ):
        expect_raise("a failed AI call is reported", lambda: brain.ask_ai("find clips"), RuntimeError, "did not answer")
    check("the failure was handed to the dashboard", noted.call_count, 1)
    with patch.object(config, "ai_ready", return_value=False):
        expect_raise("a server with no AI setup says so", lambda: brain.ask_ai("find clips"), RuntimeError, "not set up")

# ---------------------------------------------------------------- tidy up
ai.httpx.post, ai.httpx.get = real_post, real_get
shutil.rmtree(DATA, ignore_errors=True)
print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
