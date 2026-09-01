"""The batch-loop orchestrator: one job URL from raw page to filled form.

Phase 1 (reach the open form) reuses agent_runner.snapshot_one as-is — it
already does exactly "navigate, click Apply, report outcome" with zero write
tools, and there's no reason to duplicate that logic here. Its `questions`
field is discarded; only `outcome`/`final_url` matter to this module, because
phase 2 re-extracts deterministically rather than trusting the LLM's read.

Phase 2 (fill) loops: deterministic extraction (extract_tier1, no LLM) ->
the existing, unmodified Candidate Agent (answering.resolver.answer_job,
guardrails-first) -> deterministic writes for ordinary fields
(filling.dom_writer) -> one scoped, click-gated browser-use Agent turn for
whatever the deterministic pass couldn't handle (a custom combobox, an
unresolved selector, or a legitimate structural control like "Add another
entry"). Every write into that residual turn is a value the Candidate Agent
already vetted — the LLM never invents an answer, only transcribes one, and
its only other action is a single gated click per turn.

Always re-extract fresh every batch; never cache a resolved selector across
batches. A write that adds/removes DOM nodes (a "show more" toggle, a
file-upload chip) shifts the extractor's positional fallback selectors
(nth-of-type, dom_path) for siblings — ElementRef documents this fragility
explicitly. Re-extracting every batch is what makes that safe.
"""

from __future__ import annotations

import asyncio

from browser_use import Agent

from ..agent_runner import DEFAULT_JOB_TIMEOUT, snapshot_one
from ..answering.models import Answer, AnswerSource
from ..answering.resolver import answer_job
from ..extract.js_extractor import extract_tier1
from ..guard import guard_status
from ..llm import LLMConfig, resolve_config
from ..models import JobSnapshot, Outcome, Question, WidgetType
from . import dom_writer
from .models import FieldFillResult, JobFillResult, WritePath, WriteStatus
from .tools import build_fill_tools

#: Widgets that skip dom_writer entirely and go straight to the residual
#: agent — no selector to interact with, or no known interaction model at
#: all. FILE isn't here: it has its own non-answer-driven write path
#: (write_file) and is filtered out before answer_job is ever called —
#: asking a model to "answer" a file-upload question makes no sense.
#: SELECT_ARIA isn't here either, even though it's the residual agent's most
#: common job — dom_writer.write_value tries it first (type to filter, click
#: the matching option; see dom_writer.py), and only a FAILED result for it
#: routes to the residual agent below, not a FAILED result for anything else.
_RESIDUAL_WIDGETS = {WidgetType.UNKNOWN}

#: Wall-clock cap on one residual browser-use Agent turn. Independent of the
#: whole-job DEFAULT_JOB_TIMEOUT, same reasoning as agent_runner.snapshot_one:
#: one stuck turn must not consume the entire job's budget.
DEFAULT_BATCH_TIMEOUT = 120.0

#: Two consecutive extraction passes with nothing new — after giving the
#: residual turn a chance to reveal more via a structural click — means
#: there's genuinely nothing left to discover.
_STOP_AFTER_CONSECUTIVE_EMPTY = 2

#: Independent of the "nothing new" signal, so a form that keeps revealing
#: exactly one new field per scroll can't loop forever.
DEFAULT_MAX_BATCHES = 15

_RESIDUAL_TASK_HEADER = """\
You are helping fill fields on an already-open job application form. You are
NOT applying and must NEVER click Submit, Send, Finish, Complete, Confirm, or
anything that would file the application. Your click tool already refuses
controls that look like that — treat a refusal as final, do not retry with a
different index, and do not look for another way to trigger the same action.
"""

_RESIDUAL_TASK_TRANSCRIBE = """
For each of the following fields, enter or select EXACTLY the given value —
do not invent, guess, paraphrase, or modify it. If a field is a dropdown/
combobox, you can type the given value or open it and choose the option matching the given value; if none
matches closely, leave that field alone and call done. 

{items}
"""

