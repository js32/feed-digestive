"""LLM-Clients: Anthropic (nativ) und alle OpenAI-kompatiblen APIs."""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time

import httpx

from .config import OPENAI_COMPATIBLE

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


# Gemeinsame Drossel pro Anbieter (gilt über Threads und LLM-Instanzen hinweg),
# z. B. für Gratis-Tarife mit ~2 Anfragen/Minute: LLM_MIN_INTERVAL=31
_throttle_lock = threading.Lock()
_last_call: dict[str, float] = {}


def _throttle(provider: str) -> None:
    interval = float(os.environ.get("LLM_MIN_INTERVAL", "0") or 0)
    if interval <= 0:
        return
    with _throttle_lock:
        wait = _last_call.get(provider, 0) + interval - time.monotonic()
        if wait > 0:
            log.info("%s: warte %.0fs (LLM_MIN_INTERVAL)", provider, wait)
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

    def __repr__(self) -> str:
        return f"{self.provider}:{self.model}"

    # ------------------------------------------------------------------
    def complete(self, system: str, user: str, max_tokens: int = 2000, json_mode: bool = False) -> str:
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
        if effort:
            body["reasoning_effort"] = effort
        r = self.http.post(f"{self.base_url}/chat/completions", headers=headers, json=body)
        r.raise_for_status()
        data = r.json()
        content = data["choices"][0]["message"]["content"] or ""
        if isinstance(content, list):  # Denk-Blöcke (type "thinking") verwerfen, nur die Antwort behalten
            content = "".join(c.get("text", "") for c in content
                              if isinstance(c, dict) and c.get("type") == "text")
        return content

    # ------------------------------------------------------------------
    def complete_json(self, system: str, user: str, max_tokens: int = 2000):
        text = self.complete(system, user, max_tokens, json_mode=True)
        try:
            return extract_json(text)
        except ValueError:
            log.warning("%s lieferte kein gültiges JSON, neuer Versuch", self)
            text = self.complete(system, user + "\n\nAntworte AUSSCHLIESSLICH mit gültigem JSON.", max_tokens,
                                 json_mode=True)
            return extract_json(text)


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
