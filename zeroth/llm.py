"""OpenAI-compatible chat completion with providers, BYOK and a budget.

House keys: Euri primary, Groq fallback, same wire format either way. Groq is
tried across every model in settings.groq_models, in order - each GroqCloud
model enforces its own max-completion-tokens ceiling (GROQ_MODEL_LIMITS, from
console.groq.com/docs/models), so the loop doubles as a token-limit fallback.

BYOK: a run may carry its own OpenAI-compatible key (preset provider or custom
base URL). When it does, ONLY that provider is used - falling back to house
keys would quietly spend the operator's budget on a run that promised not to -
and the run's token budget is waived: the caller's key runs until the caller's
provider says otherwise.

Budget: runs on house keys count completion+prompt tokens against a per-run
ceiling. The counter lives in module state set by the worker around each run;
the worker is single-threaded per job, which is what makes that safe.
"""
import json
import re

import httpx

from zeroth.config import settings

# max_completion_tokens per GroqCloud production model. Unlisted models fall
# back to GROQ_DEFAULT_LIMIT rather than sending an uncapped request.
GROQ_MODEL_LIMITS = {
    "llama-3.1-8b-instant": 131_072,
    "llama-3.3-70b-versatile": 32_768,
    "openai/gpt-oss-120b": 65_536,
    "openai/gpt-oss-20b": 65_536,
}
GROQ_DEFAULT_LIMIT = 8_192

# The presets a run can bring a key for. Kept deliberately short and honest:
# these are the ones tested; "custom" covers any other OpenAI-compatible
# endpoint the user can vouch for themselves.
BYOK_PRESETS = {
    "openai": {"base_url": "https://api.openai.com/v1", "default_model": "gpt-4o-mini"},
    "groq": {"base_url": "https://api.groq.com/openai/v1", "default_model": "llama-3.3-70b-versatile"},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "default_model": "meta-llama/llama-3.3-70b-instruct"},
    "custom": {"base_url": "", "default_model": ""},  # both supplied by the user
}


class LLMError(Exception):
    pass


class BudgetExceeded(LLMError):
    pass


# Per-run context, owned by the worker. One job runs at a time per process.
_run = {"byok": None, "used_tokens": 0}


def set_run_context(byok: dict | None) -> None:
    """Called by the worker at the start of a job's processing.

    byok: {"provider", "api_key", "model"?, "base_url"?} or None for house keys.
    """
    _run["byok"] = byok or None
    _run["used_tokens"] = 0


def clear_run_context() -> None:
    _run["byok"] = None
    _run["used_tokens"] = 0


def tokens_used() -> int:
    return _run["used_tokens"]


def _providers() -> list[tuple[str, str, str, str, int | None]]:
    """(name, base_url, api_key, model, max_tokens_cap)."""
    byok = _run["byok"]
    if byok:
        preset = BYOK_PRESETS.get(byok.get("provider") or "custom", BYOK_PRESETS["custom"])
        base = (byok.get("base_url") or preset["base_url"]).rstrip("/")
        model = byok.get("model") or preset["default_model"]
        if base and model and byok.get("api_key"):
            return [("byok:" + (byok.get("provider") or "custom"), base, byok["api_key"], model, None)]
        raise LLMError("BYOK is set but incomplete: provider/base URL, model and key are all required.")

    out = []
    if settings.euri_api_key:
        out.append(("euri", settings.euri_base_url, settings.euri_api_key, settings.euri_model, None))
    if settings.groq_api_key:
        for model in [m.strip() for m in settings.groq_models.split(",") if m.strip()]:
            cap = GROQ_MODEL_LIMITS.get(model, GROQ_DEFAULT_LIMIT)
            out.append(("groq", settings.groq_base_url, settings.groq_api_key, model, cap))
    return out


def _check_budget() -> None:
    """House keys are a shared resource; a run gets a slice, not the pool.

    BYOK runs are exempt by design - the key's owner decides when it is
    exhausted, not us.
    """
    if _run["byok"]:
        return
    if _run["used_tokens"] >= settings.llm_token_budget:
        raise BudgetExceeded(
            f"This run used its {settings.llm_token_budget:,}-token analysis budget. "
            "Bring your own OpenAI-compatible key (BYOK) to run without this cap."
        )


def complete(system: str, user: str, max_tokens: int = 2000) -> str:
    _check_budget()
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
            data = resp.json()
            usage = data.get("usage") or {}
            _run["used_tokens"] += int(usage.get("total_tokens")
                                       or (usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0))
                                       or max_tokens)  # no usage reported: assume the worst
            return data["choices"][0]["message"]["content"]
        except BudgetExceeded:
            raise
        except Exception as exc:  # noqa: BLE001 - fall through to next provider
            # Never echo the key; an auth error message can contain the header.
            errors.append(f"{name}/{model}: {str(exc).replace(key, '<key>')[:200]}")
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
