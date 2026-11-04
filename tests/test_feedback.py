"""Sichere Links, Vorab-Laden, kein offener Redirect."""
import json

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


def test_votes_last_one_wins():
    rows = [{"entry_id": "a", "kind": "up"}, {"entry_id": "a", "kind": "down"}, {"entry_id": "b", "kind": "up"}]
    assert feedback.votes(rows) == {"a": "down", "b": "up"}
