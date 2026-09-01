"""The safety property for the answering agent.

Every label here must be withheld from the model. If one starts returning None,
the agent will invent a salary, a visa status, or a signature.
"""

import pytest

from autofill_job_application.answering.guardrails import (
    NEVER_ANSWER,
    REASONS,
    check,
    normalize,
)

MUST_ESCALATE = [
    ("Expected Salary", "compensation"),
    ("Desired pay range", "compensation"),
    ("What is your current CTC?", "compensation"),
    ("Are you legally authorized to work in the United States?", "work_authorization"),
    ("Will you now or in the future require sponsorship for a visa?", "work_authorization"),
    ("Do you have the right to work in the UK?", "work_authorization"),
    ("Years of experience with Python", "credentials"),
    ("How many years have you worked with Kubernetes?", "credentials"),
    ("What was your GPA?", "credentials"),
    ("Gender", "eeo_demographic"),
    ("Are you Hispanic/Latino?", "eeo_demographic"),
    ("Veteran Status", "eeo_demographic"),
    ("Disability Status", "eeo_demographic"),
    ("Voluntary Self-Identification", "eeo_demographic"),
    ("Have you ever been convicted of a felony?", "background_check"),
    ("Do you consent to a background check?", "background_check"),
    ("I certify that the information provided is true and complete", "attestation"),
    ("Electronic signature", "attestation"),
    ("Reference name", "references"),
    ("Reference email address", "references"),
]


@pytest.mark.parametrize("label,expected", MUST_ESCALATE)
def test_dangerous_questions_are_withheld(label, expected):
    hit = check(label)
    assert hit is not None, f"{label!r} would have been sent to the model"
    assert hit[0] == expected


@pytest.mark.parametrize("label,_", MUST_ESCALATE)
def test_every_escalation_explains_itself(label, _):
    """A withheld question must tell the human why, not just refuse."""
    category, reason = check(label)
    assert reason == REASONS[category]
    assert len(reason) > 20


ANSWERABLE = [
    "First Name",
    "Last Name",
    "Email",
    "Phone",
    "LinkedIn Profile",
    "Personal website",
    "Why do you want to work here?",
    "What interests you about this role?",
    "Describe a project you are proud of",
    "Preferred name",
    "How did you hear about us?",
    "Current company",
]


@pytest.mark.parametrize("label", ANSWERABLE)
def test_ordinary_questions_are_allowed_through(label):
    assert check(label) is None, f"{label!r} was withheld but is safe to draft"


def test_matching_is_case_and_punctuation_insensitive():
    assert check("EXPECTED SALARY!!!") is not None
    assert check("expected  salary") is not None


def test_help_text_is_searched_too():
    """A bland label with a dangerous hint must still be caught."""
    assert check("Additional information", "Please include your salary expectations") is not None


def test_empty_input_is_not_an_escalation():
    assert check("") is None
    assert check(None) is None


def test_normalize():
    assert normalize("  Expected   Salary?  ") == "expected salary"
    assert normalize(None) == ""


def test_every_category_has_a_reason():
    assert set(NEVER_ANSWER) == set(REASONS)


def test_word_boundaries_avoid_false_positives_on_substrings():
    """Plain substring containment would match "race" inside "embrace" or
    "terrace" (eeo_demographic), a false positive that unnecessarily withholds an
    ordinary question."""
    assert check("Do you embrace a fast-paced environment?") is None
    assert check("Does the office have a rooftop terrace?") is None
    assert check("Race") is not None
    assert check("What is your race/ethnicity?") is not None
