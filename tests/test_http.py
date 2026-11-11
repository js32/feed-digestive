"""Webserver: nur der geheime Pfad, Stimmen zählen nur von Menschen, Berichte nur aus fester Liste."""
import json
import threading
from http.server import ThreadingHTTPServer

import httpx
import pytest

from app.__main__ import REPORTS, _make_handler

UA = {"User-Agent": "Mozilla/5.0 (iPhone)"}


@pytest.fixture
def server(settings):
    d = settings.data_dir
    (d / "digests.json").write_text(json.dumps([{"id": "d1", "items": [
        {"entry_id": "a1", "title": "Ein Titel <b>", "url": "https://example.org/a"},
        {"entry_id": "bad", "title": "Böse", "url": "javascript:alert(1)"}]}]))
    (d / "feed.xml").write_text("<feed/>")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(settings))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    with httpx.Client(base_url=f"http://127.0.0.1:{httpd.server_port}", follow_redirects=False) as c:
        yield c, d
    httpd.shutdown()


def _feedback(d):
    p = d / "feedback.jsonl"
    return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []


def test_feed_only_with_token(server):
    c, _ = server
    assert c.get("/tok/feed.xml").status_code == 200
    assert c.get("/feed.xml").status_code == 404
    assert c.get("/wrong/feed.xml").status_code == 404
    assert c.get("/wrong/status.json").status_code == 404


def test_status_json_has_no_secrets(server):
    c, _ = server
    body = c.get("/tok/status.json").text
    assert c.get("/tok/status.json").status_code == 200
    assert "test-key" not in body


def test_redirect_known_article(server):
    c, d = server
    r = c.get("/tok/r/a1", headers=UA)
    assert r.status_code == 302 and r.headers["location"] == "https://example.org/a"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert [x["kind"] for x in _feedback(d)] == ["click"]


def test_no_open_redirect(server):
    c, _ = server
    assert c.get("/tok/r/unknown", headers=UA).status_code == 404
    assert c.get("/tok/r/bad", headers=UA).status_code == 404


def test_old_vote_links_are_gone(server):
    c, d = server
    r = c.get("/tok/v/a1/up", headers=UA)
    assert r.status_code == 410 and "Stern" in r.text
    assert _feedback(d) == []


def test_click_ignored_for_bots_and_prefetch(server):
    c, d = server
    for headers in ({"User-Agent": "Googlebot"}, {**UA, "Sec-Purpose": "prefetch"}, {}):
        assert c.get("/tok/r/a1", headers=headers).status_code == 302
    assert _feedback(d) == []


def test_topic_feed_served(server):
    c, d = server
    (d / "topics").mkdir()
    (d / "topics" / "philosophie.xml").write_text("<feed>phil</feed>")
    assert c.get("/tok/topic/philosophie.xml").text == "<feed>phil</feed>"
    assert c.get("/tok/topic/unbekannt.xml").status_code == 404
    assert c.get("/topic/philosophie.xml").status_code == 404           # nur hinter dem geheimen Pfad
    assert c.get("/tok/topic/..%2Fdigests.json.xml").status_code == 404  # kein Ausbruch aus dem Ordner
    assert c.get("/tok/topic/Philosophie.xml").status_code == 404


def test_head_does_not_count(server):
    c, d = server
    c.head("/tok/r/a1", headers=UA)
    assert _feedback(d) == []


def test_report_whitelist(server, monkeypatch):
    c, _ = server
    import app.__main__ as m
    seen = []
    monkeypatch.setattr(m, "_report_text", lambda name: (seen.append(name), (200, f"Bericht {name}"))[1])
    assert c.get("/tok/status/report/learn").text == "Bericht learn"
    for bad in ("x", "../learn", "learn;rm", "laya-report"):
        assert c.get(f"/tok/status/report/{bad}").status_code == 404
    assert c.head("/tok/status/report/learn").status_code == 404  # HEAD startet keinen Bericht
    assert seen == ["learn"]
    assert set(REPORTS) == {"learn", "laya", "privacy", "opens", "profile"}


def test_report_runs_in_subprocess(settings):
    """Echter Aufruf: der Bericht läuft als eigener Prozess mit derselben Umgebung."""
    from app.__main__ import _report_text
    code, text = _report_text("privacy")
    assert code == 200 and "LLM" in text
