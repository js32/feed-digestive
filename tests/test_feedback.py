"""Sichere Links, Vorab-Laden, kein offener Redirect."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from app import feedback


@pytest.mark.parametrize("url,ok", [
    ("https://example.org/a?b=1", True),
    ("http://example.org", True),
    ("HTTPS://EXAMPLE.ORG/x", True),
    ("https://example.org/mit leerzeichen.html", True),
    ("javascript:alert(1)", False),
    ("data:text/html,<b>x</b>", False),
    ("ftp://example.org", False),
    ("https://example.org/\r\nSet-Cookie: x=1", False),
    ("//example.org", False),
    ("", False),
    (None, False),
])
def test_safe_url(url, ok):
    assert (feedback.safe_url(url) != "") is ok


@pytest.mark.parametrize("headers,human", [
    ({}, True),
    ({"Sec-Purpose": "prefetch"}, False),
    ({"Sec-Purpose": "prefetch;prerender"}, False),
    ({"Purpose": "prefetch"}, False),
    ({"X-Moz": "prefetch"}, False),
])
def test_prefetch_is_not_human(headers, human):
    assert feedback.is_human("Mozilla/5.0 (iPhone)", headers) is human


@pytest.mark.parametrize("ua", ["", None, "Googlebot/2.1", "curl/8.0", "facebookexternalhit/1.1"])
def test_bots_are_not_human(ua):
    assert not feedback.is_human(ua)


def test_lookup_url_only_known_and_safe(tmp_path):
    digests = [{"items": [{"entry_id": "1", "url": "https://ok.example/a"},
                          {"entry_id": "2", "url": "javascript:alert(1)"}]}]
    (tmp_path / "digests.json").write_text(json.dumps(digests))
    assert feedback.lookup_url(tmp_path, "1") == "https://ok.example/a"
    assert feedback.lookup_url(tmp_path, "2") is None
    assert feedback.lookup_url(tmp_path, "999") is None


NOW = datetime(2026, 11, 11, tzinfo=timezone.utc)


def _ago(days):
    return (NOW - timedelta(days=days)).isoformat()


@pytest.mark.parametrize("days,expected", [(0, 1.0), (30, 0.5), (60, 0.25), (-5, 1.0)])
def test_fade(days, expected):
    assert feedback.fade(_ago(days), 30, NOW) == pytest.approx(expected)


def test_fade_off_or_unreadable():
    assert feedback.fade(_ago(400), 0, NOW) == 1.0
    assert feedback.fade("kein Datum", 30, NOW) == 1.0
    assert feedback.fade(None, 30, NOW) == 1.0


def test_signals_fade_and_keep_freshest():
    rows = [{"entry_id": "a", "kind": "click", "ts": _ago(60)},
            {"entry_id": "a", "kind": "click", "ts": _ago(0)},
            {"entry_id": "b", "kind": "star", "ts": _ago(30)}]
    sig = feedback.signals(rows, 30, NOW)
    assert sig["a"]["click"] == pytest.approx(1.0)
    assert sig["b"]["star"] == pytest.approx(0.75)
    assert feedback.signals(rows)["b"]["star"] == 1.5  # ohne Verblassen volles Gewicht


def test_old_thumb_votes_no_longer_count():
    rows = [{"entry_id": "a", "kind": "up", "ts": _ago(1)}, {"entry_id": "b", "kind": "down", "ts": _ago(1)},
            {"entry_id": "b", "kind": "click", "ts": _ago(1)}]
    assert feedback.signals(rows) == {"b": {"click": 1.0}}
