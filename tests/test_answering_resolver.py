"""Resolver behaviour, offline. A fake LLM stands in for the model, so the
validation logic — the part that decides what a model is allowed to produce —
is tested without a key or a network call.
"""

import pytest

from autofill_job_application.answering.models import AnswerSource, DraftAnswer, DraftAnswerSet
from autofill_job_application.answering.resolver import UNCITED_CEILING, answer_job
from autofill_job_application.llm import LLMConfig
from autofill_job_application.models import (
    ElementRef,
    JobSnapshot,
    Question,
    QuestionOption,
    WidgetType,
)

pytestmark = pytest.mark.asyncio

CONTEXT = """\
Alex Doe is a senior backend engineer based in San Francisco.
Email: alex.doe@example.com. LinkedIn: https://linkedin.com/in/alexdoe.
Alex is willing to relocate for the right role.
Alex is most proud of rebuilding a payments ledger that cut reconciliation time.
"""

CFG = LLMConfig(provider="openrouter", model="test/model", api_key="k")


class FakeLLM:
    """Returns a canned DraftAnswerSet; records the prompt it was given."""

    def __init__(self, drafts):
        self.drafts = drafts
        self.prompts = []

    async def ainvoke(self, messages, output_format=None, **kw):
        self.prompts.append("\n".join(str(getattr(m, "content", m)) for m in messages))

        class Completion:
            pass

        c = Completion()
        c.completion = DraftAnswerSet(answers=self.drafts)
        return c


def q(label, *, widget=WidgetType.TEXT, options=None, required=False, notes=None):
    return Question(
        ref=ElementRef(),
        label=label,
        widget=widget,
        required=required,
        options=options,
        notes=notes,
    )


def snap(*questions):
    return JobSnapshot(input_url="https://x.test/1", questions=list(questions))


# --- grounding -----------------------------------------------------------


async def test_quoted_answer_is_marked_supported():
    llm = FakeLLM([DraftAnswer(
        question_index=0, value="alex.doe@example.com",
        evidence="Email: alex.doe@example.com", confidence=0.95)])
    out = await answer_job(snap(q("Email")), CONTEXT, llm=llm, config=CFG)
    a = out.answers[0]
    assert a.source is AnswerSource.CONTEXT_DOC
    assert a.value == "alex.doe@example.com"
    assert a.flagged is False
    assert a.evidence


async def test_fabricated_quote_is_downgraded():
    """A citation that isn't in the document is not evidence."""
    llm = FakeLLM([DraftAnswer(
        question_index=0, value="15",
        evidence="Alex has fifteen years of experience", confidence=0.99)])
    out = await answer_job(snap(q("How many projects have you shipped?")), CONTEXT,
                           llm=llm, config=CFG)
    a = out.answers[0]
    assert a.source is AnswerSource.LLM_INFERENCE
    assert a.confidence <= UNCITED_CEILING
    assert a.flagged is True
    assert a.evidence is None


async def test_uncited_answer_is_always_flagged():
    llm = FakeLLM([DraftAnswer(question_index=0, value="Something plausible",
                               evidence="", confidence=1.0)])
    out = await answer_job(snap(q("Why this role?")), CONTEXT, llm=llm, config=CFG)
    assert out.answers[0].flagged is True
    assert out.answers[0].confidence <= UNCITED_CEILING


async def test_cannot_answer_becomes_an_escalation():
    llm = FakeLLM([DraftAnswer(question_index=0, cannot_answer=True)])
    out = await answer_job(snap(q("What is your mother's maiden name?")), CONTEXT,
                           llm=llm, config=CFG)
    assert out.answers[0].source is AnswerSource.ESCALATED
    assert "does not cover" in out.answers[0].escalation_reason


async def test_omitted_question_escalates_by_absence():
    """If the model skips a question, it must not silently vanish."""
    llm = FakeLLM([])
    out = await answer_job(snap(q("Email"), q("Phone")), CONTEXT, llm=llm, config=CFG)
    assert len(out.answers) == 2
    assert all(a.source is AnswerSource.ESCALATED for a in out.answers)


# --- enum constraints ----------------------------------------------------


