"""filling.runner.fill_job's batch-loop logic, isolated from a real browser,
LLM, and CDP session — mirrors tests/test_agent_runner.py's monkeypatch
technique (a real Agent needs Chrome and an LLM key; these tests stub every
boundary fill_job touches).
"""

import asyncio

import pytest

from autofill_job_application.answering.models import Answer, AnswerSource, JobAnswers
from autofill_job_application.filling import runner as filling_runner
from autofill_job_application.filling.models import FieldFillResult, WritePath, WriteStatus
from autofill_job_application.llm import LLMConfig
from autofill_job_application.models import ElementRef, JobSnapshot, Outcome, Question, WidgetType


class _FastAgent:
    """Stands in for browser_use.Agent in a residual turn: completes
    immediately, never actually touches a browser."""

    def __init__(self, **kwargs):
        pass

    async def run(self, max_steps: int):
        return None


class _HangingExtract:
    """Stands in for extract_tier1 when a test wants the whole job to time out."""

    async def __call__(self, session):
        await asyncio.sleep(3600)


def _question(identity_suffix: str, widget: WidgetType = WidgetType.TEXT) -> Question:
    return Question(
        ref=ElementRef(frame_url="https://ats.test", css_selector=f"#field-{identity_suffix}"),
        label=f"Field {identity_suffix}",
        widget=widget,
    )


async def _answer_all(snapshot: JobSnapshot, context, *, llm=None, config=None) -> JobAnswers:
    return JobAnswers(
        input_url=snapshot.input_url,
        answers=[
            Answer(
                question_index=i,
                question_label=q.label,
                value="United States",
                source=AnswerSource.CONTEXT_DOC,
            )
            for i, q in enumerate(snapshot.questions)
        ],
    )


async def _escalate_all(snapshot: JobSnapshot, context, *, llm=None, config=None) -> JobAnswers:
    return JobAnswers(
        input_url=snapshot.input_url,
        answers=[
            Answer(
                question_index=i,
                question_label=q.label,
                source=AnswerSource.ESCALATED,
                escalation_reason="test: always escalate",
            )
            for i, q in enumerate(snapshot.questions)
        ],
    )


async def _fake_snapshot_one_form_open(session, url, *, llm=None, max_steps=25, config=None):
    return JobSnapshot(input_url=url, outcome=Outcome.FORM_OPEN)


@pytest.fixture
def fake_config():
    return LLMConfig(provider="openrouter", model="test/model", api_key="k")


@pytest.fixture(autouse=True)
def _patch_common(monkeypatch):
    """Boundaries every test needs stubbed: phase-1 navigation, the residual
    Agent, and the Candidate Agent's answer_job (parametrized per test where
    the exact answers matter)."""
    monkeypatch.setattr(filling_runner, "snapshot_one", _fake_snapshot_one_form_open)
    monkeypatch.setattr(filling_runner, "Agent", _FastAgent)


@pytest.mark.asyncio
async def test_loop_stops_after_two_consecutive_empty_extractions(monkeypatch, fake_config):
    """One real question, then the extractor keeps reporting the same
    (already-seen) question — nothing new — until the stop condition fires."""
    q = _question("a")
    monkeypatch.setattr(filling_runner, "extract_tier1", lambda session: _return([q]))
    monkeypatch.setattr(filling_runner, "answer_job", _escalate_all)

    result = await filling_runner.fill_job(
        session=None,
        url="https://ats.test/job",
        context="about me",
        llm=object(),
        config=fake_config,
        max_batches=10,
        job_timeout=5,
        batch_timeout=1,
    )

    assert result.error is None
    assert result.batches_run == filling_runner._STOP_AFTER_CONSECUTIVE_EMPTY + 1
    assert len(result.fields) == 1
    assert result.fields[0].write_status is WriteStatus.ESCALATED


@pytest.mark.asyncio
async def test_max_batches_caps_a_form_that_always_has_something_new(monkeypatch, fake_config):
    """Without max_batches, a form that reveals one new field every batch
    would never stop. This is the independent safety net for that case."""
    counter = {"n": 0}

    async def _ever_new(session):
        counter["n"] += 1
        return [_question(str(counter["n"]))]

    monkeypatch.setattr(filling_runner, "extract_tier1", _ever_new)
    monkeypatch.setattr(filling_runner, "answer_job", _escalate_all)

    result = await filling_runner.fill_job(
        session=None,
        url="https://ats.test/job",
        context="about me",
        llm=object(),
        config=fake_config,
        max_batches=3,
        job_timeout=5,
        batch_timeout=1,
    )

    assert result.error is None
    assert result.batches_run == 3
    assert len(result.fields) == 3  # one new escalated field per batch


