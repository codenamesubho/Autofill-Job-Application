"""The contract between the browser layer and the answering layer.

The single most important rule in this codebase: the answering layer never sees
the DOM. The browser layer normalizes any page into `Question` objects; the
answering layer returns `Answer` objects; the filler translates those back into
DOM actions. `Selector` is opaque to everything on the answering side.

Keeping this boundary sharp is what makes answering unit-testable with zero
browser, the answer cache reusable across every ATS, and Playwright swappable.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field, field_validator


class FieldType(str, Enum):
    TEXT = "text"
    TEXTAREA = "textarea"
    SELECT = "select"
    MULTISELECT = "multiselect"
    RADIO = "radio"
    CHECKBOX = "checkbox"
    FILE = "file"
    DATE = "date"
    PHONE = "phone"
    EMAIL = "email"
    URL = "url"
    NUMBER = "number"
    CONSENT = "consent"
    UNKNOWN = "unknown"

    @property
    def is_enum(self) -> bool:
        """True when the answer must be one of `Question.options`."""
        return self in {FieldType.SELECT, FieldType.MULTISELECT, FieldType.RADIO}


class AnswerSource(str, Enum):
    """Where an answer came from - recorded for every field, shown in review."""

    PROFILE = "profile"      # deterministic map from profile.yaml
    CACHE = "cache"          # answer_cache hit
    AGENT = "agent"          # Candidate Context Agent (LLM)
    HUMAN = "human"          # supplied during review
    PREFILLED = "prefilled"  # already on the page, left alone


class Selector(BaseModel):
    """Opaque handle back to the DOM. The answering layer must not read this."""

    strategy: str = "css"  # css | xpath | role | testid | label
    value: str
    frame_url: str | None = None  # non-None when the field lives in an iframe
    index: int | None = None      # nth match, for radio groups and repeated controls


class Option(BaseModel):
    """A legal value for an enum-typed question."""

    value: str                       # what gets submitted
    label: str                       # what the human sees
    selector: Selector | None = None  # for radios/checkboxes rendered per option


class Question(BaseModel):
    field_id: str                    # stable hash of (url_template, name/id/label)
    canonical_key: str | None = None  # "email", "phone", "work_auth", ...
    label: str                       # human-readable question text
    help_text: str | None = None
    type: FieldType = FieldType.UNKNOWN
    options: list[Option] = Field(default_factory=list)  # the ONLY legal values
    required: bool = False
    max_length: int | None = None
    current_value: str | None = None
    selector: Selector
    confidence: float = 1.0  # extractor's confidence it parsed this correctly

    @property
    def filled(self) -> bool:
        return bool(self.current_value and self.current_value.strip())

    def cache_key(self, *, jd_dependent: bool = False, job_id: str | None = None) -> str:
        """Normalized hash for the answer cache.

        Factual questions cache globally. Anything referencing the company or the
        job description must be scoped per-job, or you will paste the wrong
        company name into an essay (PLAN.md 8.4).
        """
        norm = " ".join(self.label.lower().split())
        parts = [norm, self.type.value]
        parts.extend(sorted(o.value for o in self.options))
        if jd_dependent:
            parts.append(f"job:{job_id or ''}")
        return hashlib.sha256(" ".join(parts).encode()).hexdigest()


class Answer(BaseModel):
    field_id: str
    value: str | list[str] | None  # list for multiselect; None = deliberately blank
    source: AnswerSource
    confidence: float = 1.0
    rationale: str | None = None
    sources: list[str] = Field(default_factory=list)  # citations from the context agent
    flagged: bool = False          # needs human eyes before submit
    blank_reason: str | None = None  # why the agent refused to answer

    @field_validator("confidence")
    @classmethod
    def _clamp(cls, v: float) -> float:
        return max(0.0, min(1.0, v))

    @property
    def is_blank(self) -> bool:
        return self.value is None or (isinstance(self.value, str) and not self.value.strip())


class FormSchema(BaseModel):
    """One page (or wizard step) normalized into questions."""

    url: str
    ats: str | None = None
    tier: int = 1        # 0=adapter, 1=generic DOM, 2=vision/a11y fallback
    step: int = 0        # wizard step index
    total_steps: int | None = None
    questions: list[Question] = Field(default_factory=list)
    fingerprint: str | None = None  # page-state hash, for no-progress detection

    @property
    def unfilled_required(self) -> list[Question]:
        return [q for q in self.questions if q.required and not q.filled]


class JobTarget(BaseModel):
    """A normalized, deduped job from jobs.yaml."""

    id: str
    url: str
    canonical_url: str | None = None
    company: str | None = None
    title: str | None = None
    ats: str | None = None
    notes: str | None = None

    @staticmethod
    def make_id(canonical_url: str) -> str:
        return hashlib.sha256(canonical_url.encode()).hexdigest()[:16]


class AppState(BaseModel):
    """The per-application record mirrored in the `applications` table."""

    id: str
    job_url: str
    canonical_url: str | None = None
    company: str | None = None
    title: str | None = None
    ats: str | None = None
    status: str = "PENDING"
    attempt: int = 0
    last_error: str | None = None
    artifact_dir: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
