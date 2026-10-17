"""LLM-Clients: Anthropic (nativ) und alle OpenAI-kompatiblen APIs."""
from __future__ import annotations

import json
import logging
import os
import re
import time

import httpx

from .config import OPENAI_COMPATIBLE

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, provider: str, model: str, timeout: float = 120):
        self.provider = provider
        self.model = model
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
    def complete(self, system: str, user: str, max_tokens: int = 2000) -> str:
        last: Exception | None = None
        for attempt in range(5):
            try:
                if self.provider == "anthropic":
                    return self._anthropic(system, user, max_tokens)
                return self._openai(system, user, max_tokens)
            except httpx.HTTPStatusError as e:
                code = e.response.status_code
                last = e
                if code in (429, 500, 502, 503, 504, 529):
                    wait = min(60, 2 ** attempt * 3)
                    log.warning("%s: HTTP %s, neuer Versuch in %ss", self, code, wait)
                    time.sleep(wait)
                    continue
                raise LLMError(f"{self}: HTTP {code}: {e.response.text[:500]}") from e
            except httpx.TransportError as e:
                last = e
                time.sleep(2 ** attempt * 3)
        raise LLMError(f"{self}: aufgegeben nach mehreren Versuchen: {last}")

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

    def _openai(self, system: str, user: str, max_tokens: int) -> str:
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        # OpenAI selbst will bei neueren Modellen max_completion_tokens
        body["max_completion_tokens" if self.provider == "openai" else "max_tokens"] = max_tokens
        r = self.http.post(f"{self.base_url}/chat/completions", headers=headers, json=body)
        r.raise_for_status()
        data = r.json()
        return data["choices"][0]["message"]["content"] or ""

    # ------------------------------------------------------------------
    def complete_json(self, system: str, user: str, max_tokens: int = 2000):
        text = self.complete(system, user, max_tokens)
        try:
            return extract_json(text)
        except ValueError:
            log.warning("%s lieferte kein gültiges JSON, neuer Versuch", self)
            text = self.complete(system, user + "\n\nAntworte AUSSCHLIESSLICH mit gültigem JSON.", max_tokens)
            return extract_json(text)


def extract_json(text: str):
    text = text.strip()
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
