"""
LLM provider resolution.

All model calls use the OpenAI-compatible chat API. LLM_PROVIDER picks the
endpoint + key; an explicit *_BASE_URL still overrides it (e.g. local vLLM).

  openrouter (default)  https://openrouter.ai/api/v1             OPENROUTER_API_KEY
  gemini                Google AI Studio OpenAI-compat endpoint  GEMINI_API_KEY
  custom                whatever *_BASE_URL points at            LLM_API_KEY (optional)
"""
from __future__ import annotations

from dataclasses import dataclass

from app.config import settings

_BASE_URLS = {
    "openrouter": "https://openrouter.ai/api/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "custom": "",
}

# (vision, planning) defaults when *_MODEL_NAME is empty
_DEFAULT_MODELS = {
    "openrouter": ("google/gemma-4-31b-it:free", "google/gemma-4-31b-it:free"),
    "gemini": ("gemini-3.5-flash-lite", "gemini-3.5-flash-lite"),
    "custom": ("llava:7b", "mistral:7b"),
}


@dataclass(frozen=True)
class Endpoint:
    base_url: str
    api_key: str
    model: str
    provider: str

    @property
    def configured(self) -> bool:
        if not self.base_url:
            return False
        # Hosted providers need a real key; custom/local endpoints may not.
        return self.provider == "custom" or bool(self.api_key)


def _provider() -> str:
    p = (settings.llm_provider or "openrouter").strip().lower()
    return p if p in _BASE_URLS else "custom"


def _api_key(provider: str) -> str:
    if settings.llm_api_key:
        return settings.llm_api_key
    if provider == "openrouter":
        return settings.openrouter_api_key
    if provider == "gemini":
        return settings.gemini_api_key
    return ""


def _endpoint(base_override: str, model_override: str, kind: int) -> Endpoint:
    provider = _provider()
    # An explicit base URL other than the provider's own means a custom endpoint
    if base_override and base_override.rstrip("/") != _BASE_URLS[provider].rstrip("/"):
        return Endpoint(
            base_url=base_override,
            api_key=settings.llm_api_key,
            model=model_override or _DEFAULT_MODELS["custom"][kind],
            provider="custom",
        )
    return Endpoint(
        base_url=_BASE_URLS[provider],
        api_key=_api_key(provider),
        model=model_override or _DEFAULT_MODELS[provider][kind],
        provider=provider,
    )


def vision_endpoint() -> Endpoint:
    return _endpoint(settings.vision_model_base_url, settings.vision_model_name, 0)


def planning_endpoint() -> Endpoint:
    return _endpoint(settings.planning_model_base_url, settings.planning_model_name, 1)


def text_endpoint() -> Endpoint | None:
    """Text LLM for smart clips / translation: planning provider, else local vLLM."""
    ep = planning_endpoint()
    if not ep.configured and settings.vllm_base_url:
        base = settings.vllm_base_url.rstrip("/")
        ep = Endpoint(
            base_url=base if base.endswith("/v1") else f"{base}/v1",
            api_key=settings.llm_api_key,
            model=settings.vllm_model,
            provider="custom",
        )
    elif ep.provider == "custom" and not ep.base_url.rstrip("/").endswith("/v1"):
        ep = Endpoint(f"{ep.base_url.rstrip('/')}/v1", ep.api_key, ep.model, ep.provider)
    return ep if ep.configured else None


def vision_input_mode() -> str:
    """'video' sends MP4 clips, 'frames' sends sampled JPEG frames."""
    mode = (settings.vision_input_mode or "auto").strip().lower()
    if mode in ("video", "frames"):
        return mode
    # Gemini's OpenAI-compat endpoint does not accept video_url parts
    return "frames" if vision_endpoint().provider == "gemini" else "video"


def make_client(ep: Endpoint):
    from openai import OpenAI
    headers = {}
    if ep.provider == "openrouter":
        headers = {"HTTP-Referer": settings.frontend_url, "X-Title": settings.app_name}
    return OpenAI(base_url=ep.base_url, api_key=ep.api_key or "not-needed", default_headers=headers)
