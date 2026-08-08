"""OpenAI-compatible chat completion with a provider fallback.

Euri is primary, Groq is the fallback. Both speak the same wire format, so
the fallback is a base-url swap rather than a second client.

Groq is tried across every model in settings.groq_models, in order. Each
GroqCloud model enforces its own max-completion-tokens ceiling (see
GROQ_MODEL_LIMITS below, from console.groq.com/docs/models) — a request
capped for one model can still be served in full by the next, so the loop
in complete() doubles as a token-limit fallback, not just a provider one.
"""
import json
import re

import httpx

from zeroth.config import settings

# max_completion_tokens per GroqCloud production model. Unlisted models
# (e.g. one added to groq_models without updating this table) fall back to
# GROQ_DEFAULT_LIMIT rather than sending an uncapped request.
GROQ_MODEL_LIMITS = {
    "llama-3.1-8b-instant": 131_072,
    "llama-3.3-70b-versatile": 32_768,
    "openai/gpt-oss-120b": 65_536,
    "openai/gpt-oss-20b": 65_536,
}
GROQ_DEFAULT_LIMIT = 8_192


class LLMError(Exception):
    pass


def _providers() -> list[tuple[str, str, str, str, int | None]]:
    """(name, base_url, api_key, model, max_tokens_cap) — cap is None for
    providers with a single model and no known ceiling to enforce."""
    out = []
    if settings.euri_api_key:
        out.append(("euri", settings.euri_base_url, settings.euri_api_key, settings.euri_model, None))
    if settings.groq_api_key:
        for model in [m.strip() for m in settings.groq_models.split(",") if m.strip()]:
            cap = GROQ_MODEL_LIMITS.get(model, GROQ_DEFAULT_LIMIT)
            out.append(("groq", settings.groq_base_url, settings.groq_api_key, model, cap))
    return out


def complete(system: str, user: str, max_tokens: int = 2000) -> str:
    errors = []
    for name, base, key, model, cap in _providers():
        request_tokens = min(max_tokens, cap) if cap else max_tokens
        try:
            resp = httpx.post(
                f"{base.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": model,
                    "max_tokens": request_tokens,
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
            errors.append(f"{name}/{model}: {exc}")
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
