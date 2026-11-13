"""LLM-Client: Denkmodus-Fallback (auch parallel), Rate-Limit-Lernen, JSON-Extraktion."""
import json
import threading
import time

import httpx
import pytest

from app import llm as L


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(L.audit, "record", lambda *a, **k: None)
    L.reset_rate_limits()
    yield
    L.reset_rate_limits()


def _client(env, mode):
    calls = []

    def handler(req):
        body = json.loads(req.content)
        eff = body.get("reasoning_effort")
        calls.append(eff)
        time.sleep(0.02)  # damit sich parallele Anfragen überlappen
        if mode == "large4" and eff == "none":
            return httpx.Response(400, text='{"message":"reasoning_effort none is not supported"}')
        if mode == "large3" and eff is not None:
            return httpx.Response(400, text='{"message":"reasoning_effort is not enabled for this model"}')
        if mode == "other":
            return httpx.Response(400, text='{"message":"invalid messages"}')
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": '{"ok": 1}'}}]})

    m = L.LLM("mistral", "m")
    m.http = httpx.Client(transport=httpx.MockTransport(handler))
    return m, calls


def _parallel(fn, n=8):
    errors = []

    def go():
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=go) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return errors


@pytest.mark.parametrize("mode,chain", [("large4", ["low"]), ("large3", []), ("small", ["none", "low"])])
def test_reasoning_fallback_parallel(env, mode, chain):
    env.setenv("REASONING_EFFORT", "none")
    m, calls = _client(env, mode)
    assert _parallel(lambda: m.complete_json("s", "u")) == []
    assert m._effort_chain == chain


def test_unrelated_400_is_an_error_not_a_fallback(env):
    env.setenv("REASONING_EFFORT", "none")
    m, _ = _client(env, "other")
    with pytest.raises(L.LLMError):
        m.complete("s", "u")
    assert m._effort_chain == ["none", "low"]


def test_no_reasoning_param_without_setting(env):
    m, calls = _client(env, "small")
    m.complete("s", "u")
    assert calls == [None]


def test_rate_limit_burst_counts_once():
    for _ in range(5):
        L._learn_rate_limit("p", "")
    assert L._learned["p"] == 5.0
    L._learned_at["p"] -= 10  # später: echter zweiter Engpass
    L._learn_rate_limit("p", "")
    assert L._learned["p"] == 10.0


def test_rate_limit_respects_retry_after_and_cap():
    L._learn_rate_limit("p", "30")
    assert L._learned["p"] == 30.0
    L._learn_rate_limit("p", "999")
    assert L._learned["p"] == L.LEARNED_MAX


@pytest.mark.parametrize("text,expected", [
    ('{"a": 1}', {"a": 1}),
    ('```json\n{"a": 1}\n```', {"a": 1}),
    ('<think>hmm {"x": 0}</think>{"a": 1}', {"a": 1}),
    ('Hier: [1, 2] fertig', [1, 2]),
])
def test_extract_json(text, expected):
    assert L.extract_json(text) == expected


def test_extract_json_fails_cleanly():
    with pytest.raises(ValueError):
        L.extract_json("")


def test_extract_json_prefers_the_outer_object():
    """Objekt mit eckigen Klammern im Text und einem Nachsatz: nicht die Liste aus der Mitte fischen."""
    text = 'Hier das Ergebnis: {"title": "T", "excerpt": "Siehe [1] und [2]."} Ich hoffe, das hilft.'
    assert L.extract_json(text) == {"title": "T", "excerpt": "Siehe [1] und [2]."}
    assert L.extract_json('Antwort: [{"a": 1}] fertig') == [{"a": 1}]
