"""LLM provider abstraction with automatic fallback.

Supports: Gemini (free tier), Groq (free tier), OpenRouter (free models),
and local Ollama.  The first provider that succeeds is used; failures
cascade to the next configured provider.

Secrets are never logged.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
import structlog

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Provider implementations
# ---------------------------------------------------------------------------


def parse_defensive_json_array(raw: str) -> list[dict[str, Any]]:
    """Defensively parse a JSON array from LLM output, stripping fences and extra prose."""
    if not raw or not raw.strip():
        return []

    text = raw.strip()

    # Strip markdown code fences
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if fence_match:
        text = fence_match.group(1).strip()
    else:
        lines = [line for line in text.split("\n") if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()

    # 1. Try direct parse
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [x for x in parsed if isinstance(x, dict)]
        if isinstance(parsed, dict):
            for key in ("opportunities", "items", "results", "data"):
                if key in parsed and isinstance(parsed[key], list):
                    return [x for x in parsed[key] if isinstance(x, dict)]
            return [parsed]
    except Exception:
        pass

    # 2. Extract outermost [ ... ]
    start_idx = text.find("[")
    end_idx = text.rfind("]")
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        bracket_content = text[start_idx : end_idx + 1]
        try:
            parsed = json.loads(bracket_content)
            if isinstance(parsed, list):
                return [x for x in parsed if isinstance(x, dict)]
        except Exception:
            # Try cleaning trailing commas: ,] -> ] and ,} -> }
            cleaned = re.sub(r",\s*([\]}])", r"\1", bracket_content)
            try:
                parsed = json.loads(cleaned)
                if isinstance(parsed, list):
                    return [x for x in parsed if isinstance(x, dict)]
            except Exception:
                pass

    return []


async def _call_gemini_grounded(
    api_key: str,
    prompt: str,
    system: str = "",
) -> tuple[str, bool, str | None]:
    """Call Google Gemini API with Google Search grounding and verify grounding metadata.

    Returns:
        (text, is_grounded, failure_reason)
    """
    models = ["gemini-2.5-flash", "gemini-1.5-flash", "gemini-2.0-flash", "gemini-2.5-flash-lite"]
    last_err: Exception | None = None
    is_quota = False

    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        headers = {"Content-Type": "application/json"}
        params = {"key": api_key}

        contents = []
        if system:
            contents.append({"role": "user", "parts": [{"text": system}]})
            contents.append({"role": "model", "parts": [{"text": "Understood."}]})
        contents.append({"role": "user", "parts": [{"text": prompt}]})

        body: dict[str, Any] = {
            "contents": contents,
            "tools": [{"googleSearch": {}}],
        }

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(url, headers=headers, params=params, json=body)
                if resp.status_code == 429:
                    is_quota = True
                    last_err = RuntimeError("Gemini quota limit exceeded (HTTP 429)")
                    log.warning("gemini.quota_limit", model=model)
                    continue

                resp.raise_for_status()
                data = resp.json()
                candidate = data.get("candidates", [{}])[0]
                parts = candidate.get("content", {}).get("parts", [])
                texts = [p["text"] for p in parts if "text" in p]
                text = "\n".join(texts)

                # Check for genuine grounding metadata
                grounding_meta = candidate.get("groundingMetadata")
                has_grounding = bool(
                    grounding_meta
                    and (
                        grounding_meta.get("webSearchQueries")
                        or grounding_meta.get("groundingChunks")
                        or grounding_meta.get("groundingSupports")
                    )
                )

                if not has_grounding:
                    log.warning("gemini.no_grounding_metadata", model=model)
                    return text, False, "No grounding metadata or search queries returned by Gemini"

                return text, True, None
        except Exception as exc:
            err_str = str(exc).lower()
            if "429" in err_str or "quota" in err_str or "resource_exhausted" in err_str:
                is_quota = True
            last_err = exc
            log.warning("gemini.grounded_model_failed", model=model, error=str(exc))
            continue

    fail_reason = (
        "Google Search grounding hit quota limit (429 / Resource Exhausted)"
        if is_quota
        else f"Google Search grounding failed: {last_err or 'All Gemini models failed'}"
    )
    return "", False, fail_reason


async def _call_gemini(
    api_key: str,
    prompt: str,
    system: str = "",
    json_mode: bool = False,
    enable_grounding: bool = False,
) -> str:
    """Call Google Gemini API with automatic model fallback and optional Google Search grounding."""
    models = ["gemini-2.5-flash", "gemini-1.5-flash", "gemini-2.0-flash", "gemini-2.5-flash-lite"]
    last_err = None

    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        headers = {"Content-Type": "application/json"}
        params = {"key": api_key}

        contents = []
        if system:
            contents.append({"role": "user", "parts": [{"text": system}]})
            contents.append({"role": "model", "parts": [{"text": "Understood."}]})
        contents.append({"role": "user", "parts": [{"text": prompt}]})

        body: dict[str, Any] = {"contents": contents}

        if enable_grounding:
            # Enable Google Search grounding tool
            body["tools"] = [{"googleSearch": {}}]
            # Do NOT force responseMimeType when search grounding is active; Gemini returns 400
        elif json_mode:
            body["generationConfig"] = {"responseMimeType": "application/json"}

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(url, headers=headers, params=params, json=body)
                resp.raise_for_status()
                data = resp.json()
                parts = data["candidates"][0]["content"].get("parts", [])
                texts = [p["text"] for p in parts if "text" in p]
                if texts:
                    return "\n".join(texts)
                return ""
        except Exception as exc:
            last_err = exc
            log.warning("gemini.model_failed", model=model, error=str(exc))
            continue

    raise last_err or RuntimeError("All Gemini models failed")


async def _call_groq(api_key: str, prompt: str, system: str = "", json_mode: bool = False) -> str:
    """Call Groq API with active model fallback."""
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    models = ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]
    last_err = None

    for model in models:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 4096,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(url, headers=headers, json=body)
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]
        except Exception as exc:
            last_err = exc
            log.warning("groq.model_failed", model=model, error=str(exc))
            continue

    raise last_err or RuntimeError("All Groq models failed")


async def _call_openrouter(
    api_key: str, prompt: str, system: str = "", json_mode: bool = False
) -> str:
    """Call OpenRouter (with fallback across active free models)."""
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/selormtettehabotsi/TodayTech",
    }

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    models = [
        "poolside/laguna-s-2.1:free",
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
        "dots-studio/dots-3-note-preview:free",
        "poolside/laguna-xs-2.1:free",
    ]
    last_err: Exception | None = None

    for model in models:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 4096,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(url, headers=headers, json=body)
                resp.raise_for_status()
                data = resp.json()
                if "choices" in data and data["choices"]:
                    return data["choices"][0]["message"]["content"]
                raise RuntimeError(f"OpenRouter unexpected response: {data}")
        except Exception as exc:
            last_err = exc
            log.warning("openrouter.model_failed", model=model, error=str(exc))
            continue

    raise last_err or RuntimeError("All OpenRouter models failed")


async def _call_ollama(
    base_url: str, prompt: str, system: str = "", json_mode: bool = False
) -> str:
    """Call local Ollama instance."""
    url = f"{base_url.rstrip('/')}/api/chat"

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    body: dict[str, Any] = {
        "model": "llama3.2",
        "messages": messages,
        "stream": False,
    }
    if json_mode:
        body["format"] = "json"

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(url, json=body)
        resp.raise_for_status()
        data = resp.json()
        return data["message"]["content"]


# ---------------------------------------------------------------------------
# Unified LLM client
# ---------------------------------------------------------------------------


class LLMClient:
    """Multi-provider LLM client with automatic fallback."""

    def __init__(
        self,
        *,
        gemini_api_key: str = "",
        groq_api_key: str = "",
        openrouter_api_key: str = "",
        ollama_base_url: str = "http://localhost:11434",
    ) -> None:
        self._providers: list[tuple[str, Any]] = []
        if gemini_api_key:
            self._providers.append(("gemini", gemini_api_key))
        if groq_api_key:
            self._providers.append(("groq", groq_api_key))
        if openrouter_api_key:
            self._providers.append(("openrouter", openrouter_api_key))
        self._providers.append(("ollama", ollama_base_url))

    async def generate(
        self,
        prompt: str,
        *,
        system: str = "",
        json_mode: bool = False,
        enable_grounding: bool = False,
    ) -> str:
        """Generate text, trying providers in order until one succeeds."""
        last_error: Exception | None = None

        for name, key_or_url in self._providers:
            # If grounding is requested, only gemini supports Google Search grounding
            if enable_grounding and name != "gemini":
                continue
            try:
                log.debug("llm.calling", provider=name, grounding=enable_grounding)
                result = await self._dispatch(
                    name, key_or_url, prompt, system, json_mode, enable_grounding
                )
                log.debug("llm.success", provider=name, chars=len(result))
                return result
            except Exception as exc:
                log.warning("llm.provider_failed", provider=name, error=str(exc))
                last_error = exc
                continue

        raise RuntimeError(f"All LLM providers failed. Last error: {last_error}")

    async def generate_json(
        self,
        prompt: str,
        *,
        system: str = "",
    ) -> dict[str, Any]:
        """Generate and parse JSON output."""
        raw = await self.generate(prompt, system=system, json_mode=True)
        # Strip markdown code fences if present
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = [line for line in lines if not line.strip().startswith("```")]
            cleaned = "\n".join(lines)
        return json.loads(cleaned)

    async def generate_grounded_json_array(
        self,
        prompt: str,
        *,
        system: str = "",
    ) -> tuple[list[dict[str, Any]], bool, str | None]:
        """Generate with Google Search grounding, verify grounding metadata, and parse JSON array.

        Returns:
            (items, is_grounded, failure_reason)
        """
        gemini_key = next((key for name, key in self._providers if name == "gemini"), None)
        if not gemini_key:
            return [], False, "No Gemini API key configured"

        text, is_grounded, reason = await _call_gemini_grounded(
            gemini_key,
            prompt,
            system=system,
        )
        if not text:
            return [], False, reason

        items = parse_defensive_json_array(text)
        return items, is_grounded, reason

    @staticmethod
    async def _dispatch(
        name: str,
        key_or_url: str,
        prompt: str,
        system: str,
        json_mode: bool,
        enable_grounding: bool = False,
    ) -> str:
        match name:
            case "gemini":
                return await _call_gemini(key_or_url, prompt, system, json_mode, enable_grounding)
            case "groq":
                return await _call_groq(key_or_url, prompt, system, json_mode)
            case "openrouter":
                return await _call_openrouter(key_or_url, prompt, system, json_mode)
            case "ollama":
                return await _call_ollama(key_or_url, prompt, system, json_mode)
            case _:
                raise ValueError(f"Unknown LLM provider: {name}")


def create_llm_client_from_settings(settings: Any) -> LLMClient:
    """Factory that builds an LLMClient from application Settings."""
    return LLMClient(
        gemini_api_key=settings.gemini_api_key,
        groq_api_key=settings.groq_api_key,
        openrouter_api_key=settings.openrouter_api_key,
        ollama_base_url=settings.ollama_base_url,
    )
