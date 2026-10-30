"""LLM-Clients: Anthropic (nativ) und alle OpenAI-kompatiblen APIs."""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time

import httpx

from . import audit
from .config import OPENAI_COMPATIBLE

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


# Gemeinsame Drossel pro Anbieter (gilt über Threads und LLM-Instanzen hinweg). Den Abstand lernt
# die App selbst, sobald der Anbieter "zu viele Anfragen" (HTTP 429) meldet; LLM_MIN_INTERVAL setzt
# optional einen festen Mindestabstand von Anfang an.
_throttle_lock = threading.Lock()
_last_call: dict[str, float] = {}
_learned: dict[str, float] = {}
LEARNED_MAX = 65  # mehr als gut eine Minute Abstand braucht kein Tarif


def _learn_rate_limit(provider: str, retry_after: str) -> None:
    """Nach einem 429 den Abstand für diesen Anbieter erhöhen – für den Rest der Laufzeit."""
    with _throttle_lock:
        old = _learned.get(provider, 0.0)
        new = float(retry_after) if retry_after.isdigit() else old * 2 or 5.0
        new = min(LEARNED_MAX, max(old, new))
        if new > old:
            _learned[provider] = new
            log.info("%s: Rate-Limit erreicht, ab jetzt mindestens %.0fs zwischen zwei Anfragen", provider, new)


def reset_rate_limits() -> None:
    """Gelernte Abstände vergessen – nach jedem Lauf, damit ein kurzer Engpass nicht alle
    folgenden Läufe bremst. Der Server-Prozess läuft ja tagelang."""
    with _throttle_lock:
        if _learned:
            log.info("Gelernte Rate-Limits zurückgesetzt: %s",
                     ", ".join(f"{p} {v:.0f}s" for p, v in sorted(_learned.items())))
        _learned.clear()


def _throttle(provider: str) -> None:
    fixed, learned = float(os.environ.get("LLM_MIN_INTERVAL", "0") or 0), _learned.get(provider, 0.0)
    interval = max(fixed, learned)
    if interval <= 0:
        return
    reason = "Rate-Limit" if learned > fixed else "LLM_MIN_INTERVAL"
    with _throttle_lock:
        wait = _last_call.get(provider, 0) + interval - time.monotonic()
        if wait > 0:
            log.info("%s: warte %.0fs (%s)", provider, wait, reason)
            time.sleep(wait)
        _last_call[provider] = time.monotonic()


