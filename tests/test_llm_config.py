"""Provider/model configuration. Offline: no key, no network, no browser.

Constructing a chat client performs no network I/O, so these build real objects
with a dummy key — they just never call them.
"""

import pytest

from autofill_job_application.llm import (
    DEFAULT_PROVIDER,
    ENV_API_KEY,
    ENV_BASE_URL,
    ENV_MODEL,
    ENV_PROVIDER,
    PROVIDERS,
    LLMConfig,
    LLMConfigError,
    build_llm,
    resolve_config,
)

FULL_ENV = {ENV_API_KEY: "sk-test-123", ENV_MODEL: "anthropic/claude-opus-5"}


# --- resolution ----------------------------------------------------------


def test_two_variables_are_enough():
    cfg = resolve_config(env=dict(FULL_ENV))
    assert cfg.api_key == "sk-test-123"
    assert cfg.model == "anthropic/claude-opus-5"
    assert cfg.provider == DEFAULT_PROVIDER == "openrouter"
    assert cfg.base_url is None


def test_provider_can_be_overridden_by_env():
    cfg = resolve_config(env={**FULL_ENV, ENV_PROVIDER: "anthropic"})
    assert cfg.provider == "anthropic"


def test_explicit_arguments_beat_the_environment():
    cfg = resolve_config(
        model="openai/gpt-4o", provider="openai", env={**FULL_ENV, ENV_PROVIDER: "groq"}
    )
    assert cfg.provider == "openai"
    assert cfg.model == "openai/gpt-4o"


def test_provider_is_case_and_space_insensitive():
    assert resolve_config(env={**FULL_ENV, ENV_PROVIDER: "  OpenAI "}).provider == "openai"


def test_base_url_is_optional_passthrough():
    cfg = resolve_config(env={**FULL_ENV, ENV_BASE_URL: "https://gw.internal/v1"})
    assert cfg.base_url == "https://gw.internal/v1"


def test_blank_values_count_as_missing():
    with pytest.raises(LLMConfigError):
        resolve_config(env={ENV_API_KEY: "   ", ENV_MODEL: "m"})
    with pytest.raises(LLMConfigError):
        resolve_config(env={ENV_API_KEY: "k", ENV_MODEL: "  "})


# --- error paths must tell the user what to do ---------------------------


def test_missing_key_names_the_variable():
    with pytest.raises(LLMConfigError) as e:
        resolve_config(env={ENV_MODEL: "anthropic/claude-opus-5"})
    assert ENV_API_KEY in str(e.value)
    assert ".env" in str(e.value)


def test_missing_model_names_the_variable_and_shows_an_example():
    with pytest.raises(LLMConfigError) as e:
        resolve_config(env={ENV_API_KEY: "k"})
    msg = str(e.value)
    assert ENV_MODEL in msg
    assert "/" in msg  # a provider/model example is shown


def test_unknown_provider_lists_the_valid_ones():
    with pytest.raises(LLMConfigError) as e:
        resolve_config(env={**FULL_ENV, ENV_PROVIDER: "hal9000"})
    msg = str(e.value)
    assert "hal9000" in msg
    for name in PROVIDERS:
        assert name in msg


# --- construction --------------------------------------------------------


@pytest.mark.parametrize("provider", ["openrouter", "openai", "anthropic", "groq"])
def test_builds_a_client_carrying_the_requested_model(provider):
    """Providers whose SDKs are installed must construct without network I/O."""
    llm = build_llm(LLMConfig(provider=provider, model="some/model-x", api_key="k"))
    assert getattr(llm, "model", None) == "some/model-x"


def test_switching_provider_needs_no_code_change():
    a = build_llm(LLMConfig(provider="openrouter", model="m", api_key="k"))
    b = build_llm(LLMConfig(provider="openai", model="m", api_key="k"))
    assert type(a) is not type(b)


def test_litellm_missing_dependency_is_explained():
    """litellm is an optional extra; if absent, say so rather than traceback."""
    pytest.importorskip  # no-op guard for readability
    try:
        import litellm  # noqa: F401
    except ImportError:
        with pytest.raises(LLMConfigError) as e:
            build_llm(LLMConfig(provider="litellm", model="m", api_key="k"))
        assert "pip install litellm" in str(e.value)
    else:
        assert build_llm(LLMConfig(provider="litellm", model="m", api_key="k"))


def test_describe_is_readable():
    assert LLMConfig("openrouter", "anthropic/claude-opus-5", "k").describe() == (
        "openrouter:anthropic/claude-opus-5"
    )
