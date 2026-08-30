"""PARKED — not used by v1. Nothing imports this module.

It gated clicks for the deterministic navigator. v1 lets the browser-use Agent
choose its own clicks, so a gate it never calls cannot protect anything. Safety in
v1 comes instead from removing the agent's write-capable actions
(`agent_runner.EXCLUDED_ACTIONS`) and from the CDP submit guard (`guard.py`).

Its 66 tests are kept green so this stays usable if a deterministic navigator
returns.

---

The only place in the deterministic navigator permitted to authorize a click.

Design rules, in order of importance:

1. **Default deny.** Anything not positively recognized as an apply-intent control
   is refused. A new ATS phrasing costs the user one manual click; a wrong ALLOW
   sends a real job application.
2. **An open form disables clicking entirely.** Once the page has enough fields to
   be a form, our job is to read it, not to press things. This is checked before
   any name matching, so no allowlist entry can override it.
3. **One gate.** `navigate.py` is the only caller and clicks nothing else. Do not
   add a second path.
"""

from __future__ import annotations

import re
from typing import Literal

Decision = Literal["ALLOW", "DENY"]

#: A page with at least this many fields is treated as an open form.
#: Shared with navigate.py so "form is open" and "stop clicking" cannot drift apart.
MIN_FIELDS_FOR_FORM_OPEN = 3

#: Positive apply-intent. Anchored: the whole accessible name must be apply-ish, so
#: "Apply" matches but "Apply and submit application" does not.
APPLY_ALLOW = re.compile(
    r"^(apply|apply now|apply for this job|apply to this job|apply here|"
    r"start (your )?application|begin application|continue to application|"
    r"i'?m interested|view application|apply with \w+)$"
)

#: Anything submit-shaped. Searched anywhere in the name, and checked BEFORE the
#: allowlist so a control saying "Apply and submit" is denied, not allowed.
SUBMIT_DENY = re.compile(
    r"(submit|send application|send my application|finish|complete application|"
    r"confirm|agree and|i certify|i accept|final)"
)


def normalize(name: str | None) -> str:
    """Lowercase, strip punctuation noise and collapse whitespace."""
    if not name:
        return ""
    text = name.replace(" ", " ").strip().lower()
    text = re.sub(r"[‘’]", "'", text)
    text = re.sub(r"[^\w\s'’]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def classify_click(
    name: str | None,
    *,
    tag: str = "",
    input_type: str | None = None,
    in_form_with_values: bool = False,
    field_count: int = 0,
) -> Decision:
    """Decide whether clicking this control is permitted.

    `name` is the accessible name (visible text, aria-label, or value).
    `field_count` is how many form fields the page currently exposes.
    """
    # Rule 2 — an open form ends the clicking phase. Checked first, deliberately:
    # no name, tag or allowlist entry can get past this.
    if field_count >= MIN_FIELDS_FOR_FORM_OPEN:
        return "DENY"

    text = normalize(name)
    if not text:
        return "DENY"

    # Submit-shaped wins over apply-shaped whenever both appear.
    if SUBMIT_DENY.search(text):
        return "DENY"

    # A real submit control inside a form holding user input is never safe.
    if input_type == "submit" and in_form_with_values:
        return "DENY"
    if tag.lower() == "button" and input_type == "submit" and in_form_with_values:
        return "DENY"

    if APPLY_ALLOW.match(text):
        return "ALLOW"

    # Rule 1 — everything unrecognized.
    return "DENY"