_RESIDUAL_TASK_STRUCTURAL = """
After handling any fields listed above, look for ONE legitimate way to reveal
more of this form that isn't visible yet — a control like "Add another entry",
"Next", "Continue", or an accordion/section toggle. If you find one, click it
once, then call done. If there is nothing more to reveal, call done
immediately without clicking anything.
"""


def _residual_task(residual: list[tuple[Question, Answer]]) -> str:
    task = _RESIDUAL_TASK_HEADER
    if residual:
        items = "\n".join(f"- {q.label!r} (a {q.widget}): {a.value!r}" for q, a in residual)
        task += _RESIDUAL_TASK_TRANSCRIBE.format(items=items)
    task += _RESIDUAL_TASK_STRUCTURAL
    return task


async def _run_residual_turn(
    session,
    residual: list[tuple[Question, Answer]],
    *,
    llm,
    batch_timeout: float,
) -> list[FieldFillResult]:
    """One scoped, click-gated browser-use Agent turn. Never raises — a
    failure here becomes an empty result list, same "one bad step doesn't end
    the job" convention as agent_runner.snapshot_one."""
    results = [
        FieldFillResult(
            question_label=q.label,
            widget=q.widget,
            value_written=a.value,
            path=WritePath.LLM_RESIDUAL,
            answer_source=a.source,
            write_status=WriteStatus.WRITTEN,
        )
        for q, a in residual
    ]
    # Optimistic: recorded as WRITTEN because the Agent was explicitly told to
    # transcribe exactly this value and nothing else, and its own click tool
    # is gated. Unlike dom_writer, there is no read-back verification here —
    # a browser-use Agent's actions aren't individually inspectable after the
    # fact the way a single CDP call is. tests/live/ is where this gets
    # checked against a real page.
    try:
        agent = Agent(
            task=_residual_task(residual),
            llm=llm,
            browser_session=session,
            tools=build_fill_tools(),
            use_vision=False,
            max_failures=3,
            enable_planning=False,
            calculate_cost=True,
        )
        await asyncio.wait_for(agent.run(max_steps=8), timeout=batch_timeout)
    except Exception:
        pass
    return results


def _split_batch(
    questions: list[Question],
) -> tuple[list[Question], list[Question]]:
    """FILE questions never reach the Candidate Agent — see module docstring."""
    file_qs = [q for q in questions if q.widget is WidgetType.FILE]
    normal_qs = [q for q in questions if q.widget is not WidgetType.FILE]
    return file_qs, normal_qs


def _resume_or_cover_letter(question: Question, resume_path: str | None, cover_letter_path: str | None) -> str | None:
    if cover_letter_path and "cover" in question.label.lower():
        return cover_letter_path
    return resume_path


async def _fill_batch(
    session,
    normal_qs: list[Question],
    *,
    context: str,
    llm,
    config: LLMConfig,
    url: str,
    batch_timeout: float,
) -> tuple[list[FieldFillResult], bool]:
    """Returns (results, ran_residual_turn). The caller uses ran_residual_turn
    to decide whether a *separate*, structural-only residual turn is still
    owed for this batch — exactly one residual turn happens per batch, never
    zero (so a purely-structural "Add another entry" opportunity is never
    missed) and never two (so a batch with real residual fields doesn't get a
    second, redundant turn)."""
    fields: list[FieldFillResult] = []
    if not normal_qs:
        return fields, False

    snapshot = JobSnapshot(input_url=url, questions=normal_qs)
    answers = await answer_job(snapshot, context, llm=llm, config=config)  # UNCHANGED, guardrails-first

    residual: list[tuple[Question, Answer]] = []
    for q, a in zip(normal_qs, answers.answers):
        if a.source is AnswerSource.ESCALATED:
            fields.append(
                FieldFillResult(
                    question_label=q.label,
                    widget=q.widget,
                    answer_source=a.source,
                    write_status=WriteStatus.ESCALATED,
                    failure_reason=a.escalation_reason or "not answered",
                )
            )
            continue

        selector_resolved = bool(q.ref.css_selector or q.ref.dom_path)
        if q.widget in _RESIDUAL_WIDGETS or not selector_resolved:
            residual.append((q, a))
            continue

        result = await dom_writer.write_value(session, q, a)
        if q.widget is WidgetType.SELECT_ARIA and result.write_status is WriteStatus.FAILED:
            # Type-to-filter didn't find a confident match (non-searchable
            # widget, unusual DOM shape, or genuinely no matching option) —
            # fall back to the agent rather than reporting this as a final
            # failure, unlike every other widget type.
            residual.append((q, a))
        else:
            fields.append(result)

    if residual:
        fields.extend(await _run_residual_turn(session, residual, llm=llm, batch_timeout=batch_timeout))
        return fields, True

    return fields, False


