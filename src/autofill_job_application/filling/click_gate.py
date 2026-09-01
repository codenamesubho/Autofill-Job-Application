"""Phase-2 click gate: governs clicks once the fill agent is loose on an open form.

Phase 1 (`click_classifier.py`, finding and clicking Apply) is default-deny: an
unrecognized click costs one manual step, a wrong ALLOW risks the wrong page.
Phase 2 is the opposite by necessity — a fill agent legitimately needs to click
things a fixed allowlist can't anticipate (an ATS's own wording for "add
another employment entry", "next", an accordion toggle, a specific radio
option). So this gate is default-ALLOW, and denies by name only.

Deliberately does NOT import guard.SUBMIT_TEXT_PATTERN or
click_classifier.SUBMIT_DENY: same intent, a separately-written pattern, so a
blind spot in one deny list isn't automatically a blind spot in both. The CDP
guard in guard.py is the second, independent backstop after this gate — even a
click this gate allows that turns out to be submit-shaped is intercepted at the
DOM event level before its default action fires.
"""

from __future__ import annotations

import re
from typing import Literal

Decision = Literal["ALLOW", "DENY"]

#: Anything terminal-shaped. Written independently of guard.SUBMIT_TEXT_PATTERN
#: and click_classifier.SUBMIT_DENY on purpose — see module docstring.
SUBMIT_LIKE = re.compile(
    r"submit|send( my)? application|finish( application)?|complete( my)? application|"
    r"confirm( and)?( submit)?|agree and (submit|continue|apply)|i certify|i accept|"
    r"i attest|final(ize)?( application)?|review and submit|apply and submit|"
    r"sign( and submit)?|e-?sign"
)


def normalize(name: str | None) -> str:
    if not name:
        return ""
    text = name.replace("\xa0", " ").strip().lower()
    text = re.sub(r"[^\w\s]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def decide(name: str | None, *, tag: str = "", input_type: str | None = None) -> Decision:
    """Decide whether a click is permitted once the form is open.

    Opposite default from click_classifier.classify_click: that gate exists to
    stop premature or wrong navigation before a form is even open. This gate
    exists to let a fill agent do the structural clicking a real form requires
    (add a row, go to the next wizard page, expand a section) without
    hand-authoring every ATS's phrasing for it — so it defaults to ALLOW and
    denies only what looks terminal.
    """
    text = normalize(name)
    if not text:
        # No accessible name at all is refused, not allowed: nothing to reason
        # about, and a genuinely structural control almost always has visible
        # text ("Add", "Next", "+").
        return "DENY"

    if SUBMIT_LIKE.search(text):
        return "DENY"

    if input_type in ("submit", "image"):
        return "DENY"
    if tag.lower() == "button" and input_type == "submit":
        return "DENY"

    return "ALLOW"
