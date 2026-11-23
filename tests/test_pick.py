"""Nachholen: Artikel aus "Knapp nicht drin" in den neuesten Digest – mit Titel/Auszug, Feed,
Lernsignal; nie doppelt, nie während eines Laufs, nur angemeldet."""
import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer

import httpx
import pytest

from app import pick as P
from app.config import Settings

NOW = datetime.now(timezone.utc)


class FakeSource:
    def __init__(self, missing=False, broken=False):
        self.missing, self.broken = missing, broken

    def entry(self, entry_id):
        if self.broken:
            raise httpx.ConnectError("Reader nicht erreichbar")
        if self.missing:
            return None
        return {"id": entry_id, "title": "Ein knapp verpasster Essay", "url": "https://example.org/essay",
                "feed_title": "Blog", "feed_id": "7", "published_at": NOW.isoformat(),
                "content": "<p>" + "Gedanke " * 120 + "</p>"}


class FakeLLM:
    is_local = False
    calls = 0

    def __init__(self, provider, model):
        pass

    def complete_json(self, system, user, max_tokens=0, purpose=""):
        FakeLLM.calls += 1
        assert "Gedanke" in user or "kein Text im Feed" in user
        return {"title": "Ein Essay auf Deutsch", "excerpt": "Du erfährst etwas.", "why": "Weil."}


@pytest.fixture
def setup(settings, monkeypatch):
    s = settings
    FakeLLM.calls = 0
    monkeypatch.setattr(P, "LLM", FakeLLM)
    monkeypatch.setattr(P, "make_source", lambda s: FakeSource())
    hist = [{"run": "r1", "ts": (NOW - timedelta(hours=5)).isoformat(), "entry_id": "42", "title": "Ein knapp verpasster Essay",
             "url": "https://example.org/essay", "feed": "Blog", "features": {"topic_phil": 0.8}, "laya_v": "v1",
             "prefilter": 5.5, "topic": "Philosophie", "score": 5, "shown": False},
            {"run": "r1", "ts": (NOW - timedelta(hours=5)).isoformat(), "entry_id": "1", "title": "Gezeigt",
             "url": "https://example.org/1", "feed": "Blog", "score": 8, "shown": True}]
    (s.data_dir / "history.jsonl").write_text("".join(json.dumps(r) + "\n" for r in hist))
    digests = [{"id": "old", "date": "21.11.2026", "generated_at": (NOW - timedelta(days=2)).isoformat(), "items": []},
               {"id": "new", "date": "23.11.2026", "generated_at": (NOW - timedelta(hours=4)).isoformat(), "items": [
                   {"entry_id": "1", "title": "Gezeigt", "url": "https://example.org/1", "feed": "Blog", "score": 8,
                    "topic": "Künstliche Intelligenz"}]}]
    (s.data_dir / "digests.json").write_text(json.dumps(digests))
    return s


def _digests(s):
    return json.loads((s.data_dir / "digests.json").read_text())


def test_pick_appends_to_latest_digest(setup):
    s = setup
    item = P.pick(s, "42")
    assert item["digest_title"] == "Ein Essay auf Deutsch" and item["picked"] and item["topic"] == P.SECTION
    assert item["interest_topic"] == "Philosophie"
    assert item["reasons"][0]["text"].startswith("Von dir nachgeholt")
    old, new = _digests(s)
    assert old["items"] == [] and [str(i["entry_id"]) for i in new["items"]] == ["1", "42"]
    feed = (s.data_dir / "feed.xml").read_text()
    assert "↑ Ein Essay auf Deutsch" in feed and "von dir nachgeholt" in feed
    assert (s.data_dir / "topics" / "philosophie.xml").exists()

    rows = [json.loads(line) for line in (s.data_dir / "history.jsonl").read_text().splitlines()]
    assert rows[-1]["entry_id"] == "42" and rows[-1]["shown"] and rows[-1]["features"] == {"topic_phil": 0.8}
    fb = [json.loads(line) for line in (s.data_dir / "feedback.jsonl").read_text().splitlines()]
    assert fb[-1]["entry_id"] == "42" and fb[-1]["kind"] == "pick"


