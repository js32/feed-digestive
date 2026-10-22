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

_ROOT = Path(__file__).resolve().parent.parent  # Ordner, in dem "python -m app" funktioniert
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
def _size(folder: Path) -> float:
    return sum(f.stat().st_size for f in folder.iterdir() if f.is_file()) / 1e9


def export(s, check: int = 5) -> None:
    """Jedes Modell in einem eigenen Prozess exportieren, danach vergleichen – ebenfalls je Backend
    in einem eigenen Prozess. So hängt nie mehr als ein Modell gleichzeitig im Speicher."""
    import subprocess
    import sys
    models = MODELS_FOR.get((s.laya_model or "auto").lower(), [s.laya_model])
    for model in models:
        r = subprocess.run([sys.executable, "-m", "app", "_laya-export-one", model], cwd=_ROOT)
        if r.returncode != 0:
            raise SystemExit(_died(r.returncode, f"Export von {model}"))
    if check > 0:
        compare(s, check, models)
    print("\nFertig. Einschalten mit  LAYA_BACKEND=onnx  in der .env, dann  docker compose up -d")


def _died(code: int, what: str) -> str:
    if code in (-9, 137):
        return (f"✗ {what} wurde beendet (Speicher voll?). DIGEST_MEM_LIMIT in der .env vorübergehend "
                "erhöhen, docker compose up -d, dann erneut.")
    return f"✗ {what} fehlgeschlagen (Exit-Code {code})"


def export_one(s, model: str) -> None:
    import shutil
    from laya.agent import Agent
    from .vendor import laya_export_onnx as ex

    repo, sub = _repo(model)
    out = onnx_file(s.data_dir, model)
    print(f"\n== {model} ({repo}{'/' + sub if sub else ''}) ==")
    print("Lade PyTorch-Modell …")
    agent = Agent(repo, subfolder=sub, compile=False, device="cpu")
    # Die Gewichte landen in einer Nebendatei (laya.onnx.data), die über ihren Namen gefunden wird –
    # deshalb gleich unter dem endgültigen Namen in einen Zwischenordner schreiben und den Ordner tauschen.
    tmp_dir = out.parent.with_name(out.parent.name + ".tmp")
    shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir(parents=True)
    print(f"Exportiere nach {out} (dauert ein paar Minuten, braucht viel RAM) …")
    ex.export_module(agent.model, str(tmp_dir / out.name))
    print("Prüfe die Datei gegen PyTorch (Batch 1, 2, 3) …")
    ex.verify_batch_dynamic(agent.model, str(tmp_dir / out.name))
    shutil.rmtree(out.parent, ignore_errors=True)
    tmp_dir.rename(out.parent)
    print(f"✓ geschrieben: {out.parent}/ ({_size(out.parent):.2f} GB)")


# ----------------------------------------------------------------------
def compare(s, n: int, models: list[str] | None = None) -> None:
    import json
    import subprocess
    import sys
    models = models or MODELS_FOR.get((s.laya_model or "auto").lower(), [s.laya_model])
    samples = _samples(s, n)
    if not samples:
        print("Keine ungelesenen Artikel für den Vergleich gefunden.")
        return
    work = onnx_dir(s.data_dir) / "compare"
    work.mkdir(parents=True, exist_ok=True)
    (work / "samples.json").write_text(json.dumps(samples, ensure_ascii=False), encoding="utf-8")
    for model in models:
        if not onnx_file(s.data_dir, model).exists():
            print(f"\n{model}: keine ONNX-Datei – erst exportieren")
            continue
        print(f"\n== Vergleich {model} an {len(samples)} Artikeln ==")
        results = {}
        for backend in ("torch", "onnx"):
            print(f"  {backend} …", flush=True)
            r = subprocess.run([sys.executable, "-m", "app", "_laya-compare-one", model, backend], cwd=_ROOT)
            if r.returncode != 0:
                raise SystemExit(_died(r.returncode, f"Vergleich {model}/{backend}"))
            results[backend] = json.loads((work / f"{model}-{backend}.json").read_text(encoding="utf-8"))
        _report(s, samples, results["torch"], results["onnx"])


def compare_one(s, model: str, backend: str) -> None:
    """Ein Backend, ein Modell, gespeicherte Artikel – Ergebnis als JSON (läuft im eigenen Prozess)."""
    import json
    work = onnx_dir(s.data_dir) / "compare"
    samples = json.loads((work / "samples.json").read_text(encoding="utf-8"))
    repo, sub = _repo(model)
    if backend == "torch":
        from laya.agent import Agent
        agent = Agent(repo, subfolder=sub, compile=False, device="cpu")
    else:
        from laya.onnx_agent import ONNXAgent
        agent = ONNXAgent(repo, onnx_path=str(onnx_file(s.data_dir, model)), subfolder=sub)
    flt, questions, states = _setup(s, samples)
    kw = {"batch_size": s.laya_batch_size, "max_len": s.laya_max_len, "sort_by_length": True}
    agent.predict_batch(states[:1], questions, **kw)  # Aufwärmen, nicht mitgemessen
    t = time.monotonic()
    res = agent.predict_batch(states, questions, **kw)
    secs = (time.monotonic() - t) / len(states)
    out = {"features": [flt.features(r.get("answers", {})) for r in res], "secs": secs}
    (work / f"{model}-{backend}.json").write_text(json.dumps(out), encoding="utf-8")


def _setup(s, samples):
    from .laya_filter import STATE_CHARS, LayaFilter, load_questions
    flt = LayaFilter.__new__(LayaFilter)  # nur Fragen und Formel, ohne eigenen Router
    flt.spec = load_questions(s.config_dir / "laya_questions.json")
    states = [f"Source: {it['feed']}\nTitle: {it['title']}\n\n{it['text'][:STATE_CHARS]}" for it in samples]
    return flt, flt._build_questions(), states


def _report(s, samples, torch_res, onnx_res) -> None:
    flt, _, _ = _setup(s, samples)
    worst_feat = worst_val = 0.0
    for it, a, b in zip(samples, torch_res["features"], onnx_res["features"]):
        d = max(abs(a[k] - b.get(k, 0.0)) for k in a)
        va, vb = flt.combine_features(a)[0], flt.combine_features(b)[0]
        worst_feat, worst_val = max(worst_feat, d), max(worst_val, abs(va - vb))
        print(f"  {va:5.2f} / {vb:5.2f}  (max Δ Antwort {d:.4f})  {it['title'][:60]}")
    t_torch, t_onnx = torch_res["secs"], onnx_res["secs"]
    print(f"Größte Abweichung einer Antwort: {worst_feat:.4f}, des Laya-Werts (0–10): {worst_val:.3f}")
    print(f"Zeit pro Artikel: PyTorch {t_torch:.1f} s, ONNX {t_onnx:.1f} s"
          + (f"  → {t_torch / t_onnx:.1f}× so schnell" if t_onnx > 0 else ""))
    if worst_feat > 0.01:
        print("⚠ Abweichung größer als erwartet – ONNX lieber noch nicht einschalten und Ausgabe melden.")
    else:
        print("✓ ONNX liefert praktisch dieselben Ergebnisse.")


def _samples(s, n: int) -> list[dict]:
    from datetime import datetime, timedelta, timezone
    from .digest import prepare
    from .sources import make_source
    print(f"Hole {n} ungelesene Artikel für den Vergleich …")
    raw = make_source(s).fetch_unread(max(n * 3, n))
    items = prepare(raw, s, datetime.now(timezone.utc) - timedelta(days=3650))
    return [it for it in items if len(it.get("text") or "") > 300][:n] or items[:n]
