"""Proves filling.tools.gate_click_action actually gates the real, registered
'click' action on a browser_use.Tools instance — not just that click_gate.decide()
returns the right verdict in isolation (that's tests/test_filling_click_gate.py).

This is the fragile, non-public-API-dependent part of the design (see
filling/tools.py's module docstring): it must fail loudly if a future
browser-use upgrade changes how Tools registers 'click', rather than silently
leaving every click ungated.
"""

import pytest

from autofill_job_application.filling.tools import build_fill_tools
from browser_use.tools.views import ClickElementActionIndexOnly


class _FakeAXNode:
    def __init__(self, name):
        self.name = name


class _FakeNode:
    def __init__(self, name, *, tag_name="button", input_type=None):
        self.ax_node = _FakeAXNode(name)
        self.tag_name = tag_name
        self.attributes = {"type": input_type} if input_type else {}


class _FakeSession:
    def __init__(self, node):
        self._node = node

    async def get_element_by_index(self, index):
        return self._node


@pytest.fixture(scope="module")
def click_fn():
    """The actual registered closure, called the same way browser_use's own
    dispatch calls it — confirmed experimentally: fn(params=..., browser_session=...).
    """
    tools = build_fill_tools()
    return tools.registry.registry.actions["click"].function


@pytest.mark.asyncio
async def test_submit_shaped_click_is_refused_before_reaching_browser_use(click_fn):
    session = _FakeSession(_FakeNode("Submit Application", input_type="submit"))
    result = await click_fn(params=ClickElementActionIndexOnly(index=1), browser_session=session)
    assert result.error is not None
    assert "was refused" in result.error
    assert "submit" in result.error.lower()


@pytest.mark.asyncio
async def test_structural_click_is_delegated_to_the_real_click_handler(click_fn):
    """A structural click must NOT be short-circuited by the gate — it should
    reach browser_use's own _click_by_index, which then fails for an unrelated
    reason against our fake node (proving delegation happened, not denial)."""
    session = _FakeSession(_FakeNode("Add another employment entry", input_type=None))
    result = await click_fn(params=ClickElementActionIndexOnly(index=1), browser_session=session)
    assert result.error is not None
    assert "was refused" not in result.error


@pytest.mark.asyncio
async def test_missing_element_is_not_treated_as_a_gate_decision(click_fn):
    """No node at that index (page changed) is browser_use's own concern, not
    ours — must not be misreported as a gate refusal."""
    session = _FakeSession(None)
    result = await click_fn(params=ClickElementActionIndexOnly(index=1), browser_session=session)
    assert "was refused" not in (result.error or "") + (result.extracted_content or "")