def test_pick_is_the_strongest_learning_signal(setup):
    from app import learn
    s = setup
    P.pick(s, "42")
    X, y, w, stats = learn.build_dataset(s.data_dir, s.feedback_days, s.signal_half_life_days)
    assert stats["pick"] == 1 and max(w) == pytest.approx(2.0, abs=0.01)
    assert "1 nachgeholt" in learn.describe(s.data_dir, s.feedback_days, {}, s.signal_half_life_days)


def test_no_double_pick(setup):
    s = setup
    P.pick(s, "42")
    with pytest.raises(P.PickError) as e:
        P.pick(s, "42")
    assert e.value.code == 409 and FakeLLM.calls == 1
    assert sum(str(i["entry_id"]) == "42" for d in _digests(s) for i in d["items"]) == 1


@pytest.mark.parametrize("entry_id,code", [("999", 404), ("1", 409)])
def test_unknown_or_already_shown(setup, entry_id, code):
    with pytest.raises(P.PickError) as e:
        P.pick(setup, entry_id)
    assert e.value.code == code and FakeLLM.calls == 0


def test_not_during_a_run(setup, monkeypatch):
    monkeypatch.setattr(P.status, "run_active", lambda d: True)
    with pytest.raises(P.PickError) as e:
        P.pick(setup, "42")
    assert e.value.code == 409


def test_no_digest_yet(setup):
    (setup.data_dir / "digests.json").write_text("[]")
    with pytest.raises(P.PickError) as e:
        P.pick(setup, "42")
    assert e.value.code == 409


@pytest.mark.parametrize("source", [FakeSource(missing=True), FakeSource(broken=True)])
def test_works_with_title_only_when_reader_has_no_text(setup, monkeypatch, source):
    monkeypatch.setattr(P, "make_source", lambda s: source)
    item = P.pick(setup, "42")
    assert item["url"] == "https://example.org/essay" and item["digest_title"] == "Ein Essay auf Deutsch"


# ---------- über den Webserver ----------
@pytest.fixture
def server(setup, env, monkeypatch):
    env.setenv("STATUS_PASSWORD", "pw")
    s = Settings.from_env()
    from app.__main__ import _make_handler
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(s))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    from app import auth
    cookie = {"Cookie": f"{auth.COOKIE}={auth.make_cookie('pw', s.feed_token)}"}
    with httpx.Client(base_url=f"http://127.0.0.1:{httpd.server_port}") as c:
        yield c, cookie, s
    httpd.shutdown()


def test_http_pick_needs_login_and_same_origin(server):
    c, cookie, s = server
    assert c.post("/tok/status/pick/42").status_code == 401
    host = c.base_url.netloc.decode()
    assert c.post("/tok/status/pick/42", headers={**cookie, "Origin": "https://evil.example"}).status_code == 403
    assert c.post("/tok/status/pick/../x", headers=cookie).status_code == 404
    assert c.get("/tok/status/pick/42", headers=cookie).status_code == 404   # nur POST
    r = c.post("/tok/status/pick/42", headers={**cookie, "Origin": f"http://{host}"})
    assert r.status_code == 200 and r.json()["digest_title"] == "Ein Essay auf Deutsch"
    again = c.post("/tok/status/pick/42", headers=cookie)
    assert again.status_code == 409 and "schon" in again.text


def test_near_misses_carry_entry_id(server):
    c, cookie, s = server
    near = c.get("/tok/status.json", headers=cookie).json()["scores"]["near_misses"]
    assert [n["entry_id"] for n in near] == ["42"]
    c.post("/tok/status/pick/42", headers=cookie)
    status = c.get("/tok/status.json", headers=cookie).json()
    assert status["scores"]["near_misses"] == []
    assert status["latest"]["items"][-1]["picked"] is True
