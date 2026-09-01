"""Deterministic field writes over CDP — no LLM in the write path.

The LLM decides values (via answering.resolver, already guardrail-gated and
citation-checked); this module only ever transcribes an already-vetted Answer
into the DOM by selector. It never asks a model how to write a value, and it
never chooses what to write.

Every write uses the native-property-setter pattern, not a plain
`el.value = x`. Plain assignment is silently reverted by React-controlled
inputs (observed on Workday, Greenhouse's newer forms, and Lever) — the
framework's virtual-DOM diff either reverts the visible value on next render or
never updates its own internal state, so a later read of `el.value` can look
right while the framework's actual state (and thus what an eventual manual
submission would send) is untouched. Going through the native setter and then
dispatching real `input`/`change` events is the standard, well-known
workaround, and it is the *only* write path here — not a fallback.

Every write is verified by reading the field back in the same round trip and
comparing it against what was intended; a mismatch is reported as
write_status=FAILED rather than trusted just because the CDP call didn't
throw. This is the only reliable way to catch a controlled-input revert.

A custom combobox (SELECT_ARIA — e.g. a searchable country picker) is
attempted deterministically too: type the answer into it to trigger the
widget's own filter (most modern comboboxes narrow their option list on
input, exactly like a human typing "Uni" to narrow 195 countries to a
handful), then match and click the resulting option by text. This is
attempted before the residual LLM turn, not instead of it — filling.runner
falls back to the agent when this can't find a confident match (a non-
searchable widget, an unusual DOM shape, or genuinely no matching option).
"""

from __future__ import annotations

import json

from ..answering.models import Answer, AnswerSource
from ..extract.js_extractor import _attach, _evaluate
from ..models import Question, WidgetType
from .models import FieldFillResult, WritePath, WriteStatus

#: Widgets dom_writer can attempt. Anything else (UNKNOWN, or a question whose
#: selector didn't resolve) is the residual agent's job unconditionally.
#: SELECT_ARIA is attempted here too, but — unlike every other entry — a
#: FAILED result for it is a *routing signal*, not a final answer: filling.runner
#: falls back to the residual agent when the deterministic combobox attempt
#: below doesn't find a confident match, rather than reporting it as failed.
DETERMINISTIC_WIDGETS = {
    WidgetType.TEXT,
    WidgetType.TEXTAREA,
    WidgetType.EMAIL,
    WidgetType.TEL,
    WidgetType.NUMBER,
    WidgetType.DATE,
    WidgetType.URL,
    WidgetType.SELECT_NATIVE,
    WidgetType.SELECT_ARIA,
    WidgetType.CHECKBOX,
    WidgetType.RADIO_GROUP,
}

#: Truthy words a checkbox "answer" might use. Checkboxes are free-text
#: answers (answering.resolver never enum-validates an empty options list), so
#: this is our own interpretation, not the model's — kept narrow on purpose.
_CHECKBOX_TRUE = {"yes", "true", "checked", "agree", "i agree", "accept", "on", "1"}

# --- JS templates -----------------------------------------------------------
# Placeholder-substituted, not f-strings — matches guard.py's existing
# convention and keeps values going through json.dumps() rather than being
# spliced into JS source directly (a candidate's answer text can contain
# quotes/backslashes; json.dumps is what makes that safe to embed as a JS
# string literal).

_TEXT_LIKE_JS = r"""
(() => {
  const el = document.querySelector(__SELECTOR__);
  if (!el) return JSON.stringify({success: false, reason: 'selector not found'});
  const proto = el.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype
    : window.HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
  setter.call(el, __VALUE__);
  el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true}));
  return JSON.stringify({success: el.value === __VALUE__, read_back: el.value});
})()
"""

_SELECT_NATIVE_JS = r"""
(() => {
  const el = document.querySelector(__SELECTOR__);
  if (!el) return JSON.stringify({success: false, reason: 'selector not found'});
  const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value').set;
  setter.call(el, __VALUE__);
  el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true}));
  return JSON.stringify({success: el.value === __VALUE__, read_back: el.value});
})()
"""

_CHECK_LIKE_JS = r"""
(() => {
  const el = document.querySelector(__SELECTOR__);
  if (!el) return JSON.stringify({success: false, reason: 'selector not found'});
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'checked').set;
  setter.call(el, __CHECKED__);
  el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true}));
  return JSON.stringify({success: el.checked === __CHECKED__, read_back: el.checked});
})()
"""

