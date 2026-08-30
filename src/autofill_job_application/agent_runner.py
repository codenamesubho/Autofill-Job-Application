"""The single execution path: a browser-use Agent per job URL.

The agent navigates, finds the Apply control, opens the form and reports the
questions as validated structured output.

Safety note. An autonomous agent chooses its own clicks, so safety cannot live in
a gate the agent calls — it lives in what the agent is *capable of*. `EXCLUDED`
removes every write-capable action from the registry before the agent is built,
so there is no tool by which it can type, upload, choose, or press a key. A form
it cannot fill is a form it cannot meaningfully submit; the CDP guard then blocks
the submission act itself.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from browser_use import Agent, Tools

from .guard import guard_status, inject_into_all_targets
from .models import (
    AgentFormSnapshot,
    JobSnapshot,
    Outcome,
    agent_question_to_question,
)

#: Actions removed from the registry. Everything that could write to the page,
#: touch the filesystem, or run arbitrary script.
EXCLUDED_ACTIONS = [
    "input",  # types into fields
    "send_keys",  # Enter would submit
    "select_dropdown",  # writes a choice (dropdown_options still *reads* them)
    "upload_file",
    "write_file",
    "replace_file",
    "read_file",
    "save_as_pdf",
    "evaluate",  # arbitrary JS, could call form.submit()
    "search",  # web search; irrelevant and a distraction
]

#: Kept deliberately: the agent still needs to move and to look.
EXPECTED_KEPT = [
    "navigate",
    "click",
    "scroll",
    "extract",
    "find_elements",
    "find_text",
    "search_page",
    "dropdown_options",
    "wait",
    "done",
]

TASK_TEMPLATE = """\
You are cataloguing a job application form. You are NOT applying.

Job URL: {url}

Steps:
1. The page is already open. If it is a job description rather than a form, find
   and click the control that opens the application ("Apply", "Apply now",
   "Start application"). Follow at most 2 such steps.
2. If the form is behind a login or account wall, stop and report
   outcome="login_wall".
3. Once the application form is visible, scroll through the WHOLE form and record
   every question it asks, top to bottom. Include optional ones, consent
   checkboxes, and voluntary self-identification / EEO questions.
4. For choice questions, use the dropdown_options action to READ the available
   choices. Never pick one.
5. Call done with the structured result.

Absolute rules:
- Do NOT type anything into any field.
- Do NOT upload any file.
- Do NOT select, tick, or choose any value.
- Do NOT click Submit, Send application, Finish, or anything that would file the
  application. Your job ends when the questions are catalogued.

For each question report: the visible label, the widget kind
(text|textarea|email|tel|number|date|url|file|select|combobox|radio|checkbox),
whether it is required, any visible choices, help text, and the section heading
it sits under.

Set outcome to one of: form_open, login_wall, no_apply_control_found, error.
"""

_OUTCOME_MAP = {
    "form_open": Outcome.FORM_OPEN,
    "login_wall": Outcome.LOGIN_WALL,
    "no_apply_control_found": Outcome.NO_APPLY_CONTROL_FOUND,
    "max_hops_exceeded": Outcome.MAX_HOPS_EXCEEDED,
    "error": Outcome.NAVIGATION_ERROR,
}


def build_tools() -> Tools:
    """Registry with every write-capable action removed."""
    return Tools(exclude_actions=EXCLUDED_ACTIONS)


def build_llm(model: str = "claude-opus-5"):
    """Construct the chat model, or raise a useful error if no key is present.

    Deferred import: `cli --help` and the offline test suite must not require an
    API key or an SDK client to be constructible.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. The agent needs an LLM for every run.\n"
            "Put it in a .env file next to pyproject.toml, or export it:\n"
            "    export ANTHROPIC_API_KEY=sk-ant-..."
        )
    from browser_use import ChatAnthropic

    return ChatAnthropic(model=model)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def snapshot_one(
    session,
    url: str,
    *,
    llm=None,
    max_steps: int = 25,
    model: str = "claude-opus-5",
) -> JobSnapshot:
    """Catalogue one job URL. Never raises — a failure becomes an artifact entry."""
    snap = JobSnapshot(input_url=url, hops=[url], timestamp=_now())
    try:
        llm = llm or build_llm(model)
        agent = Agent(
            task=TASK_TEMPLATE.format(url=url),
            llm=llm,
            browser_session=session,
            tools=build_tools(),
            output_model_schema=AgentFormSnapshot,
            initial_actions=[{"navigate": {"url": url}}],
            use_vision=False,
            max_failures=3,
            enable_planning=False,
            calculate_cost=True,
        )
        history = await agent.run(max_steps=max_steps)

        # A cross-origin form frame is a new CDP target and does not inherit the
        # parent's init script, so re-arm after the agent has navigated.
        try:
            await inject_into_all_targets(session)
        except Exception:
            pass

        result: AgentFormSnapshot | None = history.structured_output
        if result is None:
            snap.outcome = Outcome.NAVIGATION_ERROR
            snap.error = "agent returned no structured output"
            snap.raw_result = history.final_result()
            return snap

        snap.outcome = _OUTCOME_MAP.get(result.outcome, Outcome.NAVIGATION_ERROR)
        snap.final_url = result.final_url or url
        snap.questions = [agent_question_to_question(q) for q in result.questions]
        snap.tier2 = {
            "ran": True,
            "model": model,
            "steps": len(history.history),
            "notes": result.notes,
        }
    except Exception as exc:  # one bad URL must not end the batch
        snap.outcome = Outcome.NAVIGATION_ERROR
        snap.error = f"{type(exc).__name__}: {exc}"

    try:
        snap.guard = await guard_status(session)
    except Exception:
        pass
    return snap
