"""Auswertung der Laya-Fragen: welche Frage trägt etwas bei, welche nicht?

  python -m app laya-report

Grundlage ist data/history.jsonl (Laya-Antworten je Artikel, LLM-Bewertung, gezeigt?)
und data/feedback.jsonl (Klicks und Sterne). Es wird nichts verändert und nichts versendet.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from .feedback import read_jsonl

HIT = 0.5        # ab dieser Wahrscheinlichkeit gilt eine Ja/Nein-Frage als "trifft zu"
GOOD_SCORE = 7   # ab dieser LLM-Bewertung gilt ein Artikel als "gut"


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 5:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def _fmt(v: float | None, digits: int = 2) -> str:
    return "  –  " if v is None else f"{v:+.{digits}f}"


def _labels(config_dir: Path) -> tuple[dict, dict]:
    spec = {}
    qfile = config_dir / "laya_questions.json"
    if qfile.exists():
        spec = json.loads(qfile.read_text(encoding="utf-8"))
    lab = {f"topic_{k}": "Thema " + v.get("label", k) for k, v in spec.get("topics", {}).items()}
    lab["depth"] = "Tiefe"
    lab.update({f"penalty_{k}": "Malus " + v.get("label", k) for k, v in spec.get("penalties", {}).items()})
    return lab, spec


def report(data_dir: Path, config_dir: Path) -> str:
    labels, spec = _labels(config_dir)
    # pro Artikel der jüngste Eintrag mit Laya-Merkmalen; LLM-Wert notfalls aus älterem Eintrag
    latest: dict[str, dict] = {}
    for r in read_jsonl(data_dir / "history.jsonl"):
        eid = str(r.get("entry_id"))
        prev = latest.get(eid, {})
        row = {**prev, **{k: v for k, v in r.items() if v is not None}}
        latest[eid] = row
    rows = [r for r in latest.values() if r.get("features")]
    if not rows:
        return "Noch keine Laya-Daten in data/history.jsonl – erst einen Lauf mit PREFILTER=laya machen."

    current = set(labels) or {k for r in rows for k in r["features"]}
    stale = sum(1 for r in rows if not current <= set(r["features"]))
    rows = [r for r in rows if current <= set(r["features"])]
    # nur deutliche Signale (Klick, Stern, 👍) – "angelesen" ist zu schwach für diesen Vergleich
    from .feedback import signals
    positive = {eid for eid, k in signals(read_jsonl(data_dir / "feedback.jsonl")).items()
                if k.keys() & {"click", "star", "up"}}
    for r in rows:
        r["_pos"] = str(r["entry_id"]) in positive
    scored = [r for r in rows if isinstance(r.get("score"), (int, float))]
    pos = [r for r in rows if r["_pos"]]
    keys = [k for k in labels if k in current] or sorted(current)

    out = []
    out.append("Laya-Auswertung")
    out.append("=" * 60)
    out.append(f"Artikel mit Laya-Antworten: {len(rows)}"
               + (f"  (+{stale} mit altem Fragenkatalog, ignoriert)" if stale else ""))
    out.append(f"davon vom LLM bewertet:     {len(scored)}")
    out.append(f"davon angeklickt/Stern:     {len(pos)}")
    if len(scored) < 30:
        out.append("\nHinweis: Unter 30 LLM-Bewertungen sind die Zusammenhänge noch wenig aussagekräftig.")

    # --- Gesamt: passt Laya zum LLM? -----------------------------------
    pv = [(float(r["prefilter"]), float(r["score"])) for r in scored if r.get("prefilter") is not None]
    if pv:
        r_all = _pearson([a for a, _ in pv], [b for _, b in pv])
        out.append(f"\nÜbereinstimmung Laya-Wert ↔ LLM-Bewertung: r = {_fmt(r_all)}"
                   "  (0 = kein Zusammenhang, 1 = perfekt)")
        out.append("Achtung: Das LLM sieht nur, was Laya durchgelassen hat – der Wert ist eher zu niedrig.")

    # --- pro Frage --------------------------------------------------------
    out.append("\nPro Frage")
    out.append("-" * 60)
    out.append("trifft = Anteil Artikel mit Ja-Wahrscheinlichkeit ≥ 0,5 (bei Tiefe: Mittelwert)")
    out.append("r(LLM) = Zusammenhang mit der LLM-Bewertung (+ gut, − sollte bei Malus negativ sein)")
    out.append("Ø LLM  = mittlere LLM-Bewertung der Artikel, bei denen die Frage zutrifft")
    out.append("Klick× = wie viel häufiger angeklickte/gesternte Artikel die Frage erfüllen (1 = egal)\n")
    out.append(f"{'Frage':<34}{'trifft':>8}{'r(LLM)':>9}{'Ø LLM':>8}{'Klick×':>8}  Hinweis")
    base_hit = {}
    for k in keys:
        vals = [float(r["features"].get(k, 0.0)) for r in rows]
        is_depth = k == "depth"
        hit = sum(vals) / len(vals) if is_depth else sum(v >= HIT for v in vals) / len(vals)
        base_hit[k] = hit
        r_llm = _pearson([float(r["features"].get(k, 0.0)) for r in scored], [float(r["score"]) for r in scored])
        hits_scored = [float(r["score"]) for r in scored if float(r["features"].get(k, 0.0)) >= HIT]
        avg_llm = sum(hits_scored) / len(hits_scored) if hits_scored else None
        lift = None
        if pos and not is_depth and hit > 0:
            pos_hit = sum(float(r["features"].get(k, 0.0)) >= HIT for r in pos) / len(pos)
            lift = pos_hit / hit
        notes = []
        if not is_depth and hit < 0.02:
            notes.append("schlägt fast nie an")
        if not is_depth and hit > 0.5:
            notes.append("schlägt zu oft an – zu breit?")
        if r_llm is not None:
            if k.startswith("penalty_") and r_llm > 0.05:
                notes.append("Malus trifft eher gute Artikel!")
            elif not k.startswith("penalty_") and abs(r_llm) < 0.08 and 0.02 <= hit <= 0.5:
                notes.append("kaum Zusammenhang mit LLM")
            elif k.startswith("topic_") and r_llm < -0.05:
                notes.append("Thema eher bei schlechten Artikeln")
        name = labels.get(k, k)[:33]
        out.append(f"{name:<34}{hit:>7.0%} {_fmt(r_llm):>8}"
                   f"{(f'{avg_llm:.1f}' if avg_llm is not None else '–'):>8}"
                   f"{(f'{lift:.1f}' if lift is not None else '–'):>8}  {', '.join(notes)}")

    # --- Überschneidungen -------------------------------------------------
    topics = [k for k in keys if k.startswith("topic_")]
    pairs = []
    for i, a in enumerate(topics):
        for b in topics[i + 1:]:
            r_ab = _pearson([float(r["features"].get(a, 0.0)) for r in rows],
                            [float(r["features"].get(b, 0.0)) for r in rows])
            if r_ab is not None and r_ab > 0.5:
                pairs.append((r_ab, a, b))
    out.append("\nThemen, die oft gemeinsam anschlagen (r > 0,5 – evtl. zusammenlegen oder schärfer trennen)")
    out.append("-" * 60)
    if pairs:
        for r_ab, a, b in sorted(pairs, reverse=True):
            out.append(f"  {labels.get(a, a)} ↔ {labels.get(b, b)}: r = {r_ab:.2f}")
    else:
        out.append("  keine")

    # --- Widersprüche -----------------------------------------------------
    both = [r for r in scored if r.get("prefilter") is not None]
    if both:
        def best_topic(r):
            t = max(topics, key=lambda k: float(r["features"].get(k, 0.0)), default=None)
            return f"{labels.get(t, t).removeprefix('Thema ')} {float(r['features'].get(t, 0)):.2f}" if t else ""
        under = sorted(both, key=lambda r: float(r["prefilter"]) - float(r["score"]))[:8]
        over = sorted(both, key=lambda r: float(r["score"]) - float(r["prefilter"]))[:8]
        out.append("\nLaya unterschätzt (LLM fand gut, Laya nicht) – welche Frage fehlt hier?")
        out.append("-" * 60)
        for r in under:
            if float(r["score"]) - float(r["prefilter"]) < 2:
                break
            out.append(f"  LLM {r['score']:>2} / Laya {float(r['prefilter']):4.1f}  [{best_topic(r)}]  {r['title'][:70]}")
        out.append("\nLaya überschätzt (Laya fand gut, LLM nicht) – welche Frage ist zu breit?")
        out.append("-" * 60)
        for r in over:
            if float(r["prefilter"]) - float(r["score"]) < 2:
                break
            out.append(f"  LLM {r['score']:>2} / Laya {float(r['prefilter']):4.1f}  [{best_topic(r)}]  {r['title'][:70]}")

    # --- Angeklickt, aber von Laya abgelehnt -----------------------------
    missed = [r for r in pos if r.get("prefilter") is not None and float(r["prefilter"]) < 3]
    if missed:
        out.append("\nAngeklickt/gesternt, aber von Laya niedrig bewertet")
        out.append("-" * 60)
        for r in missed[:10]:
            out.append(f"  Laya {float(r['prefilter']):4.1f}  [{best_topic(r) if both else ''}]  {r['title'][:70]}")

    out.append("\nTitel oben bleiben auf deinem Server – zum Teilen ggf. kürzen.")
    return "\n".join(out)
