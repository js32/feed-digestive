"""Laya-Fragen aus dem Interessenprofil erzeugen:  python -m app laya-questions

Neue Nutzer schreiben nur config/interests.md. Daraus formuliert das Auszugs-LLM (SUMMARY_*) einen
Startkatalog nach den Regeln, die sich für Laya bewährt haben. Ein vorhandener Katalog wird nicht
überschrieben – der Vorschlag landet dann in config/laya_questions.suggested.json.

Der local-only-Teil von interests.md geht dabei nur an lokale Modelle (Ollama), nie in die Cloud.
"""
from __future__ import annotations

import json
import re

from .config import Settings
from .digest import LOCAL_ONLY_RE
from .llm import LLM

# Bewährte Fragen, die für alle gelten (Tiefe und Malus). Kurz, ein Gedanke pro Frage,
# Ausschlüsse ausdrücklich genannt.
DEPTH = {
    "question": "How much depth and original insight does this article offer?",
    "levels": [
        "none: a teaser, short news item or advertisement",
        "little: mostly repeats known facts",
        "some: useful summary or explanation",
        "solid: original analysis, research or detailed explanation",
        "exceptional: outstanding essay or major new finding",
    ],
}
PENALTIES = {
    "promo": {"label": "Werbung/PR",
              "question": "Is this an advertisement, sponsored post, press release or shopping deal?"},
    "clickbait": {"label": "Clickbait/Boulevard",
                  "question": "Is this clickbait, or about celebrities, sports results or lifestyle gossip?"},
    "news": {"label": "Tagesnachricht",
             "question": "Is this a short news report about a current event, for example war, attacks, "
                         "politics, disasters, crime or an event date?"},
}
CURIOUS = {"label": "Food for thought", "weight": 0.6,
           "question": "Is this a thoughtful personal essay or does it present a surprising, unusual idea or fact?"}

SYSTEM = """You write the question catalogue for Laya, a small local yes/no classifier that pre-filters
news articles for one reader. Laya is good at coarse decisions and bad at fine distinctions.

Rules for every topic question:
- English, one sentence, ends with "?", answerable with yes or no from the article text.
- Ask whether the topic is the CENTRAL subject, e.g. "Is X the central subject of this article, ... not just
  a side mention?" or "Does this article explain or discuss X?".
- One idea per question. At most 4 concrete examples in "for example ...".
- If a topic is easily confused with daily news, add an exclusion: "Answer no for news about politics,
  war or events." For society/politics topics ask for analysis, not reports: "an in-depth analysis ...
  rather than a news report".
- Do not create questions for things the reader does NOT want – those are handled separately.
- Merge interests that are near-synonyms; keep distinct interests separate even if they are niche.

Weights: 1.0 for the highest priority, 0.85 for high, 0.7 for medium, 0.6 for low but wanted.

Answer with JSON only:
{"topics": {"<short_snake_case_id>": {"label": "<short German label>", "weight": <number>,
 "question": "<English question>"}, ...}}
Between 5 and 14 topics."""


def generate(s: Settings) -> dict:
    path = s.config_dir / "interests.md"
    if not path.exists():
        raise SystemExit(f"{path} fehlt – zuerst deine Interessen dort beschreiben (Vorlage: config.example/)")
    llm = LLM(s.summary_provider, s.summary_model)
    text = path.read_text(encoding="utf-8")
    if not llm.is_local:
        text = LOCAL_ONLY_RE.sub("", text).strip()
    data = llm.complete_json(SYSTEM, "Reader's interest profile:\n\n" + text, max_tokens=3000,
                             purpose="Laya-Fragen erzeugen")
    topics = {}
    for tid, t in (data.get("topics") or {}).items():
        tid = re.sub(r"[^a-z0-9_]", "_", str(tid).lower()).strip("_")[:30] or f"topic{len(topics)}"
        q = str(t.get("question") or "").strip()
        if not q.endswith("?") or len(q) < 20:
            continue
        try:
            weight = max(0.3, min(1.0, float(t.get("weight", 0.7))))
        except (TypeError, ValueError):
            weight = 0.7
        topics[tid] = {"label": str(t.get("label") or tid)[:40], "weight": weight, "question": q}
    if len(topics) < 3:
        raise SystemExit(f"{llm} hat keinen brauchbaren Fragenkatalog geliefert – interests.md ausführlicher "
                         "schreiben oder ein anderes Modell versuchen.")
    topics.setdefault("curious", CURIOUS)
    return {"_info": f"Erzeugt mit python -m app laya-questions ({llm}). Frei anpassbar.",
            "topics": topics, "depth": DEPTH, "penalties": PENALTIES}


def run(s: Settings) -> None:
    spec = generate(s)
    target = s.config_dir / "laya_questions.json"
    if target.exists():
        target = s.config_dir / "laya_questions.suggested.json"
    target.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{len(spec['topics'])} Themen-Fragen geschrieben nach {target}:\n")
    for tid, t in spec["topics"].items():
        print(f"  {t['label']:<28} {t['weight']:.2f}  {t['question']}")
    if target.name.endswith("suggested.json"):
        print("\nDein bisheriger Katalog bleibt unverändert. Zum Übernehmen:\n"
              "  mv config/laya_questions.suggested.json config/laya_questions.json\n"
              "Achtung: Neue Fragen heißen, dass Laya alle Artikel neu bewertet (beim nächsten Lauf).")
