"""Submit blocking, injected into every page before its own scripts run.

In this version an autonomous agent decides what to click, so this guard is a
primary defense rather than a backstop. It is deliberately broader than the
version proven in the spike: the agent clicks by element index, and plenty of ATS
submit controls are `<div role=button>` or `<a>` rather than `button[type=submit]`,
so matching on accessible *text* matters as much as matching on type.

Verified against real Chrome: the prototype overrides and the click interception
each blocked a real submission, and the page never navigated.
"""

from __future__ import annotations

#: Text that marks a control as terminal. Kept in sync with the docstring in
#: click_classifier.SUBMIT_DENY, though the two are independent by design.
SUBMIT_TEXT_PATTERN = (
    r"submit|send application|send my application|finish|complete application|"
    r"confirm and|agree and|i certify|i accept|apply and submit"
)

SUBMIT_GUARD_JS = r"""
(() => {
  if (window.__autofill_guard) return;
  window.__autofill_guard = 1;
  window.__autofill_blocked = 0;
  window.__autofill_blocked_reasons = [];

  const PATTERN = /__SUBMIT_TEXT_PATTERN__/i;

  const note = (why) => {
    window.__autofill_blocked++;
    try { window.__autofill_blocked_reasons.push(String(why).slice(0, 120)); } catch (e) {}
    try { console.warn('autofill: blocked submission -', why); } catch (e) {}
  };

  // 1. Programmatic submission.
  try {
    const proto = HTMLFormElement.prototype;
    proto.submit = function () { note('form.submit()'); };
    proto.requestSubmit = function () { note('form.requestSubmit()'); };
  } catch (e) {}

  // 2. Submit events raised by any means.
  document.addEventListener('submit', (e) => {
    e.preventDefault();
    e.stopImmediatePropagation();
    note('submit event on ' + (e.target && e.target.id ? '#' + e.target.id : 'form'));
  }, true);

  // 3. Clicks on anything that looks terminal - by type OR by accessible text.
  document.addEventListener('click', (e) => {
    const t = e.target;
    if (!t || !t.closest) return;
    const el = t.closest('button, input[type=submit], input[type=image], [role=button], a');
    if (!el) return;
    const text = (
      (el.innerText || '') + ' ' + (el.value || '') + ' ' +
      (el.getAttribute('aria-label') || '') + ' ' + (el.getAttribute('title') || '')
    ).replace(/\s+/g, ' ').trim();
    const isSubmitType = (el.type === 'submit' || el.type === 'image');
    if (isSubmitType || PATTERN.test(text)) {
      e.preventDefault();
      e.stopImmediatePropagation();
      note('click on "' + text.slice(0, 60) + '"');
    }
  }, true);

  // 4. Enter inside a form implicitly submits it.
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    const el = e.target;
    if (el && el.closest && el.closest('form') && el.tagName !== 'TEXTAREA') {
      e.preventDefault();
      e.stopImmediatePropagation();
      note('Enter key inside form');
    }
  }, true);
})();
""".replace("__SUBMIT_TEXT_PATTERN__", SUBMIT_TEXT_PATTERN)


async def inject_submit_guard(session, session_id=None) -> None:
    """Install the guard on one CDP target.

    `runImmediately` covers the already-loaded document; the registration covers
    every subsequent navigation in that target.
    """
    cdp = await session.get_or_create_cdp_session()
    await cdp.cdp_client.send.Page.addScriptToEvaluateOnNewDocument(
        params={"source": SUBMIT_GUARD_JS, "runImmediately": True},
        session_id=session_id or cdp.session_id,
    )


async def guard_status(session) -> dict:
    """Read the guard's counters back out of the page, for the run artifact.

    A non-zero `blocked` count means something actually tried to submit — worth
    surfacing loudly rather than leaving buried in the browser console.
    """
    cdp = await session.get_or_create_cdp_session()
    res = await cdp.cdp_client.send.Runtime.evaluate(
        params={
            "expression": (
                "({installed: !!window.__autofill_guard,"
                " blocked: window.__autofill_blocked || 0,"
                " reasons: (window.__autofill_blocked_reasons || []).slice(0, 10)})"
            ),
            "returnByValue": True,
        },
        session_id=cdp.session_id,
    )
    return res.get("result", {}).get("value") or {"installed": False, "blocked": 0, "reasons": []}


async def inject_into_all_targets(session) -> int:
    """Install the guard on the page target and every non-trivial iframe target.

    Cross-origin frames are separate CDP sessions and do not inherit the parent's
    init script — measured in the spike, not assumed.
    """
    cdp = await session.get_or_create_cdp_session()
    await inject_submit_guard(session, cdp.session_id)
    count = 1
    try:
        targets = await cdp.cdp_client.send.Target.getTargets()
    except Exception:
        return count
    for t in targets.get("targetInfos", []):
        if t.get("type") != "iframe":
            continue
        try:
            att = await cdp.cdp_client.send.Target.attachToTarget(
                params={"targetId": t["targetId"], "flatten": True}
            )
            sid = att["sessionId"]
            await cdp.cdp_client.send.Page.enable(session_id=sid)
            await cdp.cdp_client.send.Page.addScriptToEvaluateOnNewDocument(
                params={"source": SUBMIT_GUARD_JS, "runImmediately": True},
                session_id=sid,
            )
            count += 1
        except Exception:
            continue
    return count
