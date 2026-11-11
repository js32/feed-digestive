"""„Warum steht das hier?“ und das Interessen-Thema für die Themenfeeds."""
from app.digest import explain, interest_topic, select


def _item(i, score, topic="x", feed=None, **extra):
    return {"id": str(i), "title": f"T{i}", "feed": feed or f"f{i}", "feed_id": feed or f"f{i}", "score": score,
            "score_topic": topic, "text": "x" * 100, **extra}


def test_select_records_how_an_item_got_in():
    items = [_item(i, 9, "ki") for i in range(4)] + [_item(10, 5, "philosophie"), _item(11, 4, "garten")]
    chosen = select(items, top_n=6, min_score=6, min_items=6, floor_score=3, max_per_topic=3)
    via = {it["id"]: it["selected_via"] for it in chosen}
    assert via["0"] == "score" and via["10"] == "variety" and via["11"] == "filler"


def test_explain_lists_reasons(settings):
    settings.prefilter = "laya"
    it = _item(1, 8, score_why="tiefe Analyse", prefilter=6.24, prefilter_via="topic:Philosophie",
               prefilter_why="Thema: Philosophie (0.70), Tiefe 0.62", selected_via="score",
               similar_to="Ein Stern-Titel", similar_sim=0.61)
    reasons = {r["label"]: r for r in explain(it, settings, "Philosophie")}
    assert list(reasons) == ["Auswahl", "Note", "Thema", "Vorauswahl (Laya)", "Stern-Ähnlichkeit"]
    assert reasons["Note"]["text"] == "8/10 – tiefe Analyse" and reasons["Note"]["value"] == 8.0
    assert "Mindestplatz für das Thema Philosophie" in reasons["Vorauswahl (Laya)"]["text"]
    assert reasons["Stern-Ähnlichkeit"]["value"] == 6.1


def test_explain_special_cases(settings):
    assert "Zufallsfund" in explain(_item(1, 3, explore=True), settings, None)[0]["text"]
    assert "Aufgefüllt" in explain(_item(1, 4, selected_via="filler"), settings, None)[0]["text"]
    assert "Themenvielfalt" in explain(_item(1, 5, selected_via="variety"), settings, None)[0]["text"]
    cached = {r["label"]: r["text"] for r in explain(_item(1, 7, cached_score=7), settings, None)}
    assert "früheren Lauf" in cached["Note"]


def test_interest_topic():
    labels = ["Künstliche Intelligenz", "Philosophie"]
    assert interest_topic({"score_topic": "philosophie"}, labels) == "Philosophie"
    assert interest_topic({"score_topic": "Sonstiges"}, labels) is None
    assert interest_topic({"score_topic": "Neues Thema"}, labels) == "Neues Thema"
    by_feature = {"topic_ki": "Künstliche Intelligenz", "topic_phil": "Philosophie"}
    feats = {"laya_features": {"topic_ki": 0.8, "topic_phil": 0.3, "depth": 0.9}}
    assert interest_topic(feats, labels, by_feature) == "Künstliche Intelligenz"
    assert interest_topic({"laya_features": {"topic_ki": 0.4}}, labels, by_feature) is None


def test_prefilter_records_how_an_item_got_through():
    """apply_prefilter mit nachgebautem Filter (ohne Laya): Themenplatz, Wert, Nachrücker, Kontrollprobe."""
    from app.laya_filter import apply_prefilter

    class FakeFilter:
        spec = {"topics": {"ki": {"label": "KI"}, "phil": {"label": "Philosophie"}}, "penalties": {}}

        def score(self, items, learner=None, distilled=None):
            pass  # Werte stehen schon in den Artikeln

    def it(i, value, ki=0.0, phil=0.0):
        return {"id": i, "prefilter": value, "laya_features": {"topic_ki": ki, "topic_phil": phil}}

    items = [it(1, 9.0, ki=0.9), it(2, 8.0, ki=0.8), it(3, 3.0, phil=0.9), it(4, 7.0), it(5, 1.0), it(6, 0.5)]
    kept = apply_prefilter(items, FakeFilter(), keep=5, min_value=5.0, min_keep=4, per_topic=1, explore=1)
    via = {x["id"]: x["prefilter_via"] for x in kept}
    assert via[3] == "topic:Philosophie"      # unter der Schwelle, aber das Thema bekommt seinen Platz
    assert via[1] == "topic:KI" and via[2] == "score" and via[4] == "score"
    assert sorted(via.values()).count("explore") == 1 and len(kept) == 5
