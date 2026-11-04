"""Feed-Ausgabe: LLM- und Feed-Inhalte werden maskiert, unsichere Links verschwinden."""
import xml.etree.ElementTree as ET

from app.feed import write_feed

ATOM = "{http://www.w3.org/2005/Atom}"


def _digest(**item):
    base = {"entry_id": "1", "title": "Orig", "url": "https://example.org/a", "feed": "Blog", "score": 8}
    return [{"id": "d1", "date": "04.11.2026", "generated_at": "2026-11-04T05:00:00+00:00",
             "items": [{**base, **item}]}]


def _content(settings):
    root = ET.parse(settings.data_dir / "feed.xml").getroot()
    return root.find(f"{ATOM}entry/{ATOM}content").text


def test_llm_output_is_escaped(settings):
    write_feed(settings, _digest(digest_title='<script>alert(1)</script>', excerpt='<img src=x onerror=alert(1)>',
                                 why='"><b>x</b>'))
    html = _content(settings)
    assert "<script>" not in html and "<img src=x" not in html and "<b>x</b>" not in html
    assert "&lt;script&gt;" in html


def test_unsafe_link_dropped_in_items_mode(env):
    env.setenv("FEED_MODE", "items")
    env.setenv("TRACK_CLICKS", "false")
    from app.config import Settings
    s = Settings.from_env()
    write_feed(s, _digest(url="javascript:alert(1)"))
    xml = (s.data_dir / "feed.xml").read_text()
    assert "javascript:" not in xml


def test_tracked_links_use_token_path(settings):
    write_feed(settings, _digest())
    html = _content(settings)
    assert "https://digest.example.org/tok/r/1" in html
    assert "https://digest.example.org/tok/v/1/up" in html
