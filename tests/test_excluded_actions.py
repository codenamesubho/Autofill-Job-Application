"""The safety test for the agent version.

The agent decides its own clicks, so safety rests on what it is *capable* of.
This test builds the real registry and asserts the write-capable actions are
genuinely gone — not that a list constant contains the right strings.

If this fails, the agent can type into and submit a real job application.
"""

import pytest

from autofill_job_application.agent_runner import (
    EXCLUDED_ACTIONS,
    EXPECTED_KEPT,
    build_tools,
)


@pytest.fixture(scope="module")
def registered():
    return set(build_tools().registry.registry.actions.keys())


@pytest.mark.parametrize("action", EXCLUDED_ACTIONS)
def test_write_capable_actions_are_absent(action, registered):
    assert action not in registered, (
        f"{action!r} is still registered — the agent can use it. "
        "This is the control that stops it filling or submitting a form."
    )


@pytest.mark.parametrize(
    "action", ["input", "send_keys", "select_dropdown", "upload_file", "evaluate"]
)
def test_the_dangerous_four_specifically(action, registered):
    """Named explicitly so a careless edit to EXCLUDED_ACTIONS cannot silently
    re-enable them — this test does not read that list."""
    assert action not in registered


@pytest.mark.parametrize("action", EXPECTED_KEPT)
def test_navigation_and_reading_still_available(action, registered):
    assert action in registered, f"{action!r} is needed to reach and read the form"


def test_can_read_dropdown_choices_but_not_select_one(registered):
    """The distinction the whole approach depends on: reading options is allowed,
    choosing one is not."""
    assert "dropdown_options" in registered
    assert "select_dropdown" not in registered


def test_default_registry_would_be_unsafe():
    """Control case: the exclusions are doing real work, not describing a
    registry that never had these actions."""
    from browser_use import Tools

    default = set(Tools().registry.registry.actions.keys())
    for action in ("input", "send_keys", "select_dropdown", "upload_file"):
        assert action in default
