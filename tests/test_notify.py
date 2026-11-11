"""ntfy: Adresse zerlegen, Nachricht ohne/mit Details, Fehler dürfen nie einen Lauf stören."""
import json

import httpx
import pytest

from app import notify
from app.config import Settings

DIGEST = {"date": "11.11.2026", "items": [
    {"digest_title": "Erster Titel", "topic": "Künstliche Intelligenz"},
    {"digest_title": "Zweiter Titel", "topic": "Künstliche Intelligenz"},
    {"title": "Zufall", "explore": True}]}


@pytest.mark.parametrize("url,expected", [
    ("https://ntfy.sh/mein-thema", ("https://ntfy.sh", "mein-thema")),
    ("https://push.example.org/ntfy/thema/", ("https://push.example.org/ntfy", "thema")),
])
def test_target(url, expected):
    assert notify._target(url) == expected


@pytest.mark.parametrize("url", ["https://ntfy.sh", "ntfy.sh/thema", "ftp://ntfy.sh/thema"])
def test_target_invalid(url):
    with pytest.raises(ValueError):
        notify._target(url)


def test_message_without_details_has_no_titles(settings):
    title, message = notify.digest_message(settings, DIGEST)
    assert title == "Digest vom 11.11.2026: 3 Artikel"
    assert "Titel" not in message and "Intelligenz" not in message


def test_message_with_details(env):
    env.setenv("NTFY_DETAILS", "true")
    _, message = notify.digest_message(Settings.from_env(), DIGEST)
    assert message.splitlines() == ["Künstliche Intelligenz", "• Erster Titel", "• Zweiter Titel", "• 🎲 Zufall"]


def test_send_posts_json_with_token(env, monkeypatch):
    env.setenv("NTFY_URL", "https://ntfy.example.org/thema")
    env.setenv("NTFY_TOKEN", "tk_geheim")
    seen = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        seen.update(url=url, json=json, headers=headers)
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(notify.httpx, "post", fake_post)
    s = Settings.from_env()
    notify.digest_ready(s, DIGEST)
    assert seen["url"] == "https://ntfy.example.org"
    assert seen["json"]["topic"] == "thema" and seen["json"]["title"].startswith("Digest vom")
    assert seen["headers"] == {"Authorization": "Bearer tk_geheim"}
    json.dumps(seen["json"])  # serialisierbar, Umlaute im Titel sind kein Problem


def test_nothing_sent_without_url_or_items(settings, env, monkeypatch):
    monkeypatch.setattr(notify.httpx, "post", lambda *a, **k: pytest.fail("darf nicht senden"))
    notify.digest_ready(settings, DIGEST)          # NTFY_URL nicht gesetzt
    notify.run_failed(settings, RuntimeError("x"))
    env.setenv("NTFY_URL", "https://ntfy.sh/thema")
    notify.digest_ready(Settings.from_env(), {"items": []})
    notify.digest_ready(Settings.from_env(), None)


def test_failures_are_swallowed(env, monkeypatch):
    env.setenv("NTFY_URL", "https://ntfy.sh/thema")

    def boom(*a, **k):
        raise httpx.ConnectError("kein Netz")

    monkeypatch.setattr(notify.httpx, "post", boom)
    s = Settings.from_env()
    assert notify.send(s, "t", "m") is False
    notify.run_failed(s, RuntimeError("x"))  # wirft nicht


def test_failure_message_hides_details_by_default(env, monkeypatch):
    env.setenv("NTFY_URL", "https://ntfy.sh/thema")
    sent = []
    monkeypatch.setattr(notify.httpx, "post", lambda url, json=None, **k: (
        sent.append(json), httpx.Response(200, request=httpx.Request("POST", url)))[1])
    notify.run_failed(Settings.from_env(), RuntimeError("https://intern.example/geheim nicht erreichbar"))
    assert "intern.example" not in sent[0]["message"] and sent[0]["priority"] == 4
