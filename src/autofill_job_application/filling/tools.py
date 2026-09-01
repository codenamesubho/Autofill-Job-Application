"""Wires filling.click_gate into a browser_use.Tools registry.

Reaches into non-public browser_use internals to re-register the 'click'
action with the phase-2 gate checked first: `Tools._register_click_action`
(tools/service.py:2110) registers 'click' the same way — delete any existing
entry, re-register via `self.registry.action(...)`. Verified directly against
the installed browser_use==0.13.8. This is delicate: it isn't public API and
could change on a minor version bump. pyproject.toml pins browser-use
narrowly (>=0.13.8,<0.14) specifically because of this dependency, and
tests/test_filling_click_gate_wiring.py exists to fail loudly, not silently,
if this mechanism ever breaks.

agent_runner.build_tools(allow_fill=True) and this module have separate jobs,
deliberately not merged: agent_runner owns which actions *exist* at all
(input/select_dropdown re-enabled, everything else still excluded); this
module owns which *clicks* are permitted once click exists as an action. Two
independent decisions, so a bug in one can't silently widen the other.
"""

from __future__ import annotations

from browser_use import ActionResult, Tools
from browser_use.tools.views import ClickElementActionIndexOnly

from . import click_gate


def build_fill_tools():
    """The residual fill agent's tool registry: agent_runner's write-capable
    subset, plus the phase-2 click gate wired onto 'click'."""
    from ..agent_runner import build_tools

    tools = build_tools(allow_fill=True)
    gate_click_action(tools)
    return tools


def gate_click_action(tools: Tools) -> None:
    """Re-register 'click' on an existing Tools instance so every click first
    goes through click_gate.decide(). Split out from build_fill_tools() so a
    test can call it directly against a plain Tools() without needing the
    agent_runner exclusion layer too.
    """
    if "click" in tools.registry.registry.actions:
        del tools.registry.registry.actions["click"]

    @tools.registry.action(
        "Click element by index. Structural clicks only (add a row, next "
        "page, expand a section) — anything submit/finish-shaped is refused "
        "before the click happens; this tool never submits an application.",
        param_model=ClickElementActionIndexOnly,
    )
    async def click(params: ClickElementActionIndexOnly, browser_session):
        node = await browser_session.get_element_by_index(params.index)
        if node is None:
            # Let the normal "index not available" handling report this —
            # nothing to gate if there's no element to click.
            return await tools._click_by_index(params, browser_session)

        name = (
            (node.ax_node.name if node.ax_node else None)
            or node.attributes.get("aria-label")
            or node.attributes.get("value")
        )
        decision = click_gate.decide(
            name,
            tag=node.tag_name,
            input_type=node.attributes.get("type"),
        )
        if decision == "DENY":
            return ActionResult(
                error=(
                    f"Click on {name!r} was refused: this looks like a "
                    "submit/finish control. This tool never submits an "
                    "application — fill the remaining fields instead."
                )
            )
        return await tools._click_by_index(params, browser_session)
