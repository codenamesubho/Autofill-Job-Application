"""Turn snapshot questions into draft answers grounded in a context document.

Order of operations matters and is deliberate:

1. **Guardrails first.** Questions on the never-answer list are escalated before
   the model is called, so it never sees them.
2. **One batched call** for everything left, with the context document supplied
   verbatim and instructions to cite rather than invent.
3. **Validation after.** A returned value is checked against the question's
   options and against whether it was actually supported by a quote. Our code —
   not the model — decides the final source, confidence and flag.

No browser is touched. This module has no path to a form field.
"""

from __future__ import annotations

from ..llm import LLMConfig, build_llm, resolve_config
from ..models import JobSnapshot, Question, WidgetType
from . import guardrails
from .models import (
    Answer,
    AnswerSource,
    DraftAnswerSet,
    JobAnswers,
)

#: Confidence ceiling for an answer the model reasoned to without a quote. Kept
#: below the review threshold so unsupported answers are always flagged.
UNCITED_CEILING = 0.5

#: At or above this, an answer is considered well-supported and not flagged.
REVIEW_THRESHOLD = 0.8

SYSTEM_PROMPT = """\
You draft answers to job application questions using ONLY the candidate context \
document provided. You are preparing a draft for the candidate to review; nothing \
you write is submitted anywhere.

Rules:
- Ground every answer in the context document. Quote the exact supporting span in \
`evidence`.
- If the document does not support an answer, set cannot_answer=true and leave \
value empty. Do NOT guess, estimate, or invent facts about this person.
- Never invent: dates, employers, job titles, numbers, qualifications, contact \
details, or anything a recruiter could verify.
- For a question with a fixed list of choices, `value` MUST be exactly one of \
those choices, copied verbatim.
- For free-text questions, write in the candidate's own voice as the document \
suggests, concise and specific to what the document actually says.
- `confidence` reflects how directly the document supports the answer: high only \
when the evidence quote plainly states it.
"""


def _question_block(questions: list[tuple[int, Question]]) -> str:
    lines = []
    for idx, q in questions:
        line = f"[{idx}] {q.label}"
        if q.required:
            line += "  (required)"
        line += f"  type={q.widget}"
        if q.options:
            choices = " | ".join(o.label for o in q.options)
            line += f"\n     CHOICES (answer must be exactly one): {choices}"
        elif q.options is None and q.widget in (
            WidgetType.SELECT_ARIA,
            WidgetType.SELECT_NATIVE,
        ):
            line += "\n     CHOICES: unknown"
        if q.notes:
            line += f"\n     note: {q.notes}"
        lines.append(line)
    return "\n".join(lines)


def _escalate(idx: int, q: Question, reason: str, category: str | None = None) -> Answer:
    return Answer(
        question_index=idx,
        question_label=q.label,
        value=None,
        source=AnswerSource.ESCALATED,
        confidence=0.0,
        flagged=True,
        escalation_reason=reason,
        category=category,
    )


def _validate(idx: int, q: Question, draft, context: str) -> Answer:
    """Decide the final answer. The model proposes; this function disposes."""
    if draft is None or draft.cannot_answer or not (draft.value or "").strip():
        return _escalate(idx, q, "context document does not cover this question")

    value = draft.value.strip()

    # An enum whose choices we could not read: a free-text guess is unusable in a
    # dropdown whose legal values are unknown, so it is never worth offering.
    if q.options is None and q.widget in (WidgetType.SELECT_ARIA, WidgetType.SELECT_NATIVE):
        return _escalate(
            idx, q, "choices could not be read from the form, so an answer cannot be validated"
        )

    # An enum with known choices: the answer must be one of them, verbatim.
    if q.options:
        legal = {o.label.strip().lower(): o.label for o in q.options}
        legal |= {o.value.strip().lower(): o.label for o in q.options}
        match = legal.get(value.lower())
        if match is None:
            return _escalate(
                idx,
                q,
                f"model answered {value!r}, which is not one of the offered choices",
            )
        value = match

    # Evidence must actually appear in the document; a quote that doesn't is a
    # fabricated citation and downgrades the answer to unsupported.
    evidence = (draft.evidence or "").strip()
    cited = bool(evidence) and _appears_in(evidence, context)

    if cited:
        confidence = min(max(draft.confidence, 0.0), 1.0)
        source = AnswerSource.CONTEXT_DOC
    else:
        confidence = min(max(draft.confidence, 0.0), UNCITED_CEILING)
        source = AnswerSource.LLM_INFERENCE
        evidence = ""

    return Answer(
        question_index=idx,
        question_label=q.label,
        value=value,
        source=source,
        confidence=round(confidence, 2),
        flagged=confidence < REVIEW_THRESHOLD or source is AnswerSource.LLM_INFERENCE,
        evidence=evidence or None,
    )


def _appears_in(quote: str, context: str) -> bool:
    """Loose containment check — whitespace-insensitive, case-insensitive."""
    norm = lambda s: " ".join(s.split()).lower()
    q, c = norm(quote), norm(context)
    if len(q) < 8:  # too short to be meaningful evidence
        return False
    return q in c


async def answer_job(
    snapshot: JobSnapshot,
    context: str,
    *,
    llm=None,
    config: LLMConfig | None = None,
) -> JobAnswers:
    """Draft answers for one job's questions. Never raises."""
    config = config or resolve_config()
    out = JobAnswers(input_url=snapshot.input_url, model=config.describe())

    if not snapshot.questions:
        return out

    # 1. Guardrails, before the model sees anything.
    pending: list[tuple[int, Question]] = []
    results: dict[int, Answer] = {}
    for idx, q in enumerate(snapshot.questions):
        hit = guardrails.check(q.label, q.notes)
        if hit:
            category, reason = hit
            results[idx] = _escalate(idx, q, reason, category)
        else:
            pending.append((idx, q))

    # 2. One batched call for whatever is left.
    if pending:
        try:
            llm = llm or build_llm(config)
            from browser_use.llm.messages import SystemMessage, UserMessage

            user = (
                f"CANDIDATE CONTEXT DOCUMENT:\n---\n{context}\n---\n\n"
                f"QUESTIONS (answer by index):\n{_question_block(pending)}"
            )
            completion = await llm.ainvoke(
                [SystemMessage(content=SYSTEM_PROMPT), UserMessage(content=user)],
                output_format=DraftAnswerSet,
            )
            drafts = {d.question_index: d for d in completion.completion.answers}
        except Exception as exc:
            out.error = f"{type(exc).__name__}: {exc}"
            drafts = {}

        # 3. Validation. Anything the model omitted escalates by absence.
        for idx, q in pending:
            results[idx] = _validate(idx, q, drafts.get(idx), context)

    out.answers = [results[i] for i in sorted(results)]
    return out
