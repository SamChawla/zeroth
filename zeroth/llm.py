"""OpenAI-compatible chat completion with a provider fallback.

Euri is primary, Groq is the fallback. Both speak the same wire format, so
the fallback is a base-url swap rather than a second client.
"""
import json
import re

import httpx

from zeroth.config import settings


class LLMError(Exception):
    pass


def _providers() -> list[tuple[str, str, str, str]]:
    out = []
    if settings.euri_api_key:
        out.append(("euri", settings.euri_base_url, settings.euri_api_key, settings.euri_model))
    if settings.groq_api_key:
        out.append(("groq", settings.groq_base_url, settings.groq_api_key, settings.groq_model))
    return out


def complete(system: str, user: str, max_tokens: int = 2000) -> str:
    errors = []
    for name, base, key, model in _providers():
        try:
            resp = httpx.post(
                f"{base.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": model,
                    "max_tokens": max_tokens,
                    "temperature": 0.1,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
                timeout=90,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as exc:  # noqa: BLE001 - fall through to next provider
            errors.append(f"{name}: {exc}")
    raise LLMError("; ".join(errors) or "No LLM provider configured.")


def complete_json(system: str, user: str, max_tokens: int = 2000) -> dict:
    """Ask for JSON and parse defensively. Models add prose and fences."""
    raw = complete(
        system + "\n\nRespond with a single JSON object and nothing else. "
        "No prose, no markdown fences.",
        user,
        max_tokens,
    )
    return parse_json(raw)


def parse_json(raw: str) -> dict:
    text = raw.strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise LLMError(f"Model did not return JSON: {text[:200]}")
