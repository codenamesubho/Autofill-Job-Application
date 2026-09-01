"""Types for a fill run.

Mirrors answering/models.py's precedent: its own types, not a reuse of root
models.RunResult, because a fill run's shape (per-field write outcome, which
path wrote it, batch count) doesn't fit JobSnapshot/RunResult's question-report
shape. AnswerSource is reused from answering.models rather than redefined, so a
human reading a fills/*.json next to the answers/*.json for the same job sees
one consistent provenance vocabulary.

Nothing here writes to a browser by itself — see dom_writer.py and runner.py
for the code that actually touches the DOM. This module is just the record of
what happened.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from ..answering.models import AnswerSource
from ..models import WidgetType


class WritePath(StrEnum):
    #: Written directly by CDP, no LLM involved in deciding how to write it.
    DETERMINISTIC = "deterministic"
    #: Written by the residual browser-use Agent turn (custom combobox, unresolved
    #: selector) — the LLM only ever transcribes an already-vetted Answer, never
    #: invents a value itself.
    LLM_RESIDUAL = "llm_residual"


class WriteStatus(StrEnum):
    WRITTEN = "written"
    #: Guardrails withheld the answer, or the Candidate Agent didn't answer it —
    #: never attempted.
    ESCALATED = "escalated"
    #: A write was attempted but the read-back verification didn't match what
    #: was intended (e.g. a React-controlled input reverted the value).
    FAILED = "failed"


class FieldFillResult(BaseModel):
    question_label: str
    widget: WidgetType
    value_written: str | None = None
    path: WritePath = WritePath.DETERMINISTIC
    #: Provenance of the *answer* (context_doc / llm_inference / escalated),
    #: from answering.models — distinct from `path`, which is provenance of the
    #: *write*.
    answer_source: AnswerSource = AnswerSource.ESCALATED
    write_status: WriteStatus = WriteStatus.ESCALATED
    failure_reason: str | None = None


class JobFillResult(BaseModel):
    input_url: str
    fields: list[FieldFillResult] = Field(default_factory=list)
    batches_run: int = 0
    #: Same shape as JobSnapshot.guard: {"installed": bool, "blocked": int, "reasons": [...]}.
    #: A non-zero `blocked` means something attempted a submission despite both
    #: gate layers — worth surfacing loudly, same as in a snapshot run.
    guard: dict = Field(default_factory=dict)
    error: str | None = None

    @property
    def written_count(self) -> int:
        return sum(1 for f in self.fields if f.write_status is WriteStatus.WRITTEN)

    @property
    def escalated_count(self) -> int:
        return sum(1 for f in self.fields if f.write_status is WriteStatus.ESCALATED)

    @property
    def failed_count(self) -> int:
        return sum(1 for f in self.fields if f.write_status is WriteStatus.FAILED)


class FillRun(BaseModel):
    jobs: list[JobFillResult] = Field(default_factory=list)
    context_path: str = ""
    resume_path: str = ""
    cover_letter_path: str = ""
    generated_by: str = "autofill-job-application (filling)"
    #: Restated in every artifact, same convention as answering.models.AnswerRun.
    disclaimer: str = (
        "Fields were filled for human review before submission. This tool never "
        "submits an application; two independent layers (a click gate and a CDP "
        "submit guard) make that structurally true regardless of what any agent "
        "decides to click."
    )