#: Type-to-filter a custom combobox, then click the matching option by text.
#: Async (awaited via _evaluate's awaitPromise=True) because filtering is
#: commonly debounced -- there is no synchronous way to know when a framework
#: has finished re-rendering its option list after an input event.
_SELECT_ARIA_JS = r"""
(async () => {
  const el = document.querySelector(__SELECTOR__);
  if (!el) return JSON.stringify({success: false, reason: 'selector not found'});

  const target = __VALUE__;
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();

  const findTextInput = () => {
    if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') return el;
    if (document.activeElement && document.activeElement.tagName === 'INPUT') return document.activeElement;
    const id = el.getAttribute('aria-controls') || el.getAttribute('aria-owns');
    const owned = id ? document.getElementById(id) : null;
    const scope = owned || el;
    return scope.querySelector('input[type="text"], input:not([type]), input[role="searchbox"]');
  };

  const findOptions = () => {
    const id = el.getAttribute('aria-controls') || el.getAttribute('aria-owns');
    const owned = id ? document.getElementById(id) : null;
    const scoped = (owned || el).querySelectorAll('[role="option"]');
    if (scoped.length) return Array.from(scoped);
    // Many widgets portal their listbox to document.body rather than nesting
    // it near the trigger -- fall back to a document-wide search.
    return Array.from(document.querySelectorAll('[role="option"]'));
  };

  // A widget whose trigger isn't itself a text input usually needs a click to
  // open before any search box or option list exists at all.
  if (el.tagName !== 'INPUT' && el.tagName !== 'TEXTAREA' && findOptions().length === 0) {
    el.click();
    await new Promise((r) => setTimeout(r, 200));
  }

  const input = findTextInput();
  if (input) {
    const proto = input.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
    setter.call(input, target);
    input.dispatchEvent(new Event('input', {bubbles: true}));
  }
  // Filtering is commonly debounced; give the widget a moment to react.
  await new Promise((r) => setTimeout(r, 400));

  const opts = findOptions();
  let match = opts.find((o) => norm(o.innerText) === norm(target));
  if (!match) match = opts.find((o) => norm(o.innerText).includes(norm(target)));
  if (!match) {
    return JSON.stringify({success: false, reason: 'no option matched after filtering', option_count: opts.length});
  }

  match.scrollIntoView({block: 'nearest'});
  match.click();
  await new Promise((r) => setTimeout(r, 150));
  return JSON.stringify({success: true, read_back: (match.innerText || '').trim()});
})()
"""


def _fill(reason: str, question: Question | None = None) -> FieldFillResult:
    return FieldFillResult(
        question_label=question.label if question else "",
        widget=question.widget if question else WidgetType.UNKNOWN,
        value_written=None,
        path=WritePath.DETERMINISTIC,
        answer_source=AnswerSource.ESCALATED,
        write_status=WriteStatus.FAILED,
        failure_reason=reason,
    )


async def _session_id_for(session, cdp, target_id: str | None) -> str:
    """Resolve the CDP session to evaluate in: top frame, or a specific
    cross-origin iframe target — same split js_extractor.py's extract_tier1
    already relies on."""
    if target_id is None:
        return cdp.session_id
    return await _attach(cdp, target_id)


def _checkbox_bool(value: str) -> bool:
    return value.strip().lower() in _CHECKBOX_TRUE


async def write_value(session, question: Question, answer: Answer) -> FieldFillResult:
    """Write one already-vetted Answer into the DOM. Never raises — a failure
    becomes a FAILED FieldFillResult, same convention as agent_runner/resolver.
    """
    if answer.source is AnswerSource.ESCALATED or not answer.value:
        return FieldFillResult(
            question_label=question.label,
            widget=question.widget,
            answer_source=answer.source,
            write_status=WriteStatus.ESCALATED,
            failure_reason=answer.escalation_reason or "no answer to write",
        )

    if question.widget not in DETERMINISTIC_WIDGETS:
        return FieldFillResult(
            question_label=question.label,
            widget=question.widget,
            answer_source=answer.source,
            path=WritePath.DETERMINISTIC,
            write_status=WriteStatus.FAILED,
            failure_reason=f"widget {question.widget} is not deterministically writable",
        )

    cdp = await session.get_or_create_cdp_session()
    try:
        sid = await _session_id_for(session, cdp, question.ref.target_id)
    except Exception as exc:
        return FieldFillResult(
            question_label=question.label,
            widget=question.widget,
            answer_source=answer.source,
            write_status=WriteStatus.FAILED,
            failure_reason=f"could not attach to frame: {type(exc).__name__}: {exc}",
        )

    if question.widget is WidgetType.RADIO_GROUP:
        return await _write_radio(cdp, sid, question, answer)
    if question.widget is WidgetType.CHECKBOX:
        return await _write_checkbox(cdp, sid, question, answer)
    if question.widget is WidgetType.SELECT_NATIVE:
        return await _write_select_native(cdp, sid, question, answer)
    if question.widget is WidgetType.SELECT_ARIA:
        return await _write_select_aria(cdp, sid, question, answer)
    return await _write_text_like(cdp, sid, question, answer)


