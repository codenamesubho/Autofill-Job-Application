"""Schema round-trips, and the agent-output mapping."""

import json

from autofill_job_application.models import (
    AgentFormSnapshot,
    AgentQuestion,
    JobSnapshot,
    LabelSource,
    Outcome,
    RunResult,
    SourceTier,
    WidgetType,
    agent_question_to_question,
    normalize_widget,
)


def test_agent_output_json_validates():
    """A blob shaped like what the agent returns must parse without coaxing."""
    raw = json.dumps(
        {
            "reached_form": True,
            "outcome": "form_open",
            "final_url": "https://example.test/apply",
            "questions": [
                {
                    "label": "First Name",
                    "widget": "text",
                    "required": True,
                    "options": [],
                    "help_text": None,
                    "section": "Personal Information",
                },
                {
                    "label": "Are you legally authorized to work?",
                    "widget": "select",
                    "required": True,
                    "options": ["Yes", "No"],
                },
            ],
            "notes": "two pages",
        }
    )
    snap = AgentFormSnapshot.model_validate_json(raw)
    assert snap.reached_form is True
    assert len(snap.questions) == 2
    assert snap.questions[1].options == ["Yes", "No"]


def test_agent_snapshot_tolerates_minimal_payload():
    """Models omit optional keys; defaults must absorb that."""
    snap = AgentFormSnapshot.model_validate({"questions": [{"label": "Email"}]})
    assert snap.questions[0].widget == "unknown"
    assert snap.questions[0].required is False
    assert snap.outcome == "error"


def test_widget_normalization():
    assert normalize_widget("text") is WidgetType.TEXT
    assert normalize_widget("Dropdown") is WidgetType.SELECT_NATIVE
    assert normalize_widget("radio-group") is WidgetType.RADIO_GROUP
    assert normalize_widget("phone") is WidgetType.TEL
    assert normalize_widget("upload") is WidgetType.FILE
    # An unknown vocabulary word must not lose the question.
    assert normalize_widget("wizardy-thing") is WidgetType.UNKNOWN
    assert normalize_widget(None) is WidgetType.UNKNOWN


def test_agent_question_maps_with_honest_provenance():
    q = agent_question_to_question(
        AgentQuestion(
            label="Gender",
            widget="select",
            required=False,
            options=["Male", "Female", "Decline"],
            section="Voluntary Self-Identification",
        )
    )
    assert q.label == "Gender"
    assert q.widget is WidgetType.SELECT_NATIVE
    assert [o.label for o in q.options] == ["Male", "Female", "Decline"]
    # Provenance must say this came from a model, not from the DOM.
    assert q.source is SourceTier.TIER2
    assert q.label_source is LabelSource.LLM
    assert q.confidence < 1.0
    assert "no DOM locator" in q.notes
    assert "Voluntary Self-Identification" in q.notes


def test_no_options_becomes_none_not_empty_list():
    """`None` means unknown; `[]` would falsely assert 'this has no choices'."""
    q = agent_question_to_question(AgentQuestion(label="Full name", widget="text"))
    assert q.options is None
    assert q.options_unknown is True


def test_run_result_round_trip():
    result = RunResult(
        jobs=[
            JobSnapshot(
                input_url="https://example.test/job/1",
                outcome=Outcome.FORM_OPEN,
                questions=[agent_question_to_question(AgentQuestion(label="Email", required=True))],
                guard={"installed": True, "blocked": 0, "reasons": []},
            )
        ]
    )
    restored = RunResult.model_validate_json(result.model_dump_json())
    assert restored.jobs[0].outcome is Outcome.FORM_OPEN
    assert restored.jobs[0].required_count == 1
    assert restored.jobs[0].guard["installed"] is True
