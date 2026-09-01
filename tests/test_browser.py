"""build_profile()'s keep_alive setting — a regression test for a real bug
hit live: without keep_alive=True, browser_use.Agent.run() kills the shared
browser session on its own completion (agent/service.py's Agent.close():
"Only close browser if keep_alive is False"). Every caller here shares one
session across more than one Agent.run() call — autofill-snapshot across every
job URL in a batch, autofill-fill across phase 1 and each residual-turn batch
against the very same session — so an Agent finishing must never be able to
kill a session its caller still needs.
"""

from autofill_job_application.browser import build_profile


def test_profile_keeps_the_browser_alive_after_an_agent_run_completes():
    assert build_profile().keep_alive is True


def test_keep_alive_holds_regardless_of_other_options():
    assert build_profile(headless=False).keep_alive is True
