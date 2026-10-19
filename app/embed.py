"""Optionale lokale Satz-Embeddings (fastembed/ONNX, kein PyTorch).

Damit werden gleiche Geschichten aus verschiedenen Quellen erkannt, der Digest nach Themen
gruppiert und Artikel mit deinen Stern-Artikeln verglichen. Alles läuft lokal, nichts geht raus.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

log = logging.getLogger(__name__)

EMBED_WORDS = 300  # Titel + Textanfang; mehr bringt bei MiniLM kaum etwas


def _text(it: dict) -> str:
    return " ".join(f"{it.get('title') or ''}\n{it.get('text') or ''}".split()[:EMBED_WORDS])


class Embedder:
    """Rechnet Vektoren für Artikel aus und merkt sie sich in data/ – bei SELECT_FROM=unread
    tauchen jeden Tag großteils dieselben Artikel auf."""

    def __init__(self, model: str, data_dir: Path):
        import numpy as np
        from fastembed import TextEmbedding  # noqa: F401 – früh scheitern, wenn nicht installiert
        self.np = np
        self.model_name = model
        self.model_dir = data_dir / "fastembed"
        self.cache_path = data_dir / f"embeddings-{hashlib.sha1(model.encode()).hexdigest()[:10]}.npz"
        self._model = None
        self._cache = self._load()
        self._used: set[str] = set()

    def __str__(self) -> str:
        return self.model_name.rsplit("/", 1)[-1]

    def _load(self) -> dict:
        try:
            with self.np.load(self.cache_path) as z:
                return dict(zip(z["ids"].tolist(), z["vecs"]))
        except (FileNotFoundError, OSError, KeyError, ValueError):
            return {}

    def save(self) -> None:
        """Nur Vektoren behalten, die in diesem Lauf gebraucht wurden – sonst wächst die Datei endlos."""
        keep = {k: v for k, v in self._cache.items() if k in self._used}
        if not keep:
            return
        tmp = self.cache_path.with_suffix(".tmp")
        with open(tmp, "wb") as f:
            self.np.savez(f, ids=self.np.array(list(keep)), vecs=self.np.vstack(list(keep.values())))
        tmp.replace(self.cache_path)

    def embed(self, items: list[dict]) -> None:
        """Setzt it["_vec"] (Länge 1) für jeden Artikel; nur Unbekanntes wird neu berechnet."""
        todo = [it for it in items if str(it["id"]) not in self._cache]
        if todo:
            if self._model is None:
                from fastembed import TextEmbedding
                self._model = TextEmbedding(model_name=self.model_name, cache_dir=str(self.model_dir))
            vecs = self.np.array(list(self._model.embed([_text(it) for it in todo])), dtype=self.np.float32)
            vecs /= self.np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9
            for it, v in zip(todo, vecs):
                self._cache[str(it["id"])] = v
            log.info("%d Artikel eingebettet (%d aus dem Zwischenspeicher)", len(todo), len(items) - len(todo))
        for it in items:
            self._used.add(str(it["id"]))
            it["_vec"] = self._cache[str(it["id"])]


def similarity(a: dict, b: dict) -> float:
    """Kosinus-Ähnlichkeit zweier eingebetteter Artikel, 0 wenn einer keinen Vektor hat."""
    va, vb = a.get("_vec"), b.get("_vec")
    return float(va @ vb) if va is not None and vb is not None else 0.0


def cluster(items: list[dict], threshold: float) -> list[list[dict]]:
    """Fasst ähnliche Artikel zusammen (Average Linkage): zwei Gruppen werden vereint, solange ihre
    mittlere Ähnlichkeit über threshold liegt. Bei ~10 Artikeln reicht der einfache Weg."""
    groups = [[it] for it in items]
    while True:
        best = None
        for a in range(len(groups)):
            for b in range(a + 1, len(groups)):
                sims = [similarity(x, y) for x in groups[a] for y in groups[b]]
                s = sum(sims) / len(sims)
                if s >= threshold and (best is None or s > best[0]):
                    best = (s, a, b)
        if best is None:
            return groups
        _, a, b = best
        groups[a] += groups.pop(b)


def nearest(item: dict, others: list[dict]) -> tuple[float, dict | None]:
    """Ähnlichster Artikel aus others (z. B. deinen Sternen), außer dem Artikel selbst."""
    best: tuple[float, dict | None] = (0.0, None)
    for o in others:
        if str(o["id"]) == str(item["id"]):
            continue
        s = similarity(item, o)
        if s > best[0]:
            best = (s, o)
    return best