class LLM:
    def __init__(self, provider: str, model: str, timeout: float | None = None):
        self.provider = provider
        self.model = model
        # lokale Modelle auf CPU brauchen pro Aufruf schnell mehrere Minuten
        default_timeout = 900 if provider in ("ollama", "custom") else 120
        timeout = timeout or float(os.environ.get("LLM_TIMEOUT", default_timeout))
        self.http = httpx.Client(timeout=timeout)
        if provider == "anthropic":
            self.api_key = os.environ.get("ANTHROPIC_API_KEY", "")
            if not self.api_key:
                raise SystemExit("ANTHROPIC_API_KEY fehlt")
            self.base_url = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")
        else:
            default_url, key_env = OPENAI_COMPATIBLE[provider]
            self.base_url = (os.environ.get(f"{provider.upper()}_BASE_URL") or default_url or "").rstrip("/")
            if not self.base_url:
                raise SystemExit(f"{provider.upper()}_BASE_URL fehlt")
            self.api_key = os.environ.get(key_env, "")
            if not self.api_key and provider not in ("ollama", "custom"):
                raise SystemExit(f"{key_env} fehlt")
        key_env = "ANTHROPIC_API_KEY" if provider == "anthropic" else OPENAI_COMPATIBLE[provider][1]
        if self.api_key and (not self.api_key.isascii() or self.api_key.startswith("<") or " " in self.api_key.strip()):
            # sonst bricht httpx später mit einem kryptischen UnicodeEncodeError im Header ab
            raise SystemExit(f"{key_env} sieht nicht wie ein Schlüssel aus (Umlaute, Leerzeichen oder <Platzhalter>?) "
                             "– bitte den echten Schlüssel in die .env eintragen")

    def __repr__(self) -> str:
        return f"{self.provider}:{self.model}"

    @property
    def is_local(self) -> bool:
        return audit.is_local(self.provider, self.base_url)

    # ------------------------------------------------------------------
    def complete(self, system: str, user: str, max_tokens: int = 2000, json_mode: bool = False,
                 purpose: str = "Sonstiges") -> str:
        audit.record(self.provider, self.model, self.base_url, purpose, system, user)
        last: Exception | None = None
        for attempt in range(5):
            _throttle(self.provider)
            try:
                if self.provider == "anthropic":
                    return self._anthropic(system, user, max_tokens)
                return self._openai(system, user, max_tokens, json_mode)
            except httpx.HTTPStatusError as e:
                code = e.response.status_code
                last = e
                if code in (429, 500, 502, 503, 504, 529):
                    wait = min(60, 2 ** attempt * 3)
                    retry_after = e.response.headers.get("retry-after", "")
                    if code == 429:
                        _learn_rate_limit(self.provider, retry_after)
                    if retry_after.isdigit():  # Vorgabe des Anbieters respektieren
                        wait = min(120, max(wait, int(retry_after)))
                    log.warning("%s: HTTP %s, neuer Versuch in %ss", self, code, wait)
                    time.sleep(wait)
                    continue
                raise LLMError(f"{self}: HTTP {code}: {e.response.text[:500]}") from e
            except httpx.TransportError as e:
                last = e
                time.sleep(2 ** attempt * 3)
        detail = ""
        if isinstance(last, httpx.HTTPStatusError):
            detail = f" – Antwort des Anbieters: {last.response.text[:400]}"
        raise LLMError(f"{self}: aufgegeben nach mehreren Versuchen: {last}{detail}")

    def _anthropic(self, system: str, user: str, max_tokens: int) -> str:
        r = self.http.post(
            f"{self.base_url}/v1/messages",
            headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={
                "model": self.model,
                "max_tokens": max_tokens,
                # Systemprompt (Profil) wird gecacht -> günstiger bei vielen Batches
                "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                "messages": [{"role": "user", "content": user}],
            },
        )
        r.raise_for_status()
        data = r.json()
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")

    def _openai(self, system: str, user: str, max_tokens: int, json_mode: bool = False) -> str:
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        # OpenAI selbst will bei neueren Modellen max_completion_tokens
        body["max_completion_tokens" if self.provider == "openai" else "max_tokens"] = max_tokens
        # JSON-Modus: zwingt kleine lokale Modelle zu gültigem JSON
        if json_mode and self.provider in ("ollama", "openai", "mistral"):
            body["response_format"] = {"type": "json_object"}
        # Denkmodus steuern (z. B. Mistral Small 4 / Medium 3.5: "none" oder "high").
        # Nur senden, wenn gesetzt – Modelle ohne diesen Parameter lehnen ihn ab.
        effort = os.environ.get("REASONING_EFFORT", "").strip()
        if effort and not getattr(self, "_no_reasoning", False):
            body["reasoning_effort"] = effort
        r = self.http.post(f"{self.base_url}/chat/completions", headers=headers, json=body)
        if r.status_code in (400, 422) and "reasoning_effort" in body:
            # Modell kennt den Parameter nicht -> ohne ihn wiederholen und merken
            log.info("%s: reasoning_effort=%s abgelehnt, wird für dieses Modell weggelassen (Antwort: %s)",
                     self, effort, r.text[:200])
            self._no_reasoning = True
            body.pop("reasoning_effort")
            r = self.http.post(f"{self.base_url}/chat/completions", headers=headers, json=body)
        r.raise_for_status()
        data = r.json()
        choice = data["choices"][0]
        self._last_finish = choice.get("finish_reason")
        if self._last_finish == "length":
            log.warning("%s: Antwort bei max_tokens=%d abgeschnitten", self, max_tokens)
        content = choice["message"]["content"] or ""
        if isinstance(content, list):  # Denk-Blöcke (type "thinking") verwerfen, nur die Antwort behalten
            content = "".join(c.get("text", "") for c in content
                              if isinstance(c, dict) and c.get("type") == "text")
        return content

    # ------------------------------------------------------------------
    def complete_json(self, system: str, user: str, max_tokens: int = 2000, purpose: str = "Sonstiges"):
        text = self.complete(system, user, max_tokens, json_mode=True, purpose=purpose)
        try:
            return extract_json(text)
        except ValueError:
            # häufigste Ursache: Antwort abgeschnitten (zu knappes max_tokens, v. a. bei Modellen, die erst
            # "nachdenken") – deshalb beim zweiten Versuch deutlich mehr Platz lassen
            log.warning("%s lieferte kein gültiges JSON (Anfang: %r), neuer Versuch mit mehr Platz",
                        self, text[:120])
            text = self.complete(system, user + "\n\nAntworte AUSSCHLIESSLICH mit gültigem JSON.", max_tokens * 3,
                                 json_mode=True, purpose=purpose)
            try:
                return extract_json(text)
            except ValueError:
                log.warning("%s: auch der zweite Versuch ohne gültiges JSON (Anfang: %r)", self, text[:200])
                raise


def extract_json(text: str):
    # "Denk"-Abschnitte von Reasoning-Modellen (Qwen, DeepSeek …) entfernen
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for open_c, close_c in (("[", "]"), ("{", "}")):
        start, end = text.find(open_c), text.rfind(close_c)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("kein JSON gefunden")
