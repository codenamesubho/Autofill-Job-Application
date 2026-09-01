"""Types for generated answers.

Nothing here writes to a browser. An `AnswerSet` is a document for a human to
read, edit and act on — the package deliberately has no path from an answer to a
form field.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class AnswerSource(StrEnum):
    #: Supported by a quoted span from the context document.
    CONTEXT_DOC = "context_doc"
    #: The model reasoned to it; plausible but unsupported by any quote.
    LLM_INFERENCE = "llm_inference"
    #: Deliberately not answered — see `escalation_reason`.
    ESCALATED = "escalated"


class Answer(BaseModel):
    #: Position of the question within its job's `questions` list.
    #: Index, not label: real forms repeat labels (a Greenhouse form showed two
    #: file inputs both labelled "Attach"), and v1 snapshots carry no selector,
    #: so the label is not a unique key.
    question_index: int
    #: Copied from the snapshot for readability; never used to join.
    question_label: str

    value: str | None = None
    source: AnswerSource = AnswerSource.ESCALATED
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    #: True when a human must look at this before it is used.
    flagged: bool = True
    #: Verbatim span from the context document supporting `value`. This is what
    #: makes a high confidence auditable instead of merely asserted.
    evidence: str | None = None
    escalation_reason: str | None = None
    #: Guardrail category, when the question was withheld by policy.
    category: str | None = None

    @property
    def answered(self) -> bool:
        return self.source is not AnswerSource.ESCALATED and bool(self.value)


class JobAnswers(BaseModel):
    input_url: str
    answers: list[Answer] = Field(default_factory=list)
    #: Model that produced these, e.g. "openrouter:anthropic/claude-opus-5".
    model: str = ""
    error: str | None = None

    @property
    def answered_count(self) -> int:
        return sum(1 for a in self.answers if a.answered)

    @property
    def escalated_count(self) -> int:
        return sum(1 for a in self.answers if not a.answered)


class AnswerRun(BaseModel):
    #: The snapshot file these answers correspond to, by index.
    snapshot_path: str = ""
    context_path: str = ""
    jobs: list[JobAnswers] = Field(default_factory=list)
    generated_by: str = "autofill-job-application (answering)"
    #: Restated in every artifact: these are drafts, not submissions.
    disclaimer: str = (
        "Draft answers for human review. Nothing here has been entered into any "
        "form; this tool cannot fill or submit an application."
    )


# --- what the model is asked to return -----------------------------------
#
# Narrower than `Answer`: the model supplies only the content and its evidence.
# Index, source and flags are set by our code, not by the model, so it cannot
# mark its own guesses as trustworthy.


class DraftAnswer(BaseModel):
    question_index: int
    value: str | None = None
    #: Verbatim quote from the context document, or empty when the model is
    #: reasoning rather than citing.
    evidence: str = ""
    #: The model's own confidence; capped by our code and never trusted as-is.
    confidence: float = 0.5
    #: Set when the context document simply does not cover this question.
    cannot_answer: bool = False


class DraftAnswerSet(BaseModel):
    answers: list[DraftAnswer] = Field(default_factory=list)