async def test_choice_answer_must_be_one_of_the_offered_options():
    opts = [QuestionOption(value="Yes", label="Yes"), QuestionOption(value="No", label="No")]
    llm = FakeLLM([DraftAnswer(question_index=0, value="Maybe",
                               evidence="Alex is willing to relocate", confidence=0.9)])
    out = await answer_job(
        snap(q("Relocate?", widget=WidgetType.SELECT_NATIVE, options=opts)),
        CONTEXT, llm=llm, config=CFG)
    a = out.answers[0]
    assert a.source is AnswerSource.ESCALATED
    assert "not one of the offered choices" in a.escalation_reason


async def test_valid_choice_is_normalized_to_the_option_label():
    opts = [QuestionOption(value="Yes", label="Yes"), QuestionOption(value="No", label="No")]
    llm = FakeLLM([DraftAnswer(question_index=0, value="yes",
                               evidence="Alex is willing to relocate for the right role",
                               confidence=0.9)])
    out = await answer_job(
        snap(q("Relocate?", widget=WidgetType.SELECT_NATIVE, options=opts)),
        CONTEXT, llm=llm, config=CFG)
    assert out.answers[0].value == "Yes"
    assert out.answers[0].source is AnswerSource.CONTEXT_DOC


async def test_unreadable_choices_escalate_rather_than_guess():
    """options is None means the legal values are unknown — free text is unusable."""
    llm = FakeLLM([DraftAnswer(question_index=0, value="Yes",
                               evidence="Alex is willing to relocate", confidence=0.9)])
    out = await answer_job(
        snap(q("Relocate?", widget=WidgetType.SELECT_ARIA, options=None)),
        CONTEXT, llm=llm, config=CFG)
    a = out.answers[0]
    assert a.source is AnswerSource.ESCALATED
    assert "could not be read" in a.escalation_reason


# --- guardrails come first ----------------------------------------------


async def test_guarded_questions_never_reach_the_model():
    llm = FakeLLM([])
    await answer_job(
        snap(q("Expected Salary"), q("Gender"), q("I certify this is true")),
        CONTEXT, llm=llm, config=CFG)
    assert llm.prompts == [], "a withheld question was sent to the model"


async def test_guarded_and_answerable_questions_coexist():
    llm = FakeLLM([DraftAnswer(question_index=1, value="alex.doe@example.com",
                               evidence="Email: alex.doe@example.com", confidence=0.95)])
    out = await answer_job(snap(q("Expected Salary"), q("Email")), CONTEXT,
                           llm=llm, config=CFG)
    assert out.answers[0].category == "compensation"
    assert out.answers[0].source is AnswerSource.ESCALATED
    assert out.answers[1].value == "alex.doe@example.com"
    # only the safe question was in the prompt
    assert "Expected Salary" not in llm.prompts[0]
    assert "Email" in llm.prompts[0]


# --- indexing and robustness --------------------------------------------


async def test_duplicate_labels_stay_distinct():
    """Two questions labelled 'Attach' are different questions; a label join
    would merge them. Real Greenhouse forms do exactly this."""
    llm = FakeLLM([
        DraftAnswer(question_index=0, value="resume.pdf", evidence="", confidence=0.4),
        DraftAnswer(question_index=1, value="cover.pdf", evidence="", confidence=0.4),
    ])
    out = await answer_job(snap(q("Attach"), q("Attach")), CONTEXT, llm=llm, config=CFG)
    assert [a.question_index for a in out.answers] == [0, 1]
    assert out.answers[0].value != out.answers[1].value


async def test_llm_failure_is_recorded_not_raised():
    class Boom:
        async def ainvoke(self, *a, **k):
            raise RuntimeError("provider exploded")

    out = await answer_job(snap(q("Email")), CONTEXT, llm=Boom(), config=CFG)
    assert "provider exploded" in out.error
    assert out.answers[0].source is AnswerSource.ESCALATED


async def test_snapshot_with_no_questions_is_fine():
    out = await answer_job(snap(), CONTEXT, llm=FakeLLM([]), config=CFG)
    assert out.answers == []


async def test_counts():
    llm = FakeLLM([DraftAnswer(question_index=1, value="alex.doe@example.com",
                               evidence="Email: alex.doe@example.com", confidence=0.95)])
    out = await answer_job(snap(q("Expected Salary"), q("Email")), CONTEXT,
                           llm=llm, config=CFG)
    assert out.answered_count == 1
    assert out.escalated_count == 1
