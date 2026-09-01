"""Live dom_writer regression against real Chrome: proves the native-setter
write pattern actually lands, including against a React-controlled-input
simulation — the exact failure mode dom_writer.py's module docstring exists to
avoid. No LLM key needed; dom_writer takes a Question/Answer pair directly, it
never runs an Agent.

    pytest -m live tests/live/test_dom_writer_injection.py

Mirrors tests/live/test_guard_injection.py's local HTTP server fixture.
"""

import asyncio
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

from autofill_job_application.answering.models import Answer, AnswerSource
from autofill_job_application.browser import start_session
from autofill_job_application.filling import dom_writer
from autofill_job_application.models import ElementRef, Question, QuestionOption, WidgetType

pytestmark = [pytest.mark.live, pytest.mark.asyncio]

FORM_HTML = """<!doctype html><title>dom_writer test</title>
<form id="f">
  <label for="plain-text">Full Name</label>
  <input id="plain-text" name="full_name">

  <label for="react-like">React-controlled Name</label>
  <input id="react-like" name="react_name">

  <label for="country">Country</label>
  <select id="country" name="country">
    <option value="">Choose one</option>
    <option value="us">United States</option>
    <option value="ca">Canada</option>
  </select>

  <fieldset>
    <legend>Willing to relocate?</legend>
    <label><input type="radio" name="relocate" value="1"> Yes</label>
    <label><input type="radio" name="relocate" value="0"> No</label>
  </fieldset>

  <label><input type="checkbox" id="consent" name="consent"> I agree</label>
</form>
<script>
  // Simulate a React-controlled input: a plain `el.value = x` (property-level
  // write) is silently dropped, exactly like a React re-render stomping an
  // uncontrolled DOM write. The getter always reflects the real native slot,
  // so reading .value afterward tells the truth either way. A write through
  // the NATIVE prototype setter, called directly (not via `el.value = x`),
  // bypasses this instance-level override entirely -- this is the real
  // mechanism dom_writer.py's native-setter pattern exists to use.
  (() => {
    const el = document.getElementById('react-like');
    const native = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
    Object.defineProperty(el, 'value', {
      get() { return native.get.call(el); },
      set(_v) { /* no-op: simulates React reverting an uncontrolled write */ },
      configurable: true,
    });
  })();
</script>
"""


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    root = tmp_path_factory.mktemp("site")
    (root / "form.html").write_text(FORM_HTML)
    handler = partial(SimpleHTTPRequestHandler, directory=str(root))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def _q(selector: str, widget: WidgetType, *, name_attr=None, options=None) -> Question:
    return Question(
        ref=ElementRef(css_selector=selector, name_attr=name_attr),
        label=selector,
        widget=widget,
        options=options,
    )


def _a(value: str) -> Answer:
    return Answer(question_index=0, question_label="", value=value, source=AnswerSource.CONTEXT_DOC)


async def test_plain_text_field_is_written_and_reads_back(site):
    session = await start_session(headless=True)
    try:
        page = await session.must_get_current_page()
        await page.goto(f"{site}/form.html")
        await asyncio.sleep(0.5)

        result = await dom_writer.write_value(session, _q("#plain-text", WidgetType.TEXT), _a("Alex Doe"))
        assert result.write_status.value == "written", result
        assert result.value_written == "Alex Doe"
    finally:
        await session.kill()


async def test_react_controlled_input_is_written_via_native_setter(site):
    """The regression this file exists for: a plain assignment would be
    silently dropped by the page's own script (simulating React); dom_writer
    must still get the real value in, not just believe it did."""
    session = await start_session(headless=True)
    try:
        page = await session.must_get_current_page()
        await page.goto(f"{site}/form.html")
        await asyncio.sleep(0.5)

        # Prove the simulation is real: a naive assignment is dropped.
        dropped = await page.evaluate(
            "() => { const el = document.getElementById('react-like'); "
            "el.value = 'should be dropped'; return el.value; }"
        )
        assert dropped != "should be dropped", "test fixture's React simulation isn't working"

        result = await dom_writer.write_value(session, _q("#react-like", WidgetType.TEXT), _a("Alex Doe"))
        assert result.write_status.value == "written", result

        actual = await page.evaluate("() => document.getElementById('react-like').value")
        assert actual == "Alex Doe"
    finally:
        await session.kill()


async def test_select_native_writes_the_real_option_value(site):
    session = await start_session(headless=True)
    try:
        page = await session.must_get_current_page()
        await page.goto(f"{site}/form.html")
        await asyncio.sleep(0.5)

        q = _q(
            "#country",
            WidgetType.SELECT_NATIVE,
            options=[
                QuestionOption(value="us", label="United States"),
                QuestionOption(value="ca", label="Canada"),
            ],
        )
        result = await dom_writer.write_value(session, q, _a("United States"))
        assert result.write_status.value == "written", result
        assert result.value_written == "us"  # the real HTML value, not the label

        actual = await page.evaluate("() => document.getElementById('country').value")
        assert actual == "us"
    finally:
        await session.kill()


async def test_radio_group_selects_the_matching_option_by_real_value(site):
    session = await start_session(headless=True)
    try:
        page = await session.must_get_current_page()
        await page.goto(f"{site}/form.html")
        await asyncio.sleep(0.5)

        q = _q(
            "#does-not-matter-for-radio",
            WidgetType.RADIO_GROUP,
            name_attr="relocate",
            options=[
                QuestionOption(value="1", label="Yes", option_selector='input[name="relocate"][value="1"]'),
                QuestionOption(value="0", label="No", option_selector='input[name="relocate"][value="0"]'),
            ],
        )
        result = await dom_writer.write_value(session, q, _a("Yes"))
        assert result.write_status.value == "written", result

        checked_value = await page.evaluate(
            "() => document.querySelector('input[name=relocate]:checked')?.value"
        )
        assert checked_value == "1"
    finally:
        await session.kill()


async def test_checkbox_is_checked_for_an_affirmative_answer(site):
    session = await start_session(headless=True)
    try:
        page = await session.must_get_current_page()
        await page.goto(f"{site}/form.html")
        await asyncio.sleep(0.5)

        result = await dom_writer.write_value(session, _q("#consent", WidgetType.CHECKBOX), _a("Yes"))
        assert result.write_status.value == "written", result

        checked = await page.evaluate("() => document.getElementById('consent').checked")
        assert checked is True
    finally:
        await session.kill()
