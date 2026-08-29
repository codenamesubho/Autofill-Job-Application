"""Invariants specific to the v2 generic-path design (no per-vendor adapters)."""

from autofill.models import (
    FieldType,
    FormSchema,
    ForwardControl,
    Locator,
    Option,
    Question,
    WidgetKind,
)

from tests.test_models import make_question


def test_combobox_is_enum_typed_so_answers_stay_constrained():
    """A custom dropdown is still a closed set - no free text into it."""
    assert FieldType.COMBOBOX.is_enum
    assert FieldType.SELECT.is_enum
    assert not FieldType.TEXTAREA.is_enum


def test_option_probing_is_lazy_but_tracked():
    assert make_question(type=FieldType.COMBOBOX).needs_option_probe
    assert not make_question(
        type=FieldType.COMBOBOX, options_probed=True
    ).needs_option_probe
    assert not make_question(
        type=FieldType.COMBOBOX, options=[Option(value="a", label="A")]
    ).needs_option_probe


def test_field_id_distinguishes_identical_fields_in_different_frames():
    top = Question.make_field_id("jobs.test", [], "email")
    framed = Question.make_field_id("jobs.test", ["https://embed.test/form"], "email")
    assert top != framed
    assert top == Question.make_field_id("jobs.test", [], "  EMAIL ")


def test_locator_knows_when_it_is_inside_a_frame():
    assert not Locator(value="#a").in_frame
    assert Locator(value="#a", frame_path=["https://embed.test/f"]).in_frame


def test_only_advance_may_be_clicked():
    """Ambiguity must resolve to stopping, never to clicking."""
    assert ForwardControl.ADVANCE.may_click
    assert not ForwardControl.AMBIGUOUS.may_click
    assert not ForwardControl.TERMINAL_SUBMIT.may_click
    assert not ForwardControl.NONE.may_click


def test_widget_kind_carries_no_vendor_names():
    assert {w.value for w in WidgetKind} == {"native", "aria", "custom", "unknown"}


def test_low_confidence_selects_tier_two_candidates():
    schema = FormSchema(
        url="https://example.test/apply",
        questions=[
            make_question(field_id="a", confidence=0.4),
            make_question(field_id="b", confidence=0.9),
        ],
    )
    assert [q.field_id for q in schema.low_confidence(0.75)] == ["a"]
    assert schema.tier == 1
