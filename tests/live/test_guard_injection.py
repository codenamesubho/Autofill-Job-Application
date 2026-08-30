"""Live guard regression, promoted from the spike that validated the approach.

Needs real Chrome. No API key — the guard has nothing to do with the LLM.

    pytest -m live tests/live/test_guard_injection.py

The spike this came from passed: all three submission vectors were blocked and
the page never navigated away.
"""

import asyncio
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from autofill_job_application.browser import start_session
from autofill_job_application.guard import guard_status

pytestmark = [pytest.mark.live, pytest.mark.asyncio]

FORM_HTML = """<!doctype html><title>Guard test</title>
<form id="f" action="/submitted.html" method="get">
  <label for="nm">Full Name</label><input id="nm" name="nm">
  <button type="submit" id="btn">Submit application</button>
</form>
"""
DONE_HTML = "<!doctype html><title>SUBMITTED</title><h1>guard failed</h1>"


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    root = tmp_path_factory.mktemp("site")
    (root / "form.html").write_text(FORM_HTML)
    (root / "submitted.html").write_text(DONE_HTML)
    handler = partial(SimpleHTTPRequestHandler, directory=str(root))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


async def test_guard_blocks_every_submission_vector(site):
    session = await start_session(headless=True)
    try:
        page = await session.must_get_current_page()
        await page.goto(f"{site}/form.html")
        await asyncio.sleep(1)

        assert (await guard_status(session))["installed"] is True

        for js in (
            "() => document.getElementById('f').submit()",
            "() => document.getElementById('f').requestSubmit()",
            "() => document.getElementById('btn').click()",
        ):
            await page.evaluate(js)
            await asyncio.sleep(0.6)
            url = await session.get_current_page_url()
            assert "submitted" not in url.lower(), f"submission got through via {js}"

        status = await guard_status(session)
        assert status["blocked"] >= 3, status
    finally:
        await session.kill()
