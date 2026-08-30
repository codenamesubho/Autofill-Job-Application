"""The safety property lives here.

If any DENY case in this file starts returning ALLOW, the package can send a real
job application. Treat a failure here as a stop-the-line defect, not a flaky test.
"""

import pytest

from autofill_job_application.click_classifier import (
    MIN_FIELDS_FOR_FORM_OPEN,
    classify_click,
    normalize,
)

# --- controls we are willing to click -------------------------------------

ALLOWED_NAMES = [
    "Apply",
    "Apply now",
    "Apply Now",
    "APPLY NOW",
    "  apply   now  ",
    "Apply for this job",
    "Apply to this job",
    "Apply here",
    "Start application",
    "Start your application",
    "Begin application",
    "Continue to application",
    "I'm interested",
    "I’m interested",  # curly apostrophe, as rendered by many ATSs
    "Apply with LinkedIn",
]


@pytest.mark.parametrize("name", ALLOWED_NAMES)
def test_apply_intent_is_allowed(name):
    assert classify_click(name, tag="a", field_count=0) == "ALLOW"


# --- controls that must never be clicked ----------------------------------

DENIED_NAMES = [
    # explicit submission
    "Submit",
    "Submit application",
    "Submit Application",
    "Send application",
    "Send my application",
    "Finish",
    "Complete application",
    "Confirm",
    "Confirm and submit",
    "I certify that the above is true",
    "I accept the terms",
    "Final step",
    # submit-shaped wins even when apply-shaped text is also present
    "Apply and submit",
    "Apply now and submit application",
    # unrecognized -> default deny
    "",
    "   ",
    "Next",
    "Continue",
    "Save",
    "Save and continue",
    "Learn more",
    "Share this job",
    "Back",
    "Sign in",
    "Create account",
    "Upload resume",
    "Add to favourites",
    "Delete",
    "Yes",
    "OK",
]


@pytest.mark.parametrize("name", DENIED_NAMES)
def test_non_apply_controls_are_denied(name):
    assert classify_click(name, tag="button", field_count=0) == "DENY"


def test_none_name_is_denied():
    assert classify_click(None) == "DENY"


# --- rule 2: an open form disables clicking entirely ----------------------


@pytest.mark.parametrize("name", ALLOWED_NAMES)
def test_open_form_denies_even_apply_controls(name):
    """The override that makes the safety argument airtight: once the form is
    open, nothing is clickable — not even a control we would otherwise allow."""
    assert classify_click(name, tag="a", field_count=MIN_FIELDS_FOR_FORM_OPEN) == "DENY"


def test_field_count_boundary():
    assert classify_click("Apply", field_count=MIN_FIELDS_FOR_FORM_OPEN - 1) == "ALLOW"
    assert classify_click("Apply", field_count=MIN_FIELDS_FOR_FORM_OPEN) == "DENY"
    assert classify_click("Apply", field_count=99) == "DENY"


# --- submit-typed controls inside a populated form ------------------------


def test_submit_input_in_filled_form_is_denied():
    assert (
        classify_click(
            "Apply", tag="input", input_type="submit", in_form_with_values=True
        )
        == "DENY"
    )
    assert (
        classify_click(
            "Apply", tag="button", input_type="submit", in_form_with_values=True
        )
        == "DENY"
    )


def test_submit_typed_control_in_empty_form_still_needs_apply_intent():
    assert (
        classify_click("Apply", tag="input", input_type="submit", in_form_with_values=False)
        == "ALLOW"
    )
    assert (
        classify_click("Send it", tag="input", input_type="submit", in_form_with_values=False)
        == "DENY"
    )


# --- normalize ------------------------------------------------------------


def test_normalize_collapses_and_strips():
    assert normalize("  Apply   NOW ") == "apply now"
    assert normalize("Apply now") == "apply now"  # non-breaking space
    assert normalize("Apply now!") == "apply now"
    assert normalize(None) == ""


def test_default_deny_is_the_fallthrough():
    """Anything genuinely novel must be refused rather than attempted."""
    for name in ("Aplicar ahora", "Postuler", "立即申请", "Bewerben"):
        assert classify_click(name) == "DENY"
