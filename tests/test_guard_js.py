"""Guard source checks.

Whether the guard actually blocks a submission can only be proven in a browser
(tests/live/test_guard_injection.py does that, and it passed as a spike). What
this file does is cheaper and still worthwhile: it fails if someone quietly
removes one of the four interception points during a refactor.
"""

import re

from autofill_job_application.guard import SUBMIT_GUARD_JS, SUBMIT_TEXT_PATTERN


def test_all_four_interception_points_present():
    assert "HTMLFormElement.prototype" in SUBMIT_GUARD_JS
    assert "proto.submit" in SUBMIT_GUARD_JS
    assert "proto.requestSubmit" in SUBMIT_GUARD_JS
    assert "addEventListener('submit'" in SUBMIT_GUARD_JS
    assert "addEventListener('click'" in SUBMIT_GUARD_JS
    assert "addEventListener('keydown'" in SUBMIT_GUARD_JS


def test_listeners_use_capture_phase():
    """Capture phase is what lets the guard run before the page's own handlers."""
    for _, tail in re.findall(r"addEventListener\('(\w+)'(.*?)\}, true\);", SUBMIT_GUARD_JS, re.S):
        pass
    assert SUBMIT_GUARD_JS.count("}, true);") >= 3


def test_pattern_is_interpolated_not_left_as_placeholder():
    assert "__SUBMIT_TEXT_PATTERN__" not in SUBMIT_GUARD_JS
    assert "submit|send application" in SUBMIT_GUARD_JS


def test_guard_is_idempotent():
    """Re-injection per target must not reset the blocked counter."""
    assert "if (window.__autofill_guard) return;" in SUBMIT_GUARD_JS


def test_counters_exposed_for_the_artifact():
    assert "__autofill_blocked" in SUBMIT_GUARD_JS
    assert "__autofill_blocked_reasons" in SUBMIT_GUARD_JS


def test_click_matcher_covers_non_button_controls():
    """Many ATS submit controls are <a> or <div role=button>, not <button>."""
    assert "[role=button]" in SUBMIT_GUARD_JS
    assert re.search(r"closest\('button[^']*a'\)", SUBMIT_GUARD_JS)


def test_submit_pattern_matches_real_control_text():
    pattern = re.compile(SUBMIT_TEXT_PATTERN, re.I)
    for text in (
        "Submit application",
        "Submit",
        "Send application",
        "Finish",
        "Complete application",
        "I certify that the above is true",
        "Apply and submit",
    ):
        assert pattern.search(text), text


def test_submit_pattern_does_not_match_benign_controls():
    pattern = re.compile(SUBMIT_TEXT_PATTERN, re.I)
    for text in ("Apply", "Apply now", "Start application", "Back", "Save draft"):
        assert not pattern.search(text), text


def test_enter_key_is_intercepted_but_textarea_is_exempt():
    """Enter submits a form implicitly, but must still work inside a textarea."""
    assert "'Enter'" in SUBMIT_GUARD_JS
    assert "TEXTAREA" in SUBMIT_GUARD_JS
