"""Phase-2 click gate: structured like tests/test_click_classifier.py (phase 1),
but the default is deliberately inverted — see click_gate.py's module
docstring for why. Both gates get their own DENY pattern on purpose, so these
tests don't import anything from click_classifier or guard.
"""

import pytest

from autofill_job_application.filling.click_gate import decide, normalize

STRUCTURAL_ALLOWED_NAMES = [
    "Add another employment entry",
    "Add",
    "+ Add experience",
    "Remove",
    "Next",
    "Continue",  # deliberately opposite of phase-1's verdict — see below
    "Back",
    "Save and continue",
    "Upload resume",
    "Show more",
    "Expand",
    "Yes",
    "No",
    "United States",  # a radio/select option label
]


@pytest.mark.parametrize("name", STRUCTURAL_ALLOWED_NAMES)
def test_structural_clicks_are_allowed_by_default(name):
    assert decide(name) == "ALLOW"


def test_continue_disagrees_with_phase_1_click_classifier_on_purpose():
    """click_classifier.classify_click("Continue") is DENY (phase 1: nothing is
    trusted before a form is even open, so an unrecognized control is refused).
    This gate exists for after the form is open, where "Continue" is very
    often a legitimate wizard-page control the fill agent must be able to
    press — so it's ALLOW here. Both are correct for what they gate; this test
    exists so the disagreement is a documented decision, not an accident."""
    from autofill_job_application.click_classifier import classify_click

    assert classify_click("Continue", tag="button", field_count=0) == "DENY"
    assert decide("Continue") == "ALLOW"


SUBMIT_SHAPED_DENIED_NAMES = [
    "Submit",
    "Submit Application",
    "Send Application",
    "Send my application",
    "Finish",
    "Finish application",
    "Complete application",
    "Complete my application",
    "Confirm",
    "Confirm and submit",
    "I certify that the information is accurate",
    "I accept",
    "I attest",
    "Final step",
    "Review and submit",
    "Apply and submit",
    "Sign and submit",
    "E-sign",
    "Agree and submit",
    "Agree and continue",
    "Agree and apply",
]


@pytest.mark.parametrize("name", SUBMIT_SHAPED_DENIED_NAMES)
def test_submit_shaped_clicks_are_denied(name):
    assert decide(name) == "DENY"


def test_no_name_is_denied_not_allowed():
    """No accessible name at all is refused, not allowed by default — nothing
    to reason about, and a genuinely structural control almost always has
    visible text."""
    assert decide(None) == "DENY"
    assert decide("") == "DENY"
    assert decide("   ") == "DENY"


def test_submit_type_input_is_denied_even_with_unrelated_name():
    assert decide("Continue", input_type="submit") == "DENY"
    assert decide("Continue", tag="button", input_type="submit") == "DENY"


def test_matching_is_case_and_punctuation_insensitive():
    assert decide("SUBMIT APPLICATION!!!") == "DENY"
    assert decide("submit  application") == "DENY"


def test_normalize():
    assert normalize("  Submit   Application!  ") == "submit application"
    assert normalize(None) == ""
