"""Live end-to-end: one real job URL must reach an open form with questions.

Needs real Chrome AND an LLM key. Skipped otherwise.

    export AUTOFILL_LLM_API_KEY=sk-or-v1-...
    export AUTOFILL_LLM_MODEL=anthropic/claude-opus-5
    pytest -m live tests/live/test_end_to_end.py

Not run during development — no key was available in that session.
"""

import os

import pytest

from autofill_job_application.agent_runner import snapshot_one
from autofill_job_application.browser import start_session
from autofill_job_application.models import Outcome

pytestmark = [
    pytest.mark.live,
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.environ.get("AUTOFILL_LLM_API_KEY"),
        reason="agent version needs an LLM key for every run",
    ),
]

#: A live Greenhouse posting whose form is served in the top frame. Replace if it
#: closes; any public posting with a visible application form will do.
JOB_URL = "https://job-boards.greenhouse.io/gitlab/jobs/8705017002"


async def test_reaches_form_and_reports_questions():
    session = await start_session(headless=True)
    try:
        snap = await snapshot_one(session, JOB_URL, max_steps=25)
    finally:
        await session.kill()

    assert snap.outcome is Outcome.FORM_OPEN, f"{snap.outcome}: {snap.error}"
    assert len(snap.questions) >= 5, [q.label for q in snap.questions]

    labels = " ".join(q.label.lower() for q in snap.questions)
    assert "name" in labels
    assert "email" in labels

    # The run must never have needed the guard. If it did, the agent tried to
    # submit and the prompt/exclusions are not holding.
    assert snap.guard.get("blocked", 0) == 0, snap.guard
