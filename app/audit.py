"""Protokoll aller Anfragen an LLM-Anbieter und Datenschutz-Bericht daraus.

LLM_AUDIT=on    (Standard) je Aufruf: Anbieter, Modell, Zweck, Zeichen, Artikeltitel, welche Teile des Profils
LLM_AUDIT=full  zusätzlich der komplette Prompt-Text
LLM_AUDIT=off   nichts protokollieren

Alles liegt in data/llm_audit.jsonl und wird nach LLM_AUDIT_DAYS Tagen gelöscht.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

_lock = threading.Lock()
_path: Path | None = None

TITLE_RES = {  # wie die Artikeltitel im jeweiligen Prompt stehen
    "Bewertung": re.compile(r"^titel: (.*)$", re.M),
    "Auszug": re.compile(r"^Originaltitel: (.*)$", re.M),
    "Themen": re.compile(r"^id: \d+ – (.*?) – ", re.M),
}


def configure(data_dir: Path) -> None:
    global _path
    _path = data_dir / "llm_audit.jsonl"


def _mode() -> str:
    return (os.environ.get("LLM_AUDIT", "on") or "on").strip().lower()


def _days() -> int:
    try:
        return int(os.environ.get("LLM_AUDIT_DAYS", "30") or 30)
    except ValueError:
        return 30


def is_local(provider: str, base_url: str) -> bool:
    """Bleibt die Anfrage im eigenen Netz? (Ollama, localhost, private Adressen)"""
    host = urlparse(base_url).hostname or ""
    if provider == "ollama" or host in ("localhost", "host.docker.internal") or host.endswith(".local"):
        return True
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return False


def _section_lines(system: str, heading: str) -> int:
    m = re.search(rf"^## {re.escape(heading)}[^\n]*\n(.*?)(?=^## |</profil>|\Z)", system, re.M | re.S)
    return len([l for l in m.group(1).splitlines() if l.strip()]) if m else 0


def record(provider: str, model: str, base_url: str, purpose: str, system: str, user: str) -> None:
    mode = _mode()
    if mode == "off" or _path is None:
        return
    profile = re.search(r"<profil>\n?(.*?)</profil>", system, re.S)
    interests = bool(profile and profile.group(1).split("## ", 1)[0].strip())
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "provider": provider, "model": model, "host": urlparse(base_url).hostname,
        "local": is_local(provider, base_url), "purpose": purpose,
        "chars": len(system) + len(user),
        "interests": interests,
        "examples": _section_lines(system, "Beispiele"),
        "stars": _section_lines(system, "Zuletzt mit Stern markiert"),
        "titles": TITLE_RES[purpose].findall(user) if purpose in TITLE_RES else [],
    }
    if mode == "full":
        row.update(system=system, user=user)
    line = json.dumps(row, ensure_ascii=False) + "\n"
    try:
        with _lock, open(_path, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


def _rows(data_dir: Path, days: int) -> list[dict]:
    path = data_dir / "llm_audit.jsonl"
    if not path.exists():
        return []
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("ts", "") >= since:
            out.append(r)
    return out


def prune(data_dir: Path) -> None:
    path = data_dir / "llm_audit.jsonl"
    if not path.exists():
        return
    kept = _rows(data_dir, _days())
    with _lock:
        tmp = path.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in kept), encoding="utf-8")
        tmp.replace(path)


def summary(data_dir: Path, days: int) -> dict:
    """Zusammenfassung für Bericht und Status-Seite."""
    rows = _rows(data_dir, days)
    groups: dict[tuple, dict] = defaultdict(lambda: {"calls": 0, "chars": 0, "titles": set()})
    me = {"calls": 0, "interests": 0, "examples": 0, "examples_max": 0, "stars": 0, "stars_max": 0}
    for r in rows:
        g = groups[(r.get("local", False), r.get("provider"), r.get("host"), r.get("model"), r.get("purpose"))]
        g["calls"] += 1
        g["chars"] += r.get("chars", 0)
        g["titles"].update(r.get("titles") or [])
        if not r.get("local"):
            me["calls"] += 1
            me["interests"] += bool(r.get("interests"))
            me["examples"] += bool(r.get("examples"))
            me["examples_max"] = max(me["examples_max"], r.get("examples", 0))
            me["stars"] += bool(r.get("stars"))
            me["stars_max"] = max(me["stars_max"], r.get("stars", 0))
    first = min((r["ts"] for r in rows), default=None)
    return {
        "days": days, "calls": len(rows), "since": first, "mode": _mode(),
        "cloud_articles": len(set().union(*(g["titles"] for k, g in groups.items() if not k[0]))) if groups else 0,
        "groups": [{"local": k[0], "provider": k[1], "host": k[2], "model": k[3], "purpose": k[4],
                    "calls": g["calls"], "chars": g["chars"], "articles": len(g["titles"])}
                   for k, g in sorted(groups.items(), key=lambda kv: (kv[0][0], -kv[1]["chars"]))],
        "about_you": me,
    }


def _num(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def _n(n: int, one: str, many: str) -> str:
    return f"{_num(n)} {one if n == 1 else many}"


def report(data_dir: Path, days: int = 30) -> str:
    s = summary(data_dir, days)
    if s["mode"] == "off":
        return "LLM_AUDIT=off – es wird nichts protokolliert. Mit LLM_AUDIT=on (Standard) einschalten."
    if not s["calls"]:
        return (f"Keine LLM-Aufrufe in den letzten {days} Tagen protokolliert.\n"
                "Das Protokoll beginnt mit dem ersten Lauf nach dem Update.")
    out = [f"Datenschutz-Bericht: letzte {days} Tage, {s['calls']} LLM-Aufrufe "
           f"(protokolliert seit {s['since'][:10]})", ""]
    for local in (False, True):
        gs = [g for g in s["groups"] if g["local"] == local]
        if not gs:
            continue
        out.append("Lokal (verlässt den Server nicht):" if local else "An Cloud-Anbieter gesendet:")
        for g in gs:
            out.append(f"  {g['provider']} ({g['host']}), {g['model']} – {g['purpose']}: "
                       f"{_n(g['calls'], 'Aufruf', 'Aufrufe')}, {_num(g['chars'])} Zeichen "
                       f"(~{_num(g['chars'] // 4)} Tokens), {_n(g['articles'], 'Artikel', 'Artikel')}")
        out.append("")
    me = s["about_you"]
    if me["calls"]:
        out += ["Über dich an Cloud-Anbieter:",
                f"  Interessenprofil (interests.md):  in {me['interests']} von {me['calls']} Aufrufen",
                f"  Beispiele (examples.txt):         in {me['examples']} Aufrufen, bis zu {me['examples_max']} Einträge",
                f"  Titel deiner Sterne:              in {me['stars']} Aufrufen, bis zu {me['stars_max']} Titel", ""]
        tips = []
        if me["stars"]:
            tips.append("USE_MINIFLUX_STARRED=false hält deine Stern-Titel aus den Prompts (lokales Lernen bleibt).")
        if me["examples"]:
            tips.append("EXAMPLES_IN_PROMPT kleiner setzen schickt weniger Beispiele mit (0 = keine).")
        if tips:
            out += ["Weniger senden:"] + [f"  - {t}" for t in tips]
    if s["mode"] != "full":
        out += ["", "Mit LLM_AUDIT=full werden auch die kompletten Prompts in data/llm_audit.jsonl gespeichert."]
    return "\n".join(out)
