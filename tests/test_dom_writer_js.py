"""Source-string tests on dom_writer's JS templates — no browser, mirrors
tests/test_guard_js.py's approach of checking the JS text directly rather than
running it.

The one property these tests exist to protect: every write goes through the
native property setter, never a plain `el.value = x` / `el.checked = x`
assignment, which is silently reverted by React-controlled inputs (see
dom_writer.py's module docstring for why that matters).
"""

import re

from autofill_job_application.filling.dom_writer import (
    _CHECK_LIKE_JS,
    _SELECT_ARIA_JS,
    _SELECT_NATIVE_JS,
    _TEXT_LIKE_JS,
    DETERMINISTIC_WIDGETS,
    _checkbox_bool,
)
from autofill_job_application.models import WidgetType

#: A plain assignment (single "=" not immediately followed by another "=", so
#: this doesn't false-positive on the "=== " read-back comparison).
_PLAIN_ASSIGNMENT = re.compile(r"\.(value|checked)\s*=\s*[^=]")


def test_text_like_uses_native_setter_not_plain_assignment():
    assert "getOwnPropertyDescriptor" in _TEXT_LIKE_JS
    assert "'value').set" in _TEXT_LIKE_JS
    assert "setter.call(el," in _TEXT_LIKE_JS
    assert not _PLAIN_ASSIGNMENT.search(_TEXT_LIKE_JS)


def test_text_like_dispatches_input_then_change():
    input_idx = _TEXT_LIKE_JS.index("new Event('input'")
    change_idx = _TEXT_LIKE_JS.index("new Event('change'")
    assert input_idx < change_idx


def test_text_like_verifies_by_reading_back():
    assert "success: el.value ===" in _TEXT_LIKE_JS
    assert "read_back: el.value" in _TEXT_LIKE_JS


def test_select_native_uses_native_setter():
    assert "HTMLSelectElement.prototype" in _SELECT_NATIVE_JS
    assert "getOwnPropertyDescriptor" in _SELECT_NATIVE_JS
    assert not _PLAIN_ASSIGNMENT.search(_SELECT_NATIVE_JS)


def test_select_native_dispatches_input_then_change():
    input_idx = _SELECT_NATIVE_JS.index("new Event('input'")
    change_idx = _SELECT_NATIVE_JS.index("new Event('change'")
    assert input_idx < change_idx


def test_checkbox_like_uses_native_setter_for_checked():
    assert "'checked').set" in _CHECK_LIKE_JS
    assert not _PLAIN_ASSIGNMENT.search(_CHECK_LIKE_JS)
    assert "success: el.checked ===" in _CHECK_LIKE_JS


def test_deterministic_widgets_includes_select_aria_excludes_unknown():
    """SELECT_ARIA (custom combobox) is attempted here first (type to filter,
    click the match) before falling back to the residual agent on failure —
    that's the routing behavior filling/runner.py implements, not something
    this set alone can express, but the set itself must include it. UNKNOWN
    has no known interaction model at all and is always the residual agent's
    job, never dom_writer's."""
    assert WidgetType.SELECT_ARIA in DETERMINISTIC_WIDGETS
    assert WidgetType.UNKNOWN not in DETERMINISTIC_WIDGETS
    assert WidgetType.FILE not in DETERMINISTIC_WIDGETS  # has its own write_file() path


def test_select_aria_js_types_to_filter_before_reading_options():
    """The whole point of this path: type into the combobox to narrow a long
    option list (a country picker being the motivating case) rather than
    scanning/scrolling an unfiltered one."""
    assert "dispatchEvent(new Event('input'" in _SELECT_ARIA_JS
    assert "findOptions" in _SELECT_ARIA_JS
    type_idx = _SELECT_ARIA_JS.index("dispatchEvent(new Event('input'")
    read_idx = _SELECT_ARIA_JS.index("const opts = findOptions();")
    assert type_idx < read_idx


def test_select_aria_js_uses_native_setter_for_typing():
    assert "getOwnPropertyDescriptor" in _SELECT_ARIA_JS
    assert not _PLAIN_ASSIGNMENT.search(_SELECT_ARIA_JS)


def test_select_aria_js_matches_case_and_whitespace_insensitively():
    assert "toLowerCase()" in _SELECT_ARIA_JS
    assert "replace(/\\s+/g" in _SELECT_ARIA_JS


def test_select_aria_js_falls_back_to_document_wide_option_search():
    """Many comboboxes portal their listbox to document.body rather than
    nesting it near the trigger (React-Select, MUI, Radix all do this) — a
    scoped-only query would silently find nothing for those."""
    assert "document.querySelectorAll('[role=\"option\"]')" in _SELECT_ARIA_JS


def test_select_aria_js_reports_option_count_on_no_match():
    """A FAILED result here is a routing signal for filling.runner (fall back
    to the residual agent), not a final answer — it needs enough detail
    (how many options were even visible) to be worth an escalation reason."""
    assert "option_count" in _SELECT_ARIA_JS


def test_checkbox_bool_interprets_common_affirmative_words():
    for word in ("Yes", "yes", "TRUE", "I agree", "Accept", "checked"):
        assert _checkbox_bool(word) is True
    for word in ("No", "false", "", "maybe", "unsure"):
        assert _checkbox_bool(word) is False
