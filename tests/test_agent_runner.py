"""snapshot_one's own logic, isolated from a real browser-use Agent.

A real Agent needs Chrome and an LLM key, so these tests stub it out with a fake
that mimics only the surface snapshot_one touches: construction kwargs and an
async .run(max_steps=...).
"""

import asyncio

import pytest

from autofill_job_application import agent_runner
from autofill_job_application.llm import LLMConfig
from autofill_job_application.models import Outcome


class _HangingAgent:
    """Stands in for browser_use.Agent: run() never returns on its own."""

    def __init__(self, **kwargs):
        pass

    async def run(self, max_steps: int):
        await asyncio.sleep(3600)


@pytest.fixture
def fake_config():
    return LLMConfig(provider="openrouter", model="test/model", api_key="k")


@pytest.mark.asyncio
async def test_job_timeout_is_recorded_as_a_failed_job_not_a_hang(monkeypatch, fake_config):
    """A job that never finishes must not block the rest of the batch."""
    monkeypatch.setattr(agent_runner, "Agent", _HangingAgent)

    snap = await agent_runner.snapshot_one(
        session=None,
        url="https://example.test/job",
        llm=object(),
        config=fake_config,
        job_timeout=0.05,
    )

    assert snap.outcome == Outcome.NAVIGATION_ERROR
    assert "timed out" in snap.error
    assert "0s" in snap.error or "0" in snap.error


@pytest.mark.asyncio
async def test_job_timeout_default_is_generous(fake_config):
    """The default must not be so tight that a normal multi-step run trips it."""
    assert agent_runner.DEFAULT_JOB_TIMEOUT >= 120
