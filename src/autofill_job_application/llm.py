"""Chat-model construction. The only place a provider is named.

Configured by two environment variables — a key and a model — so switching model
or vendor never requires editing code:

    AUTOFILL_LLM_API_KEY=sk-or-v1-...
    AUTOFILL_LLM_MODEL=anthropic/claude-opus-5

Routing defaults to OpenRouter, which reaches every major model through one key
and one OpenAI-compatible endpoint, so no vendor SDK beyond `openai` is needed.
`AUTOFILL_LLM_PROVIDER` switches backends for anyone who would rather talk to a
provider directly, or through LiteLLM.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

ENV_API_KEY = "AUTOFILL_LLM_API_KEY"
ENV_MODEL = "AUTOFILL_LLM_MODEL"
ENV_PROVIDER = "AUTOFILL_LLM_PROVIDER"
ENV_BASE_URL = "AUTOFILL_LLM_BASE_URL"

DEFAULT_PROVIDER = "openrouter"

#: provider name -> browser-use class name. Imported lazily, so selecting one
#: backend never requires another's SDK to be installed.
PROVIDERS: dict[str, str] = {
    "openrouter": "ChatOpenRouter",
    "litellm": "ChatLiteLLM",
    "anthropic": "ChatAnthropic",
    "openai": "ChatOpenAI",
    "groq": "ChatGroq",
    "google": "ChatGoogle",
}

#: Providers whose constructor accepts an explicit endpoint override.
_SUPPORTS_BASE_URL = {"openrouter", "openai", "groq", "litellm"}

EXAMPLE = (
    f"    {ENV_API_KEY}=sk-or-v1-...\n"
    f"    {ENV_MODEL}=anthropic/claude-opus-5"
)


class LLMConfigError(RuntimeError):
    """Configuration is missing or wrong. Message is shown directly to the user."""


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    model: str
    api_key: str
    base_url: str | None = None

    def describe(self) -> str:
        return f"{self.provider}:{self.model}"


def resolve_config(
    model: str | None = None,
    provider: str | None = None,
    *,
    env: dict | None = None,
) -> LLMConfig:
    """Merge CLI arguments over environment variables and validate the result.

    Explicit arguments win; the environment fills the rest. Nothing is guessed —
    a missing key or model is an error with instructions, not a silent default.
    """
    env = os.environ if env is None else env

    provider = (provider or env.get(ENV_PROVIDER) or DEFAULT_PROVIDER).strip().lower()
    if provider not in PROVIDERS:
        raise LLMConfigError(
            f"Unknown LLM provider {provider!r}.\n"
            f"Valid values for {ENV_PROVIDER}: {', '.join(sorted(PROVIDERS))}"
        )

    api_key = (env.get(ENV_API_KEY) or "").strip()
    if not api_key:
        raise LLMConfigError(
            f"{ENV_API_KEY} is not set. The agent needs an LLM for every run.\n"
            f"Add it to a .env file next to pyproject.toml, or export it:\n{EXAMPLE}"
        )

    model = (model or env.get(ENV_MODEL) or "").strip()
    if not model:
        raise LLMConfigError(
            f"{ENV_MODEL} is not set, and no --model was given.\n"
            f"For the default {DEFAULT_PROVIDER} provider use a provider/model "
            f"string, e.g. 'anthropic/claude-opus-5' or 'openai/gpt-4o':\n{EXAMPLE}"
        )

    base_url = (env.get(ENV_BASE_URL) or "").strip() or None
    return LLMConfig(provider=provider, model=model, api_key=api_key, base_url=base_url)


def build_llm(config: LLMConfig | None = None, **kwargs):
    """Construct the browser-use chat model for `config`.

    Constructing a client performs no network I/O, so this is safe to call in
    tests with a dummy key.
    """
    config = config or resolve_config(**kwargs)
    class_name = PROVIDERS[config.provider]

    # ChatLiteLLM constructs happily without litellm installed and only fails on
    # the first call — which would be after Chrome has launched. Check now.
    if config.provider == "litellm":
        try:
            import litellm  # noqa: F401
        except ImportError as exc:
            raise LLMConfigError(
                "Provider 'litellm' needs the optional litellm package:\n"
                "    pip install litellm\n"
                f"Or use the default {DEFAULT_PROVIDER} provider, which needs no extra install."
            ) from exc

    try:
        import browser_use

        chat_class = getattr(browser_use, class_name)
    except (ImportError, AttributeError) as exc:
        hint = (
            "\nLiteLLM is an optional extra: pip install litellm"
            if config.provider == "litellm"
            else ""
        )
        raise LLMConfigError(
            f"Could not load {class_name} for provider {config.provider!r}: {exc}{hint}"
        ) from exc

    params = {"model": config.model, "api_key": config.api_key}
    if config.base_url and config.provider in _SUPPORTS_BASE_URL:
        # The two wrappers spell the endpoint differently.
        params["api_base" if config.provider == "litellm" else "base_url"] = config.base_url

    try:
        return chat_class(**params)
    except TypeError as exc:
        raise LLMConfigError(
            f"{class_name} rejected the given options ({exc}). "
            f"If you set {ENV_BASE_URL}, this provider may not accept it."
        ) from exc
    except Exception as exc:
        hint = (
            "\nLiteLLM is an optional extra: pip install litellm"
            if config.provider == "litellm"
            else ""
        )
        raise LLMConfigError(
            f"Could not construct {class_name} for {config.describe()}: {exc}{hint}"
        ) from exc
