"""Aus Klicks und Sternen lernen, wie stark welche Laya-Merkmale bei dir zählen.

Merkmale = die Laya-Antworten pro Artikel (Thema KI ja/nein, Tiefe, PR …).
Ein kleines logistisches Regressionsmodell sagt daraus die Wahrscheinlichkeit voraus,
dass du einen Artikel anklickst oder markierst.

Labels:
  1    Interesse gezeigt – gewichtet nach dem stärksten Signal: im Reader angelesen 0,3,
       „Zum Artikel“ geklickt 1,0, Stern 1,5, 👍 2,0 (feedback.SIGNAL_WEIGHTS)
  0    👎 „weniger davon“ – Gewicht 2,0, schlägt alle anderen Signale
  0    im Digest gezeigt, aber nach FEEDBACK_DAYS weder geklickt noch markiert
  0    nie gezeigt und nie markiert – nur mit kleinem Gewicht (unbekannt ≠ uninteressant)

Der gelernte Wert wird mit der Formel aus laya_filter gemischt; der Anteil wächst mit der
Zahl der positiven Rückmeldungen (gewichtet, max. 80 %).
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .feedback import DOWN_WEIGHT, read_jsonl, signals, votes

log = logging.getLogger(__name__)

MIN_POS, MIN_NEG = 5, 10
UNSHOWN_WEIGHT = 0.15
L2 = 0.05
EPOCHS = 400
LR = 0.5


def current_history(data_dir: Path) -> list[dict]:
    """Protokollzeilen mit Laya-Merkmalen zum aktuellen Fragenkatalog (dem des jüngsten Eintrags).
    Nach geänderten Fragen passen alte Antworten nicht mehr – damit zu lernen hieße, aus Lücken zu lernen."""
    history = [r for r in read_jsonl(data_dir / "history.jsonl") if r.get("features")]
    if not history:
        return []
    try:
        legacy = (data_dir / "laya_legacy_version.txt").read_text(encoding="utf-8").strip() or None
    except FileNotFoundError:
        legacy = None
    version = lambda r: r.get("laya_v") or legacy  # noqa: E731
    keys, ver = set(history[-1]["features"]), version(history[-1])
    return [r for r in history if set(r["features"]) == keys and version(r) == ver]


def build_dataset(data_dir: Path, feedback_days: int) -> tuple[list[dict], list[int], list[float], dict]:
    history = current_history(data_dir)
    fb = read_jsonl(data_dir / "feedback.jsonl")
    sig, vote = signals(fb), votes(fb)
    settle = (datetime.now(timezone.utc) - timedelta(days=feedback_days)).isoformat()

    # pro Artikel nur der jüngste Protokolleintrag
    latest: dict[int, dict] = {}
    for r in history:
        latest[str(r["entry_id"])] = r
    X, y, w = [], [], []
    stats = {"pos": 0, "pos_weight": 0.0, "read": 0, "click": 0, "star": 0, "up": 0, "down": 0,
             "neg_shown": 0, "neg_unshown": 0}
    for eid, r in latest.items():
        if vote.get(eid) == "down":
            X.append(r["features"]); y.append(0); w.append(DOWN_WEIGHT); stats["down"] += 1
        elif eid in sig:
            kinds = sig[eid]
            weight = max(kinds.values())
            X.append(r["features"]); y.append(1); w.append(weight)
            stats["pos"] += 1
            stats["pos_weight"] = round(stats["pos_weight"] + weight, 2)
            stats[max(kinds, key=kinds.get)] += 1  # nach dem stärksten Signal zählen
        elif r.get("ts", "") > settle:
            continue  # noch zu frisch, vielleicht klickst du ja noch
        elif r.get("shown"):
            X.append(r["features"]); y.append(0); w.append(1.0); stats["neg_shown"] += 1
        else:
            X.append(r["features"]); y.append(0); w.append(UNSHOWN_WEIGHT); stats["neg_unshown"] += 1
    return X, y, w, stats


def _sigmoid(z: float) -> float:
    return 1 / (1 + math.exp(-max(-30.0, min(30.0, z))))


def train(X: list[dict], y: list[int], w: list[float]) -> dict:
    names = sorted({k for x in X for k in x})
    rows = [[float(x.get(n, 0.0)) for n in names] for x in X]
    coef = [0.0] * len(names)
    bias = 0.0
    total_w = sum(w)
    for _ in range(EPOCHS):
        g = [0.0] * len(names)
        gb = 0.0
        for row, label, wt in zip(rows, y, w):
            err = (_sigmoid(bias + sum(c * v for c, v in zip(coef, row))) - label) * wt
            gb += err
            for j, v in enumerate(row):
                g[j] += err * v
        bias -= LR * gb / total_w
        coef = [c - LR * (gj / total_w + L2 * c) for c, gj in zip(coef, g)]
    return {"features": names, "coef": coef, "bias": bias}


class Learner:
    def __init__(self, model: dict | None, n_pos: int = 0):
        self.model = model
        self.alpha = min(0.8, n_pos / 40) if model else 0.0

    @property
    def active(self) -> bool:
        return self.model is not None and self.alpha > 0

    def prob(self, features: dict) -> float:
        m = self.model
        z = m["bias"] + sum(c * float(features.get(n, 0.0)) for n, c in zip(m["features"], m["coef"]))
        return _sigmoid(z)

    def blend(self, formula_value: float, features: dict) -> float:
        if not self.active:
            return formula_value
        return round((1 - self.alpha) * formula_value + self.alpha * 10 * self.prob(features), 2)


def retrain(data_dir: Path, feedback_days: int) -> Learner:
    X, y, w, stats = build_dataset(data_dir, feedback_days)
    neg = stats["neg_shown"] + stats["neg_unshown"] + stats["down"]
    path = data_dir / "learned_weights.json"
    if stats["pos_weight"] < MIN_POS or neg < MIN_NEG:
        log.info("Lernen: noch zu wenig Rückmeldung (%.1f positiv gewichtet, %d negativ; nötig %d/%d) – nur Formel",
                 stats["pos_weight"], neg, MIN_POS, MIN_NEG)
        return Learner(None)
    model = train(X, y, w)
    model.update({"trained_at": datetime.now(timezone.utc).isoformat(), "stats": stats})
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(model, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    learner = Learner(model, stats["pos_weight"])
    log.info("Lernen: Modell aus %d positiven (%d 👍, %d Stern, %d geklickt, %d angelesen) / %d negativen "
             "(%d 👎) Beispielen, Anteil am Wert %.0f %%", stats["pos"], stats["up"], stats["star"], stats["click"],
             stats["read"], neg, stats["down"], learner.alpha * 100)
    return learner


def describe(data_dir: Path, feedback_days: int, labels: dict[str, str]) -> str:
    """Lesbare Übersicht für `python -m app learn`."""
    learner = retrain(data_dir, feedback_days)
    X, y, w, stats = build_dataset(data_dir, feedback_days)
    lines = [f"Rückmeldungen: {stats['pos']} positiv ({stats['up']} 👍, {stats['star']} Stern, "
             f"{stats['click']} geklickt, {stats['read']} nur angelesen; gewichtet {stats['pos_weight']:.1f}), "
             f"{stats['down']} 👎, "
             f"{stats['neg_shown']} gezeigt ohne Klick, {stats['neg_unshown']} nie gezeigt"]
    if not learner.active:
        lines.append(f"Noch kein Modell – nötig sind mindestens {MIN_POS} positive und {MIN_NEG} negative Beispiele.")
        return "\n".join(lines)
    m = learner.model
    lines.append(f"Anteil des gelernten Modells am Laya-Wert: {learner.alpha * 100:.0f} %")
    lines.append("Gelernte Gewichte (positiv = mehr davon, negativ = weniger):")
    for name, c in sorted(zip(m["features"], m["coef"]), key=lambda t: -t[1]):
        bar = ("+" if c > 0 else "−") * min(20, int(abs(c) * 4) + 1)
        lines.append(f"  {labels.get(name, name):28s} {c:+.2f}  {bar}")
    return "\n".join(lines)



# ----------------------------------------------------------------------
# Aus den LLM-Bewertungen lernen (LEARN_FROM_LLM)
# ----------------------------------------------------------------------
MIN_SCORED = 30
SCORED_FULL = 200   # ab so vielen bewerteten Artikeln zählt das Gelernte zu 90 %


class ScoreModel(Learner):
    """Sagt aus Layas Antworten die LLM-Bewertung (0–10) voraus. Ersetzt nach und nach die feste Formel
    mit ihren Handgewichten: unscharfe Fragen bekommen von selbst wenig Gewicht, verlässliche viel."""

    def __init__(self, model: dict | None, n: int = 0):
        super().__init__(model)
        self.alpha = min(0.9, n / SCORED_FULL) if model else 0.0


def retrain_from_llm(data_dir: Path) -> ScoreModel:
    latest: dict[str, dict] = {}
    for r in current_history(data_dir):
        prev = latest.get(str(r["entry_id"]), {})
        latest[str(r["entry_id"])] = {**prev, **{k: v for k, v in r.items() if v is not None}}
    rows = [r for r in latest.values() if isinstance(r.get("score"), (int, float))]
    if len(rows) < MIN_SCORED:
        log.info("Aus LLM lernen: erst %d bewertete Artikel mit aktuellen Laya-Fragen (nötig %d) – nur Formel",
                 len(rows), MIN_SCORED)
        return ScoreModel(None)
    X = [r["features"] for r in rows]
    y = [max(0.0, min(1.0, float(r["score"]) / 10)) for r in rows]  # weiche Labels 0–1
    model = train(X, y, [1.0] * len(rows))
    model.update({"trained_at": datetime.now(timezone.utc).isoformat(), "n": len(rows)})
    path = data_dir / "laya_from_llm.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(model, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    m = ScoreModel(model, len(rows))
    log.info("Aus LLM lernen: Modell aus %d bewerteten Artikeln, Anteil am Laya-Wert %.0f %%",
             len(rows), m.alpha * 100)
    return m


def describe_llm(data_dir: Path, labels: dict[str, str]) -> str:
    m = retrain_from_llm(data_dir)
    if not m.active:
        return (f"Aus LLM-Bewertungen: noch kein Modell – nötig sind {MIN_SCORED} vom LLM bewertete Artikel "
                "mit dem aktuellen Laya-Fragenkatalog.")
    lines = [f"Aus LLM-Bewertungen gelernt ({m.model['n']} Artikel), Anteil am Laya-Wert {m.alpha * 100:.0f} %:",
             "Gewichte (positiv = spricht für eine gute LLM-Bewertung):"]
    for name, c in sorted(zip(m.model["features"], m.model["coef"]), key=lambda t: -t[1]):
        bar = ("+" if c > 0 else "−") * min(20, int(abs(c) * 4) + 1)
        lines.append(f"  {labels.get(name, name):28s} {c:+.2f}  {bar}")
    return "\n".join(lines)
