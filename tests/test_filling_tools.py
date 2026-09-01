"""The safety test for the fill-agent's tool registry, mirroring
tests/test_excluded_actions.py's approach exactly: build the real
browser_use.Tools registry and assert against it, not against a list constant.

If this fails, either the snapshot agent gained write capability it shouldn't
have, or the fill agent has a write action beyond the two it's meant to.
"""

import pytest

from autofill_job_application.agent_runner import EXCLUDED_ACTIONS, FILL_ACTIONS, build_tools


@pytest.fixture(scope="module")
def default_registered():
    """build_tools() with no args — the snapshot agent's registry. Must be
    provably unchanged by the allow_fill parameter's existence."""
    return set(build_tools().registry.registry.actions.keys())


@pytest.fixture(scope="module")
def fill_registered():
    return set(build_tools(allow_fill=True).registry.registry.actions.keys())


def test_default_call_site_is_unaffected_by_allow_fill_existing(default_registered):
    """snapshot_one's call site is build_tools() with no args — adding the
    allow_fill parameter must not change its behavior."""
    for action in FILL_ACTIONS:
        assert action not in default_registered, (
            f"{action!r} leaked into the default (no-args) registry — "
            "the snapshot agent must stay write-incapable."
        )


@pytest.mark.parametrize("action", FILL_ACTIONS)
def test_fill_actions_are_present_when_allow_fill_is_true(action, fill_registered):
    assert action in fill_registered, (
        f"{action!r} should be re-enabled for the residual fill agent"
    )


@pytest.mark.parametrize(
    "action", [a for a in EXCLUDED_ACTIONS if a not in FILL_ACTIONS]
)
def test_everything_else_stays_excluded_even_with_allow_fill(action, fill_registered):
    """Only input/select_dropdown move. send_keys (Enter-submit), evaluate
    (arbitrary JS), upload_file (handled deterministically instead) and the
    rest must still be absent even for the fill agent."""
    assert action not in fill_registered, (
        f"{action!r} leaked into the fill-agent registry — it should only ever "
        f"gain {FILL_ACTIONS}, nothing else"
    )


def test_fill_actions_is_exactly_input_and_select_dropdown():
    """Pin the exact set so a future edit adding a third action here is a
    visible, deliberate diff, not a silent scope creep."""
    assert set(FILL_ACTIONS) == {"input", "select_dropdown"}
