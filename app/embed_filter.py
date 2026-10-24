"""Vorfilter mit lokalen Embeddings (PREFILTER=embeddings) – die schlanke Alternative zu Laya.

Statt ein Modell Fragen beantworten zu lassen, wird jeder Artikel in einen Vektor verwandelt
(fastembed, ~200 MB, Bruchteile einer Sekunde pro Artikel) und mit drei Dingen verglichen:

  Themen    die Stichpunkte unter "Hohe/Mittlere Priorität" in interests.md
  Vorlieben deine gespeicherten Beispiele (examples.txt), Sterne, 👍 und angeklickte Digest-Artikel
  Abneigung "Niedrige Priorität / eher nicht" in interests.md und 👎-Artikel

Daraus entstehen Merkmale wie bei Laya (topic_*, like, dislike). Wert, Themen-Mindestplätze,
Kontrollstichprobe, Lernen aus Klicks und aus den LLM-Bewertungen funktionieren damit unverändert.
Alles läuft lokal; auch der local-only-Teil von interests.md darf hier mitlesen.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

from .feedback import read_jsonl, signals, votes

log = logging.getLogger(__name__)

MAX_EXAMPLES = 500
POS_TOP = 3                 # Vorliebe = Mittel der 3 ähnlichsten positiven Beispiele
NEG_PRIORITY = re.compile(r"niedrig|eher nicht|nicht interess|not interest|low priority|avoid", re.I)
TOPIC_PRIORITY = {"hoh": 1.0, "hoch": 1.0, "high": 1.0, "mittl": 0.8, "mittel": 0.8, "medium": 0.8}


def _p(sim: float, lo: float = 0.2, span: float = 0.4) -> float:
    """Kosinus-Ähnlichkeit (MiniLM: unverwandt ~0–0.2, verwandt ~0.4–0.7) auf 0–1 abbilden."""
    return round(max(0.0, min(1.0, (sim - lo) / span)), 4)


def _slug(text: str) -> str:
    t = text.lower().translate(str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}))
    return re.sub(r"[^a-z0-9]+", "_", t).strip("_")[:30] or "thema"


def parse_interests(text: str) -> tuple[dict, list[str]]:
    """Stichpunkte aus interests.md: Themen (mit Gewicht nach Abschnitt) und Abneigungen."""
    topics: dict[str, dict] = {}
    negatives: list[str] = []
    weight, negative = None, False
    bullet: list[str] | None = None

    def flush():
        nonlocal bullet
        if bullet:
            line = " ".join(bullet)
            clean = re.sub(r"[*_„“\"]", "", line).strip()
            if len(re.sub(r"\W", "", clean)) < 3:  # Platzhalter wie "…"
                bullet = None
                return
            if negative:
                negatives.append(clean)
            elif weight is not None:
                label = re.split(r"[:(]", clean, 1)[0].strip()[:40] or clean[:40]
                tid = _slug(label)
                while tid in topics:
                    tid += "_"
                topics[tid] = {"label": label, "weight": weight, "question": clean}
        bullet = None

    for raw in re.sub(r"<!--.*?-->", "", text, flags=re.S).splitlines():
        line = raw.rstrip()
        if line.startswith("#"):
            flush()
            head = line.lstrip("#").strip().lower()
            negative = bool(NEG_PRIORITY.search(head))
            weight = next((w for k, w in TOPIC_PRIORITY.items() if k in head), None)
            if negative:
                weight = None
        elif re.match(r"\s*[-*]\s+", line):
            flush()
            bullet = [re.sub(r"^\s*[-*]\s+", "", line)]
        elif bullet is not None and line.strip():
            bullet.append(line.strip())
        else:
            flush()
    flush()
    return topics, negatives


class EmbedFilter:
    backend = "embeddings"

    def __init__(self, s, starred_entries: list[dict] | None = None):
        try:
            from .embed import Embedder
            self.embedder = Embedder(s.embedding_model, s.data_dir)
        except ImportError as e:
            raise SystemExit("PREFILTER=embeddings, aber fastembed fehlt – Image mit WITH_EMBEDDINGS=1 bauen") from e
        import numpy as np
        self.np = np
        self.s = s
        interests = s.config_dir / "interests.md"
        text = interests.read_text(encoding="utf-8") if interests.exists() else ""
        topics, self.negatives = parse_interests(text)
        if not topics:  # Fallback: Themen aus dem Laya-Katalog, falls vorhanden
            qfile = s.config_dir / "laya_questions.json"
            if qfile.exists():
                spec = json.loads(qfile.read_text(encoding="utf-8"))
                topics = {k: {"label": v.get("label", k), "weight": v.get("weight", 1.0),
                              "question": f"{v.get('label', k)}: {v.get('question', '')}"}
                          for k, v in spec.get("topics", {}).items()}
        if not topics:
            raise SystemExit("PREFILTER=embeddings braucht Themen: Stichpunkte unter „## Hohe Priorität“ / "
                             "„## Mittlere Priorität“ in config/interests.md")
        self.spec = {"topics": topics, "penalties": {"dislike": {"label": "Abneigung"}}}
        anchor_text = json.dumps([s.embedding_model, [t["question"] for t in topics.values()], self.negatives],
                                 ensure_ascii=False)
        self.version = "emb-" + hashlib.sha1(anchor_text.encode()).hexdigest()[:10]
        self.legacy_version = self.version
        self.positives = self._positives(starred_entries or [])
        self.negatives_fb = self._feedback_items("down")
        log.info("Embedding-Vorfilter: %d Themen, %d positive Beispiele, %d Abneigungen (+%d 👎)",
                 len(topics), len(self.positives), len(self.negatives), len(self.negatives_fb))

    # --- Anker ------------------------------------------------------------
    def _positives(self, starred_entries: list[dict]) -> list[str]:
        from .miniflux import html_to_text
        out = []
        ex = self.s.config_dir / "examples.txt"
        if ex.exists():
            for line in ex.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    out.append(re.sub(r"^\[([^\]]*)\]\s*", r"\1: ", line))
        out = out[:MAX_EXAMPLES]
        for e in starred_entries[:300]:
            out.append(f"{e.get('title', '')}\n{html_to_text(e.get('content'))[:600]}")
        out += self._feedback_items("positive")
        return [t for t in out if t.strip()]

    def _feedback_items(self, kind: str) -> list[str]:
        """Texte gezeigter Digest-Artikel mit 👍/Stern/Klick ("positive") bzw. 👎 ("down")."""
        rows = read_jsonl(self.s.data_dir / "feedback.jsonl")
        ids = ({eid for eid, k in signals(rows).items() if k.keys() & {"up", "star", "click"}}
               if kind == "positive" else {eid for eid, v in votes(rows).items() if v == "down"})
        if not ids:
            return []
        try:
            digests = json.loads((self.s.data_dir / "digests.json").read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return []
        return [f"{it.get('title', '')}\n{it.get('digest_title', '')}\n{it.get('excerpt', '')}"
                for d in digests for it in d.get("items", []) if str(it.get("entry_id")) in ids]

    def _vectors(self, texts: list[str]):
        rows = [{"id": "anchor:" + hashlib.sha1(t.encode()).hexdigest()[:16], "title": "", "text": t}
                for t in texts]
        self.embedder.embed(rows)
        return self.np.vstack([r["_vec"] for r in rows]) if rows else self.np.zeros((0, 384), "float32")

    # --- Schnittstelle wie LayaFilter -------------------------------------
    def feature_keys(self) -> set[str]:
        return {f"topic_{t}" for t in self.spec["topics"]} | {"like", "dislike"}

    def labels(self) -> dict:
        lab = {f"topic_{k}": "Thema " + v["label"] for k, v in self.spec["topics"].items()}
        lab.update({"like": "Ähnlich zu deinen Favoriten", "dislike": "Ähnlich zu Abgelehntem"})
        return lab

    def combine_features(self, f: dict) -> tuple[float, str]:
        best, best_id = 0.0, ""
        for tid, t in self.spec["topics"].items():
            p = float(f.get(f"topic_{tid}", 0.0)) * float(t.get("weight", 1.0))
            if p > best:
                best, best_id = p, tid
        like, dislike = float(f.get("like", 0.0)), float(f.get("dislike", 0.0))
        value = 10 * (0.5 * like + 0.5 * best) * (1 - 0.6 * dislike)
        label = self.spec["topics"][best_id]["label"] if best_id else "–"
        why = f"Thema: {label} ({best:.2f}), wie deine Favoriten {like:.2f}"
        if dislike >= 0.5:
            why += f", ähnlich zu Abgelehntem {dislike:.2f}"
        return round(value, 2), why

    def score(self, items: list[dict], learner=None, distilled=None) -> None:
        np = self.np
        self.embedder.embed(items)
        X = np.vstack([it["_vec"] for it in items])
        T = self._vectors([t["question"] for t in self.spec["topics"].values()])
        P = self._vectors(self.positives)
        N = self._vectors(self.negatives + self.negatives_fb)
        st = X @ T.T
        sp = np.sort(X @ P.T, axis=1)[:, -POS_TOP:].mean(axis=1) if len(P) else np.zeros(len(items))
        sn = (X @ N.T).max(axis=1) if len(N) else np.zeros(len(items))
        tids = list(self.spec["topics"])
        for i, it in enumerate(items):
            f = {f"topic_{t}": _p(float(st[i, j])) for j, t in enumerate(tids)}
            f["like"] = _p(float(sp[i]))
            f["dislike"] = _p(float(sn[i]), lo=0.35, span=0.35)
            it["laya_features"], it["laya_v"] = f, self.version
            it["prefilter"], it["prefilter_why"] = self.combine_features(f)
            if distilled is not None and distilled.active:
                formula = it["prefilter"]
                it["prefilter"] = distilled.blend(formula, f)
                it["prefilter_why"] += f", aus LLM gelernt {10 * distilled.prob(f):.1f}"
            if learner is not None and learner.active:
                formula = it["prefilter"]
                it["prefilter"] = learner.blend(formula, f)
                it["prefilter_why"] += f", gelernt {learner.prob(f):.2f} (Formel {formula})"
        log.info("Embedding-Vorfilter: %d Artikel bewertet", len(items))
