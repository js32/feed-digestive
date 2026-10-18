"""Lokaler Vorfilter mit Laya (https://laya.convaiinnovations.com/).

Laya ist ein kleines Entscheidungsmodell (BERT-Encoder, ~320–420 Mio. Parameter), das
festgelegte Fragen (Ja/Nein, Skala, Auswahl) in einem Durchgang mit Wahrscheinlichkeiten
beantwortet. Es erzeugt keinen Text und läuft auf CPU – kein Artikel verlässt den Server.

Die Fragen stehen in config/laya_questions.json. Aus den Antworten wird ein Wert 0–10
berechnet:

    relevanz = max(gewicht_i * P(thema_i))          # passt der Artikel zu einem Interesse?
    tiefe    = Skalenwert / (Stufen - 1)            # 0 = Meldung … 1 = herausragend
    malus    = max(P(ausschluss_j))                 # PR, Clickbait, Promi …
    wert     = 10 * relevanz * (0.4 + 0.6 * tiefe) * (1 - 0.8 * malus)
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

STATE_CHARS = 2500  # ~600–800 Token, passt in den 1024er-Kontext


class LayaFilter:
    def __init__(self, questions_file: Path, model: str = "multilingual",
                 device: str | None = None, max_len: int = 1024, batch_size: int = 8):
        try:
            from laya import Router  # schwere Abhängigkeit (torch) – nur bei Bedarf laden
        except ImportError as e:
            raise SystemExit(
                "PREFILTER=laya, aber das Paket 'laya' ist nicht installiert. "
                "Image mit  docker compose build --build-arg WITH_LAYA=1  bauen."
            ) from e
        self.spec = load_questions(questions_file)
        self.model = model
        self.max_len = max_len
        self.batch_size = batch_size
        self.router = Router(device=device or os.environ.get("LAYA_DEVICE") or None, max_loaded=1)
        self.questions = self._build_questions()

    # ------------------------------------------------------------------
    def _build_questions(self) -> dict:
        q = {}
        for tid, t in self.spec["topics"].items():
            q[f"topic_{tid}"] = {"type": "noul", "instructions": t["question"]}
        d = self.spec["depth"]
        q["depth"] = {"type": "score", "instructions": d["question"], "criteria": d["levels"]}
        for pid, p in self.spec["penalties"].items():
            q[f"penalty_{pid}"] = {"type": "noul", "instructions": p["question"]}
        return q

    def _state(self, it: dict) -> str:
        return (f"Source: {it['feed']}\nTitle: {it['title']}\n\n"
                f"{it['text'][:STATE_CHARS]}")

    def features(self, answers: dict) -> dict:
        """Laya-Antworten als Zahlen 0–1 – Grundlage fürs Lernen."""
        f = {}
        for tid in self.spec["topics"]:
            f[f"topic_{tid}"] = round(float(answers.get(f"topic_{tid}", {}).get("noul", 0.0)), 4)
        levels = len(self.spec["depth"]["levels"])
        f["depth"] = round(float(answers.get("depth", {}).get("score", 0.0)) / max(1, levels - 1), 4)
        for pid in self.spec["penalties"]:
            f[f"penalty_{pid}"] = round(float(answers.get(f"penalty_{pid}", {}).get("noul", 0.0)), 4)
        return f

    def labels(self) -> dict:
        lab = {f"topic_{k}": "Thema " + v.get("label", k) for k, v in self.spec["topics"].items()}
        lab["depth"] = "Tiefe"
        lab.update({f"penalty_{k}": "Malus " + v.get("label", k) for k, v in self.spec["penalties"].items()})
        return lab

    def combine(self, answers: dict) -> tuple[float, str]:
        topics = self.spec["topics"]
        rel_best, rel_id = 0.0, ""
        for tid, t in topics.items():
            p = float(answers.get(f"topic_{tid}", {}).get("noul", 0.0)) * float(t.get("weight", 1.0))
            if p > rel_best:
                rel_best, rel_id = p, tid
        levels = len(self.spec["depth"]["levels"])
        depth = float(answers.get("depth", {}).get("score", 0.0)) / max(1, levels - 1)
        malus, mal_id = 0.0, ""
        for pid in self.spec["penalties"]:
            p = float(answers.get(f"penalty_{pid}", {}).get("noul", 0.0))
            if p > malus:
                malus, mal_id = p, pid
        value = 10 * rel_best * (0.4 + 0.6 * depth) * (1 - 0.8 * malus)
        why = f"Thema: {topics[rel_id].get('label', rel_id) if rel_id else '–'} ({rel_best:.2f}), Tiefe {depth:.2f}"
        if malus >= 0.5:
            why += f", Malus: {self.spec['penalties'][mal_id].get('label', mal_id)}"
        return round(value, 2), why

    def score(self, items: list[dict], learner=None) -> None:
        """Setzt it['prefilter'] (0–10), it['prefilter_why'] und it['laya_features']."""
        requests = [{"state": self._state(it), "questions": self.questions,
                     "model": self.model, "max_len": self.max_len} for it in items]
        results = self.router.predict_batch(requests, batch_size=self.batch_size, sort_by_length=True)
        for it, res in zip(items, results):
            answers = res.get("answers", {})
            it["laya_features"] = self.features(answers)
            it["prefilter"], it["prefilter_why"] = self.combine(answers)
            if learner is not None and learner.active:
                formula = it["prefilter"]
                it["prefilter"] = learner.blend(formula, it["laya_features"])
                it["prefilter_why"] += f", gelernt {learner.prob(it['laya_features']):.2f} (Formel {formula})"


def load_questions(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"{path} fehlt – Vorlage aus config.example/ kopieren")
    spec = json.loads(path.read_text(encoding="utf-8"))
    for key in ("topics", "depth", "penalties"):
        if key not in spec:
            raise SystemExit(f"{path}: Abschnitt '{key}' fehlt")
    if len(spec["depth"].get("levels", [])) < 2:
        raise SystemExit(f"{path}: depth.levels braucht mindestens 2 Stufen")
    return spec


def apply_prefilter(items: list[dict], flt: LayaFilter, keep: int, min_value: float,
                    learner=None) -> list[dict]:
    flt.score(items, learner)
    ranked = sorted(items, key=lambda it: -it["prefilter"])
    kept = [it for it in ranked if it["prefilter"] >= min_value][:keep]
    log.info("Laya-Vorfilter: %d von %d Artikeln behalten (bester Wert %.1f, Schwelle %.1f)",
             len(kept), len(items), ranked[0]["prefilter"] if ranked else 0, min_value)
    return kept
