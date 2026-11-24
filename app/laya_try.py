"""Laya-Modelle auf deinen eigenen Artikeln vergleichen, bevor du umstellst.

  python -m app laya-try MODELL [ANZAHL]      z. B.  laya-try vega 20   oder   laya-try auto 20

Nimmt die zuletzt von Mistral bewerteten Artikel, holt ihren Text aus dem Reader und lässt das
gewählte Laya-Modell sie neu einschätzen. Danach zeigt es:
  - Zeit pro Artikel und den höchsten Speicherbedarf dieses Prozesses,
  - wie gut die Reihenfolge der Vorauswahl zu den Mistral-Noten passt (Rangkorrelation),
  - wie viele der von Mistral am besten bewerteten Artikel bei der Vorauswahl oben landen.
Das ist die Frage, auf die es ankommt: Lässt die Vorauswahl durch, was Mistral gut findet?

Ein Aufruf testet ein Modell. Für einen fairen Vergleich nacheinander mit denselben Artikeln
aufrufen, etwa erst `auto`, dann `vega`. Nichts wird gespeichert, kein Lauf wird verändert.
"""
from __future__ import annotations

import logging
import resource
import time
from datetime import datetime, timezone

from .config import Settings
from .digest import prepare
from .feedback import read_jsonl
from .sources import make_source

log = logging.getLogger(__name__)

MODELS = ("auto", "english", "multilingual", "vega")


def ranks(values: list[float]) -> list[float]:
    """Ränge 1..n, gleiche Werte bekommen den mittleren Rang."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return out


def spearman(a: list[float], b: list[float]) -> float | None:
    if len(a) < 3:
        return None
    ra, rb = ranks(a), ranks(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va = sum((x - ma) ** 2 for x in ra) ** 0.5
    vb = sum((y - mb) ** 2 for y in rb) ** 0.5
    return None if not va or not vb else cov / (va * vb)


def top_overlap(prefilter: list[float], scores: list[float]) -> tuple[int, int]:
    """Wie viele der besten Drittel laut Mistral landen auch bei der Vorauswahl im oberen Drittel?"""
    k = max(1, len(scores) // 3)
    best = set(sorted(range(len(scores)), key=lambda i: -scores[i])[:k])
    top = set(sorted(range(len(prefilter)), key=lambda i: -prefilter[i])[:k])
    return len(best & top), k


def pick_articles(s: Settings, n: int) -> list[dict]:
    latest: dict[str, dict] = {}
    for r in read_jsonl(s.data_dir / "history.jsonl"):
        if r.get("score") is not None:
            latest[str(r.get("entry_id"))] = r
    rows = sorted(latest.values(), key=lambda r: r.get("ts", ""), reverse=True)
    src = make_source(s)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    items = []
    for r in rows[:n * 4]:  # manche gibt es im Reader nicht mehr
        raw = src.entry(str(r["entry_id"]))
        prepared = prepare([raw], s, epoch) if raw else []
        if prepared and prepared[0].get("text"):
            it = prepared[0]
            it["mistral"] = float(r["score"])
            items.append(it)
        if len(items) >= n:
            break
    return items


def run(s: Settings, model: str, n: int = 20) -> str:
    model = (model or "").lower()
    if model not in MODELS:
        return f"Unbekanntes Modell {model!r} – möglich: {', '.join(MODELS)}"
    items = pick_articles(s, n)
    if len(items) < 5:
        return (f"Nur {len(items)} bewertete Artikel mit Text gefunden – für einen Vergleich braucht es mindestens 5. "
                "Nach dem nächsten Lauf noch einmal versuchen.")
    from .laya_filter import LayaFilter
    t0 = time.monotonic()
    flt = LayaFilter(s.config_dir / "laya_questions.json", model=model, max_len=s.laya_max_len,
                     batch_size=s.laya_batch_size)
    load = time.monotonic() - t0
    t1 = time.monotonic()
    for it in items:
        it.pop("laya_features", None)
    flt._run_laya(items)
    per = (time.monotonic() - t1) / len(items)
    peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # Linux: KB
    values = [flt.combine_features(it["laya_features"])[0] for it in items]
    scores = [it["mistral"] for it in items]
    rho = spearman(values, scores)
    hit, k = top_overlap(values, scores)
    lines = [
        f"Laya {flt.laya_version}, Modell {model}, {len(items)} Artikel (zuletzt von Mistral bewertet)",
        f"  Laden:     {load:.0f} s",
        f"  Tempo:     {per:.1f} s pro Artikel",
        f"  Speicher:  höchstens {peak_mb / 1024:.1f} GB in diesem Prozess",
        "  Passt zu Mistral: Rangkorrelation "
        + ("–" if rho is None else f"{rho:+.2f}") + "  (1 = gleiche Reihenfolge, 0 = kein Zusammenhang)",
        f"  Von den {k} besten laut Mistral landen {hit} auch bei der Vorauswahl im oberen Drittel",
        "",
        "  Note  Vorauswahl  Titel",
    ]
    for it, v in sorted(zip(items, values), key=lambda p: -p[0]["mistral"]):
        lines.append(f"  {it['mistral']:>4.0f}  {v:>10.1f}  {it['title'][:70]}")
    lines += ["", "Zum Vergleich dasselbe mit einem anderen Modell aufrufen, z. B. "
              f"`python -m app laya-try {'auto' if model == 'vega' else 'vega'} {n}`."]
    return "\n".join(lines)
