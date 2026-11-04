"""interests.md lesen (Embedding-Vorfilter): Gewichte nach Überschrift, Abneigungen, Platzhalter."""
from app.embed_filter import parse_interests

TEXT = """# Interessen
## Hohe Priorität
- KI-Sicherheit: Alignment, Evaluierung
- Philosophie
## Mittlere Priorität
- Retro-Computing
- …
## Niedrige Priorität / eher nicht
- Promi-Klatsch
<!-- - auskommentiert -->
"""


def test_parse_interests():
    topics, negatives = parse_interests(TEXT)
    by_label = {t["label"]: t["weight"] for t in topics.values()}
    assert by_label == {"KI-Sicherheit": 1.0, "Philosophie": 1.0, "Retro-Computing": 0.8}
    assert negatives == ["Promi-Klatsch"]