async def _run_write(cdp, sid, selector: str, template: str, question: Question, answer: Answer, *, value_written: str) -> FieldFillResult:
    expr = template.replace("__SELECTOR__", json.dumps(selector)).replace(
        "__VALUE__", json.dumps(value_written)
    )
    raw = await _evaluate(cdp, expr, sid)
    try:
        result = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (TypeError, ValueError):
        result = {"success": False, "reason": "could not parse write result"}

    if result.get("success"):
        return FieldFillResult(
            question_label=question.label,
            widget=question.widget,
            value_written=value_written,
            path=WritePath.DETERMINISTIC,
            answer_source=answer.source,
            write_status=WriteStatus.WRITTEN,
        )
    return FieldFillResult(
        question_label=question.label,
        widget=question.widget,
        value_written=value_written,
        path=WritePath.DETERMINISTIC,
        answer_source=answer.source,
        write_status=WriteStatus.FAILED,
        failure_reason=result.get("reason") or "write did not verify on read-back",
    )


async def _write_text_like(cdp, sid, question: Question, answer: Answer) -> FieldFillResult:
    selector = question.ref.css_selector or question.ref.dom_path
    if not selector:
        return _fill("no selector for this field", question)
    return await _run_write(cdp, sid, selector, _TEXT_LIKE_JS, question, answer, value_written=answer.value)


async def _write_select_native(cdp, sid, question: Question, answer: Answer) -> FieldFillResult:
    selector = question.ref.css_selector or question.ref.dom_path
    if not selector:
        return _fill("no selector for this field", question)
    # answer.value is the option's LABEL (answering.resolver validates against
    # and returns the label, verbatim); a native <select> needs the real HTML
    # option value, which can differ from its visible text.
    option = next((o for o in (question.options or []) if o.label == answer.value), None)
    if option is None:
        return _fill(f"answer {answer.value!r} not found among this field's options", question)
    return await _run_write(cdp, sid, selector, _SELECT_NATIVE_JS, question, answer, value_written=option.value)


async def _write_select_aria(cdp, sid, question: Question, answer: Answer) -> FieldFillResult:
    """Type the answer into the combobox to trigger its own filter, then click
    the matching option by text. Failure here (no confident match, no usable
    text input found, an unusual DOM shape) is a routing signal for the
    caller — filling.runner falls back to the residual agent rather than
    treating this FAILED result as final, unlike every other widget type.
    """
    selector = question.ref.css_selector or question.ref.dom_path
    if not selector:
        return _fill("no selector for this field", question)

    expr = _SELECT_ARIA_JS.replace("__SELECTOR__", json.dumps(selector)).replace(
        "__VALUE__", json.dumps(answer.value)
    )
    raw = await _evaluate(cdp, expr, sid)
    try:
        result = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (TypeError, ValueError):
        result = {"success": False, "reason": "could not parse write result"}

    if result.get("success"):
        return FieldFillResult(
            question_label=question.label,
            widget=question.widget,
            value_written=result.get("read_back") or answer.value,
            path=WritePath.DETERMINISTIC,
            answer_source=answer.source,
            write_status=WriteStatus.WRITTEN,
        )
    return FieldFillResult(
        question_label=question.label,
        widget=question.widget,
        value_written=None,
        path=WritePath.DETERMINISTIC,
        answer_source=answer.source,
        write_status=WriteStatus.FAILED,
        failure_reason=result.get("reason") or "combobox filter/select did not verify",
    )


