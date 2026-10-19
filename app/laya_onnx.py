"""Laya über ONNX Runtime statt PyTorch (LAYA_BACKEND=onnx).

Gleiches Modell, gleiche Gewichte (32 Bit), nur eine schnellere Ausführung auf der CPU.
Die Ergebnisse stimmen bis auf Rundungsunterschiede mit PyTorch überein.

  python -m app laya-export [--check N]

lädt das offizielle Laya-Modell (PyTorch), schreibt es als ONNX-Datei nach
DATA_DIR/laya-onnx/<modell>/laya.onnx, prüft die Datei und vergleicht danach an N echten
ungelesenen Artikeln mit deinen Fragen, ob ONNX dieselben Antworten gibt – samt Zeitmessung.
"""
from __future__ import annotations

import gc
import logging
import os
import time
from pathlib import Path

log = logging.getLogger(__name__)

MODELS_FOR = {"auto": ["english", "multilingual"], "english": ["english"], "multilingual": ["multilingual"]}


def onnx_dir(data_dir: Path) -> Path:
    return Path(os.environ.get("LAYA_ONNX_DIR") or (data_dir / "laya-onnx"))


def onnx_file(data_dir: Path, model: str) -> Path:
    return onnx_dir(data_dir) / model / "laya.onnx"


def _repo(model: str) -> tuple[str, str | None]:
    """Hub-Repo und Unterordner eines Laya-Modells, genau wie der Router sie auflöst."""
    from laya.router import DEFAULT_MODELS, _split
    key = (model or "").lower()
    if key not in DEFAULT_MODELS:
        raise SystemExit(f"Unbekanntes Laya-Modell {model!r} – erlaubt: {', '.join(sorted(DEFAULT_MODELS))}")
    return tuple(_split(DEFAULT_MODELS[key]))


def make_router(data_dir: Path, **kwargs):
    """Laya-Router, der statt PyTorch-Agents ONNX-Agents baut (Sprachweiche bleibt gleich)."""
    from laya import Router
    from laya.onnx_agent import ONNXAgent
    from laya.router import _split

    base = onnx_dir(data_dir)

    class OnnxRouter(Router):
        def _build(self, key: str):  # noqa: D401 – überschreibt Router._build aus laya 0.4.11
            with self._lock:
                repo, sub = _split(self.models[key])
            path = base / key / "laya.onnx"
            if not path.exists():
                raise SystemExit(f"LAYA_BACKEND=onnx, aber {path} fehlt – einmalig "
                                 "'python -m app laya-export' ausführen")
            log.info("Laya (ONNX): lade %s", path)
            return ONNXAgent(repo, onnx_path=str(path), subfolder=sub)

    return OnnxRouter(**kwargs)


# ----------------------------------------------------------------------
def export(s, check: int = 5) -> None:
    from laya.agent import Agent
    from .vendor import laya_export_onnx as ex

    models = MODELS_FOR.get((s.laya_model or "auto").lower(), [s.laya_model])
    samples = _samples(s, check) if check > 0 else []
    for model in models:
        repo, sub = _repo(model)
        out = onnx_file(s.data_dir, model)
        print(f"\n== {model} ({repo}{'/' + sub if sub else ''}) ==")
        print("Lade PyTorch-Modell …")
        agent = Agent(repo, subfolder=sub, compile=False, device="cpu")
        if out.exists():
            print(f"{out} gibt es schon – wird neu geschrieben")
        tmp = out.with_suffix(".tmp.onnx")
        print(f"Exportiere nach {out} (dauert ein paar Minuten, braucht viel RAM) …")
        ex.export_module(agent.model, str(tmp))
        print("Prüfe die Datei gegen PyTorch (Batch 1, 2, 3) …")
        ex.verify_batch_dynamic(agent.model, str(tmp))
        tmp.replace(out)
        print(f"✓ geschrieben: {out} ({out.stat().st_size / 1e9:.2f} GB)")

        cmp = _Compare(s, samples) if samples else None
        if cmp:
            print(f"Vergleich an {len(samples)} Artikeln – PyTorch …")
            cmp.torch = cmp.run(agent)
        del agent  # Speicher freigeben, bevor ONNX geladen wird
        gc.collect()
        if cmp:
            from laya.onnx_agent import ONNXAgent
            print("… ONNX …")
            cmp.onnx = cmp.run(ONNXAgent(repo, onnx_path=str(out), subfolder=sub))
            cmp.report()
    print("\nFertig. Einschalten mit  LAYA_BACKEND=onnx  in der .env, dann  docker compose up -d")


def _samples(s, n: int) -> list[dict]:
    from datetime import datetime, timedelta, timezone
    from .digest import prepare
    from .sources import make_source
    print(f"Hole {n} ungelesene Artikel für den Vergleich …")
    raw = make_source(s).fetch_unread(max(n * 3, n))
    items = prepare(raw, s, datetime.now(timezone.utc) - timedelta(days=3650))
    return [it for it in items if len(it.get("text") or "") > 300][:n] or items[:n]


class _Compare:
    """Gleiche Artikel, gleiche Fragen: PyTorch gegen ONNX."""

    def __init__(self, s, samples):
        from .laya_filter import STATE_CHARS, LayaFilter, load_questions
        self.s, self.samples = s, samples
        self.flt = LayaFilter.__new__(LayaFilter)  # nur Fragen und Formel, ohne eigenen Router
        self.flt.spec = load_questions(s.config_dir / "laya_questions.json")
        self.questions = self.flt._build_questions()
        self.states = [f"Source: {it['feed']}\nTitle: {it['title']}\n\n{it['text'][:STATE_CHARS]}"
                       for it in samples]

    def run(self, agent):
        kw = {"batch_size": self.s.laya_batch_size, "max_len": self.s.laya_max_len, "sort_by_length": True}
        agent.predict_batch(self.states[:1], self.questions, **kw)  # Aufwärmen, nicht mitgemessen
        t = time.monotonic()
        res = agent.predict_batch(self.states, self.questions, **kw)
        secs = (time.monotonic() - t) / len(self.states)
        return [self.flt.features(r.get("answers", {})) for r in res], secs

    def report(self):
        (f_torch, t_torch), (f_onnx, t_onnx) = self.torch, self.onnx
        worst_feat = worst_val = 0.0
        for it, a, b in zip(self.samples, f_torch, f_onnx):
            d = max(abs(a[k] - b.get(k, 0.0)) for k in a)
            va, vb = self.flt.combine_features(a)[0], self.flt.combine_features(b)[0]
            worst_feat, worst_val = max(worst_feat, d), max(worst_val, abs(va - vb))
            print(f"  {va:5.2f} / {vb:5.2f}  (max Δ Antwort {d:.4f})  {it['title'][:60]}")
        print(f"Größte Abweichung einer Antwort: {worst_feat:.4f}, des Laya-Werts (0–10): {worst_val:.3f}")
        print(f"Zeit pro Artikel: PyTorch {t_torch:.1f} s, ONNX {t_onnx:.1f} s"
              + (f"  → {t_torch / t_onnx:.1f}× so schnell" if t_onnx > 0 else ""))
        if worst_feat > 0.01:
            print("⚠ Abweichung größer als erwartet – ONNX lieber noch nicht einschalten und Ausgabe melden.")
        else:
            print("✓ ONNX liefert praktisch dieselben Ergebnisse.")
