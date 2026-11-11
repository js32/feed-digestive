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
    assert "/v/1/" not in html and "👍" not in html  # Daumen gibt es nicht mehr


def _two_digests():
    def item(i, topic):
        return {"entry_id": str(i), "title": f"T{i}", "url": f"https://example.org/{i}", "feed": "Blog",
                "score": 8, "interest_topic": topic}
    return [{"id": "d1", "date": "10.11.2026", "generated_at": "2026-11-10T05:00:00+00:00",
             "items": [item(1, "Künstliche Intelligenz"), item(2, "Philosophie"), item(3, None)]},
            {"id": "d2", "date": "11.11.2026", "generated_at": "2026-11-11T05:00:00+00:00",
             "items": [item(4, "Künstliche Intelligenz")]}]


def test_topic_slug():
    from app.feed import topic_slug
    assert topic_slug("Künstliche Intelligenz") == "kuenstliche-intelligenz"
    assert topic_slug("Food for thought!") == "food-for-thought"
    assert topic_slug("../../etc/passwd") == "etc-passwd"
    assert topic_slug("  ") == ""


def test_topic_feeds_written_and_cleaned(settings):
    folder = settings.data_dir / "topics"
    folder.mkdir()
    (folder / "veraltet.xml").write_text("<feed/>")
    write_feed(settings, _two_digests())
    assert sorted(p.name for p in folder.glob("*.xml")) == ["kuenstliche-intelligenz.xml", "philosophie.xml"]
    root = ET.parse(folder / "kuenstliche-intelligenz.xml").getroot()
    titles = [e.find(f"{ATOM}title").text for e in root.findall(f"{ATOM}entry")]
    assert titles == ["T4", "T1"]  # neueste zuerst, ein Eintrag pro Artikel
    assert root.find(f"{ATOM}title").text.endswith("Künstliche Intelligenz")
    import json
    index = json.loads((folder / "index.json").read_text())
    assert {t["slug"]: t["count"] for t in index} == {"kuenstliche-intelligenz": 2, "philosophie": 1}


def test_topic_feeds_backfill_from_history(settings):
    """Digests von vor den Themenfeeds haben kein interest_topic – das Thema kommt aus dem Protokoll."""
    import json
    (settings.data_dir / "history.jsonl").write_text(json.dumps({"entry_id": "1", "topic": "Philosophie"}) + "\n")
    write_feed(settings, _digest())
    assert (settings.data_dir / "topics" / "philosophie.xml").exists()
