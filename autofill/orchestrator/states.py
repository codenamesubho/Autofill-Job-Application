"""The application status machine (PLAN.md section 4).

PENDING -> OPENING -> DETECTED -> FILLING
        -> (NEEDS_HUMAN | BLOCKED_LOGIN | AWAITING_REVIEW)
        -> SUBMITTED_BY_HUMAN | SKIPPED | FAILED

Note what is absent: there is no SUBMITTED state the agent can reach on its own.
Only a human moves an application to SUBMITTED_BY_HUMAN.
"""

from __future__ import annotations

from enum import Enum


class Status(str, Enum):
    PENDING = "PENDING"
    OPENING = "OPENING"
    DETECTED = "DETECTED"
    FILLING = "FILLING"
    NEEDS_HUMAN = "NEEDS_HUMAN"
    BLOCKED_LOGIN = "BLOCKED_LOGIN"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    SUBMITTED_BY_HUMAN = "SUBMITTED_BY_HUMAN"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL

    @property
    def needs_human(self) -> bool:
        return self in {Status.NEEDS_HUMAN, Status.BLOCKED_LOGIN, Status.AWAITING_REVIEW}


_TERMINAL: frozenset[Status] = frozenset(
    {Status.SUBMITTED_BY_HUMAN, Status.SKIPPED, Status.FAILED}
)

# Legal transitions. Anything not listed here is a bug, not a state change.
TRANSITIONS: dict[Status, frozenset[Status]] = {
    Status.PENDING: frozenset({Status.OPENING, Status.SKIPPED}),
    Status.OPENING: frozenset(
        {Status.DETECTED, Status.BLOCKED_LOGIN, Status.FAILED, Status.SKIPPED}
    ),
    Status.DETECTED: frozenset({Status.FILLING, Status.BLOCKED_LOGIN, Status.FAILED}),
    Status.FILLING: frozenset(
        {
            Status.FILLING,  # re-entrant: each loop iteration
            Status.AWAITING_REVIEW,
            Status.NEEDS_HUMAN,
            Status.BLOCKED_LOGIN,
            Status.FAILED,
        }
    ),
    Status.NEEDS_HUMAN: frozenset(
        {Status.FILLING, Status.AWAITING_REVIEW, Status.SKIPPED, Status.FAILED}
    ),
    Status.BLOCKED_LOGIN: frozenset({Status.OPENING, Status.SKIPPED, Status.FAILED}),
    Status.AWAITING_REVIEW: frozenset(
        {Status.SUBMITTED_BY_HUMAN, Status.SKIPPED, Status.FILLING, Status.FAILED}
    ),
    Status.SUBMITTED_BY_HUMAN: frozenset(),
    Status.SKIPPED: frozenset(),
    Status.FAILED: frozenset({Status.PENDING}),  # --retry failed
}


class IllegalTransition(RuntimeError):
    def __init__(self, frm: Status, to: Status) -> None:
        super().__init__(f"illegal status transition: {frm.value} -> {to.value}")
        self.frm = frm
        self.to = to


def can_transition(frm: Status, to: Status) -> bool:
    return to in TRANSITIONS[frm]


def assert_transition(frm: Status, to: Status) -> None:
    if not can_transition(frm, to):
        raise IllegalTransition(frm, to)
