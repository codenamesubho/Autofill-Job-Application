"""Questions the answerer must never attempt.

These run **before** the model is called, so a listed question is never sent to
it at all. That ordering is the point: a guardrail applied to a model's output is
a filter, and a filter can be argued with. A question that never reaches the
model cannot be answered wrongly by it.

The categories are the ones where a plausible-looking invention does real damage:
a wrong salary figure anchors a negotiation, a wrong work-authorization answer is
a false statement on an application, and an attestation is a signature.
"""

from __future__ import annotations

import re

#: category -> substrings matched against a normalized question label.
NEVER_ANSWER: dict[str, tuple[str, ...]] = {
    "compensation": (
        "salary", "compensation", "desired pay", "expected pay", "pay expectation",
        "current ctc", "expected ctc", "hourly rate", "day rate", "remuneration",
    ),
    "work_authorization": (
        "work authorization", "authorized to work", "authorised to work",
        "require sponsorship", "need sponsorship", "visa status", "visa sponsorship",
        "immigration status", "right to work", "work permit", "citizenship",
    ),
    "credentials": (
        "years of experience", "years experience", "how many years",
        "gpa", "grade point", "degree conferred", "graduation date",
        "license number", "certification number", "registration number",
    ),
    "eeo_demographic": (
        "race", "ethnicity", "hispanic", "latino", "gender", "veteran",
        "disability", "sexual orientation", "self-identification", "self identify",
    ),
    "background_check": (
        "criminal", "felony", "misdemeanor", "conviction", "background check",
        "drug test", "credit check", "security clearance",
    ),
    "attestation": (
        "i certify", "i attest", "i confirm that the information",
        "under penalty", "electronic signature", "e-signature", "sign here",
        "i agree that the information", "true and complete", "true and accurate",
    ),
    "references": (
        "reference name", "reference email", "reference phone", "referee",
        "reference contact",
    ),
}

#: Why each category is withheld — shown to the human in the artifact so the
#: escalation reads as a decision rather than a failure.
REASONS: dict[str, str] = {
    "compensation": "a salary figure must be your decision, not a model's",
    "work_authorization": "a legal status must come from you; a wrong answer is a false statement",
    "credentials": "verifiable facts (years, degrees, GPA) must not be estimated",
    "eeo_demographic": "voluntary self-identification is yours to answer or decline",
    "background_check": "legal and background disclosures must come from you",
    "attestation": "an attestation is a signature and cannot be delegated",
    "references": "third-party contact details must not be produced without consent",
}


def normalize(text: str | None) -> str:
    if not text:
        return ""
    text = text.replace(" ", " ").lower()
    text = re.sub(r"[^\w\s]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def check(label: str, help_text: str | None = None) -> tuple[str, str] | None:
    """Return `(category, reason)` if this question must be escalated, else None."""
    haystack = normalize(f"{label} {help_text or ''}")
    if not haystack:
        return None
    for category, needles in NEVER_ANSWER.items():
        for needle in needles:
            # Word-bounded: plain substring containment would match "race" inside
            # "embrace" or "terrace", over-triggering on ordinary questions.
            pattern = r"\b" + re.escape(normalize(needle)) + r"\b"
            if re.search(pattern, haystack):
                return category, REASONS[category]
    return None