@pytest.mark.asyncio
async def test_job_timeout_is_recorded_as_a_failed_job_not_a_hang(monkeypatch, fake_config):
    """A job that never finishes must not block the rest of the batch run,
    same property agent_runner.snapshot_one already guarantees per URL."""
    monkeypatch.setattr(filling_runner, "extract_tier1", _HangingExtract())

    result = await filling_runner.fill_job(
        session=None,
        url="https://ats.test/job",
        context="about me",
        llm=object(),
        config=fake_config,
        job_timeout=0.05,
    )

    assert result.error is not None
    assert "timed out" in result.error


@pytest.mark.asyncio
async def test_form_never_reached_is_reported_not_silently_empty(monkeypatch, fake_config):
    async def _login_wall(session, url, *, llm=None, max_steps=25, config=None):
        return JobSnapshot(input_url=url, outcome=Outcome.LOGIN_WALL)

    monkeypatch.setattr(filling_runner, "snapshot_one", _login_wall)

    result = await filling_runner.fill_job(
        session=None,
        url="https://ats.test/job",
        context="about me",
        llm=object(),
        config=fake_config,
    )

    assert result.error is not None
    assert "login_wall" in result.error
    assert result.fields == []


@pytest.mark.asyncio
async def test_select_aria_success_stays_deterministic_no_residual_transcription(monkeypatch, fake_config):
    """A combobox dom_writer can fill by typing-to-filter must not also be
    handed to the residual agent — that would be redundant and risks a second,
    conflicting write."""
    q = _question("country", widget=WidgetType.SELECT_ARIA)
    monkeypatch.setattr(filling_runner, "extract_tier1", lambda session: _return([q]))
    monkeypatch.setattr(filling_runner, "answer_job", _answer_all)

    async def _write_value_succeeds(session, question, answer):
        return FieldFillResult(
            question_label=question.label,
            widget=question.widget,
            value_written=answer.value,
            path=WritePath.DETERMINISTIC,
            answer_source=answer.source,
            write_status=WriteStatus.WRITTEN,
        )

    monkeypatch.setattr(filling_runner.dom_writer, "write_value", _write_value_succeeds)

    result = await filling_runner.fill_job(
        session=None,
        url="https://ats.test/job",
        context="about me",
        llm=object(),
        config=fake_config,
        max_batches=1,
        job_timeout=5,
        batch_timeout=1,
    )

    assert result.error is None
    written = [f for f in result.fields if f.write_status is WriteStatus.WRITTEN]
    assert len(written) == 1
    assert written[0].path is WritePath.DETERMINISTIC
    assert written[0].value_written == "United States"


@pytest.mark.asyncio
async def test_select_aria_failure_falls_back_to_residual_agent(monkeypatch, fake_config):
    """Type-to-filter finding no confident match (a non-searchable widget, or
    genuinely no matching option) must not be reported as a final failure —
    it's a routing signal to try the residual agent instead, unlike every
    other widget type."""
    q = _question("country", widget=WidgetType.SELECT_ARIA)
    monkeypatch.setattr(filling_runner, "extract_tier1", lambda session: _return([q]))
    monkeypatch.setattr(filling_runner, "answer_job", _answer_all)

    async def _write_value_fails(session, question, answer):
        return FieldFillResult(
            question_label=question.label,
            widget=question.widget,
            path=WritePath.DETERMINISTIC,
            answer_source=answer.source,
            write_status=WriteStatus.FAILED,
            failure_reason="no option matched after filtering",
        )

    monkeypatch.setattr(filling_runner.dom_writer, "write_value", _write_value_fails)

    result = await filling_runner.fill_job(
        session=None,
        url="https://ats.test/job",
        context="about me",
        llm=object(),
        config=fake_config,
        max_batches=1,
        job_timeout=5,
        batch_timeout=1,
    )

    assert result.error is None
    assert len(result.fields) == 1
    field = result.fields[0]
    assert field.write_status is WriteStatus.WRITTEN
    assert field.path is WritePath.LLM_RESIDUAL


async def _return(value):
    return value
