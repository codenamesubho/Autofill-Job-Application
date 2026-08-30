"""The snapshot contract.

A `Question` is what a human would recognize as one thing the form asks. It carries
enough identity (`ElementRef`) for a later slice to find the element again, and
enough provenance (`label_source`, `confidence`, `source`) to debug a wrong answer
without re-opening the browser.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class WidgetType(StrEnum):
    TEXT = "text"
    TEXTAREA = "textarea"
    EMAIL = "email"
    TEL = "tel"
    NUMBER = "number"
    DATE = "date"
    URL = "url"
    FILE = "file"
    SELECT_NATIVE = "select_native"
    SELECT_ARIA = "select_aria"
    RADIO_GROUP = "radio_group"
    CHECKBOX = "checkbox"
    UNKNOWN = "unknown"


class LabelSource(StrEnum):
    """Which rule in the derivation cascade produced the label.

    Ordered most to least trustworthy; `confidence` decays down this list.
    """

    FOR_ATTR = "for-attr"
    ARIA_LABELLEDBY = "aria-labelledby"
    ARIA_LABEL = "aria-label"
    WRAPPING_LABEL = "wrapping-label"
    LEGEND = "legend"
    PRECEDING_TEXT = "preceding-text"
    PLACEHOLDER = "placeholder"
    NAME_ATTR = "name-attr"
    LLM = "llm"
    NONE = "none"


class SourceTier(StrEnum):
    TIER1 = "tier1"
    TIER2 = "tier2"
    MERGED = "merged"


class ElementRef(BaseModel):
    """Four independent ways to re-find an element.

    ATS DOMs are regenerated between visits, so no single identifier is reliable.
    A later fill slice needs at least one of these to still resolve.
    """

    frame_url: str = ""
    #: CDP target id when the element lives in a cross-origin iframe; None in the top frame.
    target_id: str | None = None
    css_selector: str | None = None
    #: Positional fallback, e.g. "0.2.1.4", for when nothing else is stable.
    dom_path: str | None = None
    element_id: str | None = None
    name_attr: str | None = None


class QuestionOption(BaseModel):
    value: str
    label: str
    selected: bool = False


class Question(BaseModel):
    ref: ElementRef
    label: str
    label_source: LabelSource = LabelSource.NONE
    widget: WidgetType = WidgetType.UNKNOWN
    required: bool = False
    #: `None` means "this is an enum widget whose options could not be read without
    #: clicking it" — deliberately distinct from `[]`, which means "no options".
    options: list[QuestionOption] | None = None
    placeholder: str | None = None
    #: Value the ATS pre-filled. Read only; nothing in this package writes it.
    current_value: str | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source: SourceTier = SourceTier.TIER1
    notes: str | None = None

    @property
    def options_unknown(self) -> bool:
        return self.options is None

    def identity(self) -> tuple[str, str]:
        """Merge key: which element this is, independent of what we think it asks."""
        return (
            self.ref.frame_url,
            self.ref.css_selector or self.ref.dom_path or self.ref.name_attr or self.label,
        )


class Outcome(StrEnum):
    FORM_OPEN = "form_open"
    LOGIN_WALL = "login_wall"
    NO_APPLY_CONTROL_FOUND = "no_apply_control_found"
    MAX_HOPS_EXCEEDED = "max_hops_exceeded"
    NAVIGATION_ERROR = "navigation_error"


class JobSnapshot(BaseModel):
    input_url: str
    final_url: str = ""
    outcome: Outcome = Outcome.NAVIGATION_ERROR
    hops: list[str] = Field(default_factory=list)
    questions: list[Question] = Field(default_factory=list)
    #: {"skipped": "no ANTHROPIC_API_KEY"} or {"ran": True, "steps": 12, ...}
    tier2: dict = Field(default_factory=dict)
    #: Submit-guard readout: {"installed": bool, "blocked": int, "reasons": [...]}.
    #: A non-zero `blocked` means something actually attempted a submission.
    guard: dict = Field(default_factory=dict)
    #: Unparsed agent output, kept only when structured parsing failed, so a bad
    #: run can be diagnosed without re-running it.
    raw_result: str | None = None
    timestamp: str = ""
    error: str | None = None

    @property
    def required_count(self) -> int:
        return sum(1 for q in self.questions if q.required)


class RunResult(BaseModel):
    jobs: list[JobSnapshot] = Field(default_factory=list)
    generated_by: str = "autofill-job-application"
    version: str = "0.1.0"


# ---------------------------------------------------------------------------
# Agent-facing schema
#
# What the LLM is asked to return. Deliberately narrower than `Question` above:
# a model can reliably report what a form visibly asks, but not a CSS selector
# that will still resolve later. Asking for one invites confident fiction, so
# `ElementRef` is absent here and filled in only by deterministic extraction.
# ---------------------------------------------------------------------------


class AgentQuestion(BaseModel):
    label: str
    #: Free text rather than an enum: an over-constrained schema makes the model
    #: force odd widgets into the wrong bucket. Normalized on the way in.
    widget: str = "unknown"
    required: bool = False
    options: list[str] = Field(default_factory=list)
    help_text: str | None = None
    #: Heading the question sat under, e.g. "Voluntary Self-Identification".
    section: str | None = None


class AgentFormSnapshot(BaseModel):
    reached_form: bool = False
    outcome: str = "error"
    final_url: str = ""
    questions: list[AgentQuestion] = Field(default_factory=list)
    notes: str | None = None


#: Agent widget strings -> our WidgetType. Anything unrecognized becomes UNKNOWN
#: rather than being dropped, so a question is never lost to a vocabulary miss.
_WIDGET_ALIASES = {
    "text": WidgetType.TEXT,
    "input": WidgetType.TEXT,
    "string": WidgetType.TEXT,
    "textarea": WidgetType.TEXTAREA,
    "long_text": WidgetType.TEXTAREA,
    "email": WidgetType.EMAIL,
    "tel": WidgetType.TEL,
    "phone": WidgetType.TEL,
    "number": WidgetType.NUMBER,
    "date": WidgetType.DATE,
    "url": WidgetType.URL,
    "file": WidgetType.FILE,
    "upload": WidgetType.FILE,
    "select": WidgetType.SELECT_NATIVE,
    "dropdown": WidgetType.SELECT_NATIVE,
    "combobox": WidgetType.SELECT_ARIA,
    "radio": WidgetType.RADIO_GROUP,
    "radio_group": WidgetType.RADIO_GROUP,
    "checkbox": WidgetType.CHECKBOX,
}


def normalize_widget(value: str | None) -> WidgetType:
    if not value:
        return WidgetType.UNKNOWN
    return _WIDGET_ALIASES.get(value.strip().lower().replace("-", "_"), WidgetType.UNKNOWN)


def agent_question_to_question(aq: AgentQuestion) -> Question:
    """Map an LLM-reported question into the internal model.

    `source=TIER2` and a capped confidence record the provenance honestly: this
    came from a model looking at a page, not from reading the DOM.
    """
    notes = []
    if aq.section:
        notes.append(f"section: {aq.section}")
    if aq.help_text:
        notes.append(f"help: {aq.help_text}")
    notes.append("reported by LLM agent; no DOM locator")

    return Question(
        ref=ElementRef(),
        label=aq.label,
        label_source=LabelSource.LLM,
        widget=normalize_widget(aq.widget),
        required=aq.required,
        options=[QuestionOption(value=o, label=o) for o in aq.options] if aq.options else None,
        confidence=0.7,
        source=SourceTier.TIER2,
        notes="; ".join(notes),
    )
