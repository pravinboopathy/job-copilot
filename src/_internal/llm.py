"""Minimal multi-provider LLM wrapper for the job-copilot pipeline.

Built on top of LiteLLM. The public surface area is intentionally small:

- ``LLMConfig`` — the four fields needed to address any supported provider.
- ``complete(prompt, ...)`` — single-call text completion with router retries.
- ``complete_json(prompt, ...)`` — JSON-mode completion with content-quality
  retries on top of the router's transport retries.

Everything else (provider quirks, JSON extraction from noisy responses,
think-tag stripping, router caching) is implementation detail and stays
private to this module.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
from dataclasses import dataclass
from typing import Any

import litellm
from litellm import Router
from litellm.router import RetryPolicy
from pydantic import BaseModel

# LiteLLM is talkative at INFO; the wrapper only surfaces actionable warnings.
for _name in ("LiteLLM", "LiteLLM Router", "LiteLLM Proxy"):
    logging.getLogger(_name).setLevel(logging.WARNING)

_log = logging.getLogger(__name__)

_TIMEOUT_TEXT_SECONDS = 120
_TIMEOUT_JSON_SECONDS = 180
_MAX_JSON_BYTES = 1 << 20  # 1 MiB — anything larger is almost certainly a runaway
_MAX_JSON_RECURSION = 8


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class LLMConfig(BaseModel):
    """Address a single provider/model. Constructed by the caller from env."""

    provider: str
    model: str
    api_key: str
    api_base: str | None = None


# ---------------------------------------------------------------------------
# Model name + base URL normalization
# ---------------------------------------------------------------------------

# LiteLLM expects provider-prefixed model strings for everything except OpenAI.
# When the caller already prefixed the model (e.g. "anthropic/claude-..."), pass
# through unchanged. OpenRouter is special: it always needs "openrouter/" and
# uses nested prefixes like "openrouter/anthropic/claude-...".
_PROVIDER_PREFIX: dict[str, str] = {
    "openai": "",
    "anthropic": "anthropic/",
    "gemini": "gemini/",
    "openrouter": "openrouter/",
    "deepseek": "deepseek/",
    "ollama": "ollama_chat/",
}

_KNOWN_PREFIXES = tuple(p for p in _PROVIDER_PREFIX.values() if p) + ("ollama/",)


def _qualify_model(cfg: LLMConfig) -> str:
    """Return the LiteLLM-formatted model identifier for *cfg*."""
    if cfg.provider == "openrouter":
        return cfg.model if cfg.model.startswith("openrouter/") else f"openrouter/{cfg.model}"
    if cfg.model.startswith(_KNOWN_PREFIXES):
        return cfg.model
    prefix = _PROVIDER_PREFIX.get(cfg.provider, "")
    return f"{prefix}{cfg.model}" if prefix else cfg.model


def _normalize_api_base(provider: str, api_base: str | None) -> str | None:
    """Trim provider-specific suffixes users tend to paste in.

    Most users copy the full base URL from provider docs, which often already
    includes a ``/v1`` segment. LiteLLM appends its own path components, so
    leaving the suffix in produces ``/v1/v1/...`` 404s. This is a best-effort
    cleanup for the common cases — exotic deployments may still need a custom
    base.
    """
    if not api_base:
        return None
    base = api_base.strip().rstrip("/")
    if not base:
        return None
    duplicated_v1 = {"anthropic", "gemini", "openrouter"}
    if provider in duplicated_v1 and base.endswith("/v1"):
        base = base[: -len("/v1")].rstrip("/")
    if provider == "ollama":
        # Ollama users sometimes paste /api/chat or /api/generate.
        for suffix in ("/v1", "/api/chat", "/api/generate", "/api"):
            if base.endswith(suffix):
                base = base[: -len(suffix)].rstrip("/")
                break
    return base or None


# ---------------------------------------------------------------------------
# Router cache — rebuild lazily when the underlying config changes.
# ---------------------------------------------------------------------------

_router_lock = threading.Lock()
_router_cache: tuple[str, Router] | None = None


def _config_fingerprint(cfg: LLMConfig) -> str:
    """Stable cache key. The api_key is hashed so the raw value never escapes."""
    key_hash = hash(cfg.api_key) if cfg.api_key else 0
    return f"{cfg.provider}|{cfg.model}|{cfg.api_base or ''}|{key_hash}"


def _build_router(cfg: LLMConfig) -> Router:
    params: dict[str, Any] = {"model": _qualify_model(cfg)}
    if cfg.api_key:
        params["api_key"] = cfg.api_key
    base = _normalize_api_base(cfg.provider, cfg.api_base)
    if base:
        params["api_base"] = base
    return Router(
        model_list=[{"model_name": "active", "litellm_params": params}],
        num_retries=3,
        retry_policy=RetryPolicy(
            # Auth and bad-request failures are deterministic — retrying just
            # burns latency and quota. The transient classes get backoff retries.
            AuthenticationErrorRetries=0,
            BadRequestErrorRetries=0,
            ContentPolicyViolationErrorRetries=0,
            TimeoutErrorRetries=2,
            RateLimitErrorRetries=3,
            InternalServerErrorRetries=2,
        ),
        # No fallback deployment is configured, so cooldowns would blackout the
        # entire backend on a single transient failure. Re-enable if/when a
        # secondary deployment is added.
        disable_cooldowns=True,
    )


def _get_router(cfg: LLMConfig) -> Router:
    global _router_cache
    key = _config_fingerprint(cfg)
    with _router_lock:
        if _router_cache is None or _router_cache[0] != key:
            _router_cache = (key, _build_router(cfg))
            _log.info("Built LiteLLM router for %s/%s", cfg.provider, cfg.model)
        return _router_cache[1]


# ---------------------------------------------------------------------------
# Response text extraction
# ---------------------------------------------------------------------------


def _flatten_content(value: Any, depth: int = 0) -> str:
    """Collapse LiteLLM's per-provider content shapes into a plain string.

    Providers return content as a string (most), a list of ``{"type": "text",
    "text": "..."}`` parts (Anthropic vision, some thinking models), or a dict
    with a single ``text``/``content`` field. Recursion is bounded so a hostile
    response can't blow the stack.
    """
    if depth > 6 or value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_flatten_content(item, depth + 1) for item in value)
    if isinstance(value, dict):
        for key in ("text", "content", "value"):
            if key in value:
                return _flatten_content(value[key], depth + 1)
        return ""
    for attr in ("text", "content"):
        if hasattr(value, attr):
            return _flatten_content(getattr(value, attr), depth + 1)
    return ""


def _response_text(response: Any) -> str:
    if not response or not getattr(response, "choices", None):
        return ""
    choice = response.choices[0]
    msg = getattr(choice, "message", None)
    if msg is None and isinstance(choice, dict):
        msg = choice.get("message")
    if msg is not None:
        content = getattr(msg, "content", None) if not isinstance(msg, dict) else msg.get("content")
        if content:
            return _flatten_content(content).strip()
    # Fall back to delta / text fields (streaming-style or legacy completions)
    for field in ("text", "delta"):
        value = getattr(choice, field, None) if not isinstance(choice, dict) else choice.get(field)
        if value:
            return _flatten_content(value).strip()
    return ""


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------

_THINK_BLOCK = re.compile(r"<think\b[^>]*>.*?(?:</think>|$)", re.DOTALL | re.IGNORECASE)
_CODE_FENCE = re.compile(r"```(?:json|JSON)?\s*([\s\S]*?)```", re.MULTILINE)


def _strip_reasoning(text: str) -> str:
    """Reasoning models (deepseek-r1, qwq, ...) wrap their scratchpad in <think>."""
    return _THINK_BLOCK.sub("", text).strip()


def _carve_json_object(text: str) -> str:
    """Return the first balanced ``{...}`` slice from *text*.

    The body of the slice still needs ``json.loads`` to validate; this only
    handles the framing. ``ValueError`` is raised if no object is found or the
    braces never balance.
    """
    if not text:
        raise ValueError("Empty response — nothing to parse")
    if len(text) > _MAX_JSON_BYTES:
        raise ValueError(f"Response too large for JSON extraction ({len(text)} bytes)")

    start = text.find("{")
    if start < 0:
        raise ValueError("No '{' found in response")

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if escape:
            escape = False
            continue
        if c == "\\":
            escape = True
            continue
        if c == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise ValueError(f"Unbalanced braces (depth={depth}) — response was likely truncated")


def _extract_json(text: str, _depth: int = 0) -> str:
    """Pull a JSON object out of an LLM response that may have prose around it."""
    if _depth > _MAX_JSON_RECURSION:
        raise ValueError("Exceeded JSON extraction recursion depth")
    body = _strip_reasoning(text)
    fence = _CODE_FENCE.search(body)
    if fence:
        return _extract_json(fence.group(1), _depth + 1)
    return _carve_json_object(body)


# ---------------------------------------------------------------------------
# Provider quirks
# ---------------------------------------------------------------------------


def _supports_temperature(model_qualified: str) -> bool:
    """Some OpenAI gpt-5 variants reject any temperature except 1."""
    return "gpt-5" not in model_qualified.lower()


def _reasoning_effort(model_qualified: str) -> str | None:
    """gpt-5 family returns empty message.content unless reasoning_effort is set."""
    if "gpt-5" in model_qualified.lower():
        return "minimal"
    return None


def _supports_native_json(model_qualified: str) -> bool:
    """Use LiteLLM's registry to decide whether ``response_format`` is safe.

    Ollama models are rarely in the registry but support JSON mode natively
    via the ``format`` parameter, which LiteLLM handles when ``response_format``
    is passed — so we whitelist them.
    """
    if model_qualified.startswith(("ollama/", "ollama_chat/")):
        return True
    try:
        info = litellm.get_model_info(model=model_qualified)
        return "response_format" in info.get("supported_openai_params", [])
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


async def complete(
    prompt: str,
    system_prompt: str | None = None,
    config: LLMConfig | None = None,
    max_tokens: int = 4096,
    temperature: float = 0.7,
) -> str:
    """Make a single completion call. Returns the assistant's text response.

    Transport retries (timeouts, 429, 5xx) happen inside the router. This
    function does not retry on content-quality issues — callers needing
    structure should use :func:`complete_json` instead.
    """
    if config is None:
        raise ValueError("complete() requires an explicit LLMConfig")
    router = _get_router(config)
    qualified = _qualify_model(config)

    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    kwargs: dict[str, Any] = {
        "model": "active",
        "messages": messages,
        "max_tokens": max_tokens,
        "timeout": _TIMEOUT_TEXT_SECONDS,
    }
    if _supports_temperature(qualified):
        kwargs["temperature"] = temperature
    effort = _reasoning_effort(qualified)
    if effort:
        kwargs["reasoning_effort"] = effort

    try:
        response = await router.acompletion(**kwargs)
    except Exception as exc:
        # Log details server-side; surface a generic error to keep secrets out
        # of any user-visible context.
        _log.exception("LLM text completion failed (model=%s)", qualified)
        raise ValueError("LLM completion failed. Check provider configuration and try again.") from exc

    text = _response_text(response)
    if not text:
        raise ValueError("LLM returned an empty response")
    cleaned = _strip_reasoning(text)
    if not cleaned:
        raise ValueError("LLM response contained only reasoning content")
    return cleaned


async def complete_json(
    prompt: str,
    system_prompt: str | None = None,
    config: LLMConfig | None = None,
    max_tokens: int = 4096,
    retries: int = 2,
) -> dict[str, Any]:
    """Completion call that returns a parsed JSON object.

    The router handles transport retries internally; *retries* here only
    governs content-quality retries (malformed JSON, missing braces, empty
    response). Each attempt nudges the prompt to be stricter about format
    and increases temperature slightly to break the model out of a bad rut.
    """
    if config is None:
        raise ValueError("complete_json() requires an explicit LLMConfig")
    router = _get_router(config)
    qualified = _qualify_model(config)
    use_native_json = _supports_native_json(qualified)

    sys_msg = (system_prompt or "").rstrip()
    sys_msg += "\n\nReturn a single valid JSON object only. No prose, no markdown fences."
    messages: list[dict[str, str]] = [
        {"role": "system", "content": sys_msg.lstrip()},
        {"role": "user", "content": prompt},
    ]

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        kwargs: dict[str, Any] = {
            "model": "active",
            "messages": messages,
            "max_tokens": max_tokens,
            "timeout": _TIMEOUT_JSON_SECONDS,
        }
        if _supports_temperature(qualified):
            # Start cool, warm up on retries — a different sampling path is
            # often enough to escape a malformed-output rut.
            kwargs["temperature"] = [0.1, 0.4, 0.7][min(attempt, 2)]
        effort = _reasoning_effort(qualified)
        if effort:
            kwargs["reasoning_effort"] = effort
        if use_native_json:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            response = await router.acompletion(**kwargs)
        except Exception as exc:
            _log.exception("LLM JSON completion failed (model=%s, attempt=%d)", qualified, attempt + 1)
            raise ValueError("LLM completion failed. Check provider configuration and try again.") from exc

        text = _response_text(response)
        if not text:
            last_error = ValueError("Empty response")
            messages[-1] = {"role": "user", "content": prompt + "\n\nReturn ONLY a JSON object now."}
            await asyncio.sleep(0)  # cooperative yield between retries
            continue

        try:
            return json.loads(_extract_json(text))
        except (json.JSONDecodeError, ValueError) as exc:
            last_error = exc
            _log.warning("JSON parse failed (attempt %d/%d): %s", attempt + 1, retries + 1, exc)
            messages[-1] = {
                "role": "user",
                "content": prompt + "\n\nReturn ONLY a valid JSON object. Start with { and end with }.",
            }
            await asyncio.sleep(0)

    raise ValueError(f"Failed to parse JSON after {retries + 1} attempts: {last_error}")
