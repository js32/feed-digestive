"""Auswahl: Schwelle, Auffüllen, höchstens 3 pro Feed, Themenvielfalt."""
from app.digest import MAX_PER_FEED, select


def _items(spec):
    """spec: Liste von (score, feed, topic)"""
    return [{"id": str(i), "title": f"T{i}", "feed": f, "feed_id": f, "score": s, "score_topic": t, "text": "x" * 100}
            for i, (s, f, t) in enumerate(spec)]


def test_threshold_and_top_n():
    items = _items([(9, f"f{i}", f"t{i}") for i in range(12)] + [(5, "g", "u")])
    chosen = select(items, top_n=10, min_score=6)
    assert len(chosen) == 10 and all(it["score"] >= 6 for it in chosen)


def test_max_per_feed():
    items = _items([(9, "hn", f"t{i}") for i in range(6)] + [(7, "other", "x")])
    chosen = select(items, top_n=10, min_score=6)
    assert sum(it["feed_id"] == "hn" for it in chosen) == MAX_PER_FEED


def test_fill_up_marks_filler():
    items = _items([(8, "a", "x"), (4, "b", "y"), (4, "c", "z"), (1, "d", "w")])
    chosen = select(items, top_n=10, min_score=6, min_items=3, floor_score=3)
    assert [it["score"] for it in chosen] == [8, 4, 4]
    assert [bool(it.get("filler")) for it in chosen] == [False, True, True]


def test_topic_variety_prefers_other_topic_just_below():
    # 5× KI (je eigener Feed) über der Schwelle, Philosophie einen Punkt darunter
    items = _items([(9, f"f{i}", "ki") for i in range(5)] + [(5, "p", "philosophie")])
    chosen = select(items, top_n=4, min_score=6, max_per_topic=3)
    topics = [it["score_topic"] for it in chosen]
    assert topics.count("ki") == 3 and "philosophie" in topics


def test_unscored_items_ignored():
    items = _items([(9, "a", "x")]) + [{"id": "n", "title": "n", "feed": "b", "feed_id": "b", "score": None, "text": ""}]
    assert [it["id"] for it in select(items, top_n=10, min_score=6)] == ["0"]