async def fill_job(
    session,
    url: str,
    context: str,
    *,
    llm=None,
    config: LLMConfig | None = None,
    resume_path: str | None = None,
    cover_letter_path: str | None = None,
    max_steps: int = 25,
    max_batches: int = DEFAULT_MAX_BATCHES,
    job_timeout: float = DEFAULT_JOB_TIMEOUT,
    batch_timeout: float = DEFAULT_BATCH_TIMEOUT,
) -> JobFillResult:
    """Fill one job's form. Never raises — a failure becomes an artifact entry,
    same convention as agent_runner.snapshot_one."""
    result = JobFillResult(input_url=url)
    try:
        await asyncio.wait_for(
            _fill_job_inner(
                session,
                url,
                context,
                llm=llm,
                config=config,
                resume_path=resume_path,
                cover_letter_path=cover_letter_path,
                max_steps=max_steps,
                max_batches=max_batches,
                batch_timeout=batch_timeout,
                result=result,
            ),
            timeout=job_timeout,
        )
    except TimeoutError:
        result.error = f"job timed out after {job_timeout:.0f}s without finishing"
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"

    try:
        result.guard = await guard_status(session)
    except Exception:
        pass
    return result


async def _fill_job_inner(
    session,
    url: str,
    context: str,
    *,
    llm,
    config: LLMConfig | None,
    resume_path: str | None,
    cover_letter_path: str | None,
    max_steps: int,
    max_batches: int,
    batch_timeout: float,
    result: JobFillResult,
) -> None:
    config = config or resolve_config()

    # Phase 1: reach the open form. Reuses snapshot_one as-is; its own
    # questions are discarded, phase 2 re-extracts deterministically.
    snap = await snapshot_one(session, url, llm=llm, max_steps=max_steps, config=config)
    if snap.outcome is not Outcome.FORM_OPEN:
        result.error = f"never reached an open form (outcome={snap.outcome})"
        return

    # Phase 2: batch loop.
    already_seen: set[tuple[str, str]] = set()
    consecutive_empty = 0
    batches = 0
    while batches < max_batches and consecutive_empty < _STOP_AFTER_CONSECUTIVE_EMPTY:
        questions = await extract_tier1(session)
        new = [q for q in questions if q.identity() not in already_seen]
        already_seen |= {q.identity() for q in new}

        if not new:
            consecutive_empty += 1
        else:
            consecutive_empty = 0

        file_qs, normal_qs = _split_batch(new)
        for q in file_qs:
            path = _resume_or_cover_letter(q, resume_path, cover_letter_path)
            result.fields.append(await dom_writer.write_file(session, q, path))

        # Exactly one residual turn per batch (see _fill_batch's docstring):
        # a substantive one if there were residual fields to transcribe, else
        # a bare structural-only one — this is what lets the agent discover a
        # purely structural "Add another entry" control that has no field of
        # its own waiting on an answer, without ever running two turns for
        # the same batch.
        fields, ran_residual = await _fill_batch(
            session,
            normal_qs,
            context=context,
            llm=llm,
            config=config,
            url=url,
            batch_timeout=batch_timeout,
        )
        result.fields.extend(fields)
        if not ran_residual:
            await _run_residual_turn(session, [], llm=llm, batch_timeout=batch_timeout)

        result.batches_run = batches + 1
        batches += 1
