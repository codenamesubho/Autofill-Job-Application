"""The contract between the browser layer and the answering layer.

The single most important rule in this codebase: the answering layer never sees
the DOM. The browser layer normalizes any page into `Question` objects; the
answering layer returns `Answer` objects; the filler translates those back into
DOM actions. `Locator` is opaque to everything on the answering side.

Keeping this boundary sharp is what makes answering unit-testable with zero
browser, the answer cache reusable across every ATS, and Playwright swappable.

Second rule: nothing here names a vendor. Controls are described by behaviour
(`WidgetKind`), never by who built the page. Vendor names live in tests/corpus/
as measurement, never in code as branching.
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
    COMBOBOX = "combobox"
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
        return self in {
            FieldType.SELECT,
            FieldType.COMBOBOX,
            FieldType.MULTISELECT,
            FieldType.RADIO,
        }


class WidgetKind(str, Enum):
    """How to drive a control - not who built it.

    This is the replacement for "which ATS is this". The codebase classifies
    behaviour, never vendor; vendor names appear only in tests/corpus/.
    """

    NATIVE = "native"    # a real <select>, <input>, <textarea>
    ARIA = "aria"        # follows the ARIA combobox/listbox contract
    CUSTOM = "custom"    # neither: probe by click-and-diff
    UNKNOWN = "unknown"


class AnswerSource(str, Enum):
    """Where an answer came from - recorded for every field, shown in review."""

    PROFILE = "profile"      # deterministic map from profile.yaml
    CACHE = "cache"          # answer_cache hit
    AGENT = "agent"          # Candidate Context Agent (LLM)
    HUMAN = "human"          # supplied during review
    PREFILLED = "prefilled"  # already on the page, left alone


class Locator(BaseModel):
    """Opaque handle back to the DOM. The answering layer must not read this.

    Frames are first-class: extraction always runs over the frame tree, so every
    locator carries the path of frame URLs from the top document down to its
    field. An embedded third-party form is just a frame with high form density.
    """

    strategy: str = "css"  # css | xpath | role | testid | label
    value: str
    frame_path: list[str] = Field(default_factory=list)  # outermost -> innermost
    shadow_path: list[str] = Field(default_factory=list)  # open shadow roots to pierce
    index: int | None = None  # nth match, for radio groups and repeated controls

    @property
    def in_frame(self) -> bool:
        return bool(self.frame_path)


class Option(BaseModel):
    """A legal value for an enum-typed question."""

    value: str                      # what gets submitted
    label: str                      # what the human sees
    locator: Locator | None = None  # for radios/checkboxes rendered per option


class Question(BaseModel):
    field_id: str                    # stable hash of (domain, frame_path, name/id/label)
    canonical_key: str | None = None  # "email", "phone", "work_auth", ...
    label: str                       # human-readable question text
    help_text: str | None = None
    type: FieldType = FieldType.UNKNOWN
    widget: WidgetKind = WidgetKind.UNKNOWN  # how to drive it, not who built it
    options: list[Option] = Field(default_factory=list)  # the ONLY legal values
    options_probed: bool = False  # enumeration is lazy; a click costs a DOM diff
    required: bool = False
    max_length: int | None = None
    current_value: str | None = None
    locator: Locator
    confidence: float = 1.0  # extractor's confidence it parsed this correctly

    @property
    def filled(self) -> bool:
        return bool(self.current_value and self.current_value.strip())

    @property
    def needs_option_probe(self) -> bool:
        """Enum-typed, but we have not yet paid the click to enumerate options."""
        return self.type.is_enum and not self.options_probed and not self.options

    @staticmethod
    def make_field_id(domain: str, frame_path: list[str], name: str) -> str:
        """Stable across visits to the same page, distinct across frames."""
        raw = "|".join([domain, ">".join(frame_path), " ".join(name.lower().split())])
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

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
    tier: int = 1  # 1=structural, 2=perceptual fallback. There is no tier 0.
    questions: list[Question] = Field(default_factory=list)
    fingerprint: str | None = None  # page-state hash, for no-progress detection

    @property
    def unfilled_required(self) -> list[Question]:
        return [q for q in self.questions if q.required and not q.filled]

    def low_confidence(self, threshold: float) -> list[Question]:
        """Fields tier 1 is unsure about - the candidates for tier 2."""
        return [q for q in self.questions if q.confidence < threshold]


class ForwardControl(str, Enum):
    """Classification of the button that moves a form onward (PLAN.md 2.4).

    AMBIGUOUS is deliberately not a shrug: it is a decision to stop. A wrongly
    stopped wizard costs the human one click; a wrongly advanced one sends a
    real application. Both AMBIGUOUS and TERMINAL_SUBMIT halt the loop.
    """

    ADVANCE = "advance"                  # next / continue / save-and-continue
    TERMINAL_SUBMIT = "terminal_submit"  # never clicked, by any code path
    AMBIGUOUS = "ambiguous"              # fails safe: treated as terminal
    NONE = "none"                        # no forward control found

    @property
    def may_click(self) -> bool:
        return self is ForwardControl.ADVANCE


class JobTarget(BaseModel):
    """A normalized, deduped job from jobs.yaml."""

    id: str
    url: str
    canonical_url: str | None = None
    company: str | None = None
    title: str | None = None
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
    status: str = "PENDING"
    attempt: int = 0
    last_error: str | None = None
    artifact_dir: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("created_at", "updated_at")
    @classmethod
    def _assume_utc(cls, v: datetime) -> datetime:
        """SQLite's datetime('now') is naive UTC; model defaults are aware.

        Without this, comparing a stored timestamp against a fresh one raises
        TypeError - which is what retry backoff and staleness checks do.
        """
        return v.replace(tzinfo=timezone.utc) if v.tzinfo is None else v