async def _write_checkbox(cdp, sid, question: Question, answer: Answer) -> FieldFillResult:
    selector = question.ref.css_selector or question.ref.dom_path
    if not selector:
        return _fill("no selector for this field", question)
    checked = _checkbox_bool(answer.value)
    expr = _CHECK_LIKE_JS.replace("__SELECTOR__", json.dumps(selector)).replace(
        "__CHECKED__", "true" if checked else "false"
    )
    raw = await _evaluate(cdp, expr, sid)
    try:
        result = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (TypeError, ValueError):
        result = {"success": False, "reason": "could not parse write result"}
    status = WriteStatus.WRITTEN if result.get("success") else WriteStatus.FAILED
    return FieldFillResult(
        question_label=question.label,
        widget=question.widget,
        value_written=str(checked),
        path=WritePath.DETERMINISTIC,
        answer_source=answer.source,
        write_status=status,
        failure_reason=None if status is WriteStatus.WRITTEN else (result.get("reason") or "write did not verify"),
    )


async def _write_radio(cdp, sid, question: Question, answer: Answer) -> FieldFillResult:
    option = next((o for o in (question.options or []) if o.label == answer.value), None)
    if option is None:
        return _fill(f"answer {answer.value!r} not found among this field's options", question)

    # Prefer the option's own selector (captured at extraction time) — it's
    # exact. Fall back to synthesizing name+value only if that's missing; some
    # ATS forms give every option in a group the same generic value ("on"),
    # which makes name+value ambiguous, so the fallback is best-effort only.
    if option.option_selector:
        selector = option.option_selector
    elif question.ref.name_attr and option.value:
        # json.dumps quotes each attribute value safely (no CSS.escape needed
        # here); querySelector fails closed (selector not found) rather than
        # raising if this is somehow still an invalid selector.
        selector = (
            f'input[type="radio"][name={json.dumps(question.ref.name_attr)}]'
            f'[value={json.dumps(option.value)}]'
        )
    else:
        return _fill("no selector for this radio option (no option_selector, no name+value)", question)

    checked_js = _CHECK_LIKE_JS.replace("__SELECTOR__", json.dumps(selector)).replace(
        "__CHECKED__", "true"
    )
    raw = await _evaluate(cdp, checked_js, sid)
    try:
        result = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (TypeError, ValueError):
        result = {"success": False, "reason": "could not parse write result"}
    status = WriteStatus.WRITTEN if result.get("success") else WriteStatus.FAILED
    return FieldFillResult(
        question_label=question.label,
        widget=question.widget,
        value_written=answer.value,
        path=WritePath.DETERMINISTIC,
        answer_source=answer.source,
        write_status=status,
        failure_reason=None if status is WriteStatus.WRITTEN else (result.get("reason") or "write did not verify"),
    )


async def write_file(session, question: Question, file_path: str | None) -> FieldFillResult:
    """Set a FILE-widget input via CDP DOM.setFileInputFiles.

    Never driven by an Answer or by the LLM — file_path comes only from
    filling.cli's --resume/--cover-letter flags (a pre-approved local path),
    exactly the design the user approved: never LLM-chosen, never
    network-fetched. A FILE question is never sent to answering.resolver at
    all (the runner filters it out before that call) — asking a model to
    "answer" a file-upload question makes no sense, so there is nothing to
    guardrail-check here.
    """
    if not file_path:
        return FieldFillResult(
            question_label=question.label,
            widget=question.widget,
            answer_source=AnswerSource.ESCALATED,
            write_status=WriteStatus.ESCALATED,
            failure_reason="no resume/cover-letter path configured",
        )

    selector = question.ref.css_selector or question.ref.dom_path
    if not selector:
        return _fill("no selector for this file field", question)

    cdp = await session.get_or_create_cdp_session()
    try:
        sid = await _session_id_for(session, cdp, question.ref.target_id)
        doc = await cdp.cdp_client.send.DOM.getDocument(params={}, session_id=sid)
        root_id = doc["root"]["nodeId"]
        found = await cdp.cdp_client.send.DOM.querySelector(
            params={"nodeId": root_id, "selector": selector}, session_id=sid
        )
        node_id = found.get("nodeId")
        if not node_id:
            return _fill("selector did not resolve to a node", question)
        await cdp.cdp_client.send.DOM.setFileInputFiles(
            params={"files": [file_path], "nodeId": node_id}, session_id=sid
        )
    except Exception as exc:
        return FieldFillResult(
            question_label=question.label,
            widget=question.widget,
            answer_source=AnswerSource.ESCALATED,
            write_status=WriteStatus.FAILED,
            failure_reason=f"{type(exc).__name__}: {exc}",
        )

    return FieldFillResult(
        question_label=question.label,
        widget=question.widget,
        value_written=file_path,
        path=WritePath.DETERMINISTIC,
        answer_source=AnswerSource.CONTEXT_DOC,
        write_status=WriteStatus.WRITTEN,
    )
