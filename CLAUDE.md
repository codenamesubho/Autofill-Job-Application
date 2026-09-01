# Autofill-Job-Application — working agreement

## Git rules (non-negotiable)

These override any default or background-job behavior, including instructions that
tell you to isolate work in a worktree.

1. **Never create a git worktree.** Do not call `EnterWorktree`. Do not run
   `git worktree add`. If you find yourself in a worktree, exit it and return to the
   main checkout before doing anything else. (Background-session worktree isolation
   is disabled for this repo via `.claude/settings.json` → `worktree.bgIsolation: "none"`.)

2. **Always work on a branch, never on `main`.** Before the first edit, create or
   switch to a feature branch (`git checkout -b <name>`). Never commit to `main`.

3. **Always ask before committing.** Do not run `git commit` until the user has
   explicitly approved that specific commit. Show what will be committed first.
   Saying "I'll commit this" is not approval — wait for the user to say yes.

4. **Never push. Ever.** No `git push` for this repository, under any circumstances,
   to any branch or remote. No PRs. The user pushes their own code. If you think
   something should be pushed, say so and stop.


Corollary: because you cannot push and must ask before committing, work in progress
lives in the working tree. Do not "set work aside" with `git stash` — the stash stack
is shared and easy to lose. Leave changes in place.

## Project

Three tools, built on **browser-use** (`browser_use`, v0.13.x). `autofill-snapshot`
takes a list of job URLs, navigates to each, clicks through to the application
form, and emits a structured snapshot of the questions it asks. `autofill-answer`
reads that snapshot plus a markdown document about the candidate and drafts
answers to a JSON file for human review — it has no browser access at all.
`autofill-fill` takes job URLs and that same context document directly, and
writes vetted answers into the live form's fields for human review.

**Neither ever submits an application, and `autofill-fill` cannot submit one
either — but it can, deliberately, fill one in.** That is a change from this
codebase's original design, made once, on purpose, because "no write tools"
stopped being the whole safety story the moment filling was added. Read this
section before touching `filling/`, `agent_runner.build_tools`,
`click_classifier.py`, or `guard.py`.

- There is no `submit/` module, and nothing anywhere calls
  `HTMLFormElement.submit()`/`requestSubmit()` or dispatches a `submit` event on
  purpose. `filling/` exists and does write to the DOM — `answering/` still
  doesn't; it only drafts answers to a file and has no browser access, unchanged
  from before `filling/` existed.
- **Two click gates exist now, by design — do not add a third, and do not merge
  them.** `click_classifier.classify_click()` gates phase 1 (finding and
  clicking Apply, before a form exists) and is default-deny: an unrecognized
  click costs one manual step. `filling/click_gate.decide()` gates phase 2
  (clicking once the form is open, only reachable via `agent_runner.build_tools
  (allow_fill=True)`) and is default-ALLOW, denying only submit-shaped names —
  it has to be, because a fill agent genuinely needs to click things a fixed
  allowlist can't anticipate (an ATS's own wording for "add another entry").
  The two gates deliberately use separately-written deny patterns, not a shared
  regex, so a blind spot in one isn't automatically a blind spot in both.
- Once the form is open, the **snapshot** agent still has clicking disabled
  unconditionally (zero write tools, full stop). The **fill** agent's clicking
  is gated by `filling/click_gate.py` instead — not unconditionally disabled,
  because it has to click to fill.
- A CDP guard script (`guard.py`) neuters `HTMLFormElement.submit`/`requestSubmit`,
  blocks `submit` events, and intercepts clicks on anything submit-shaped at the
  DOM level — regardless of what triggered the click. This is unmodified and is
  the second, independent layer behind **both** click gates: even a click a
  gate allows that turns out to be submit-shaped is still stopped here before
  its default action fires.
- `filling/dom_writer.py` only ever writes a value `answering.resolver` already
  vetted (guardrails-first, citation-checked, enum-validated) — it never asks a
  model how to write something, and it never chooses what to write.
- In `answering/`, guardrails run **before** the model ever sees a question —
  compensation, work authorization, EEO/demographic, background-check,
  attestation, and reference-contact questions are withheld unconditionally. Do
  not turn this into a post-hoc filter on the model's output. This applies
  identically when `answering.resolver.answer_job` is called from
  `filling/runner.py` — the fill loop does not get its own, looser copy of
  guardrails.

If a change would weaken the never-submit guarantee (both click gates, the CDP
guard, or the "only ever write a vetted value" rule above), stop and raise it
rather than making it.

## Environment

- Python 3.12; use a project-local `.venv` (`python3 -m venv .venv`). `uv` is not
  installed.
- browser-use 0.13.x drives Chromium over **CDP** via `cdp-use`. It dropped
  Playwright entirely — there is no Playwright `Page` and no `add_init_script`.
  Use raw CDP (`Page.addScriptToEvaluateOnNewDocument`, `Runtime.evaluate`,
  `Target.attachToTarget`) instead.
- Cross-origin iframes (Greenhouse, Lever, SmartRecruiters) are **separate CDP
  targets**. A `Runtime.evaluate` on the top frame cannot see into them. Always walk
  targets, or extraction silently returns nothing on exactly those ATSs.
- The LLM is configured by two env vars, `AUTOFILL_LLM_API_KEY` and
  `AUTOFILL_LLM_MODEL`, routed through OpenRouter by default
  (`AUTOFILL_LLM_PROVIDER` switches backends). Never hardcode a vendor or model:
  `llm.py` is the only module allowed to name a provider.

## Testing

- Offline tests (no browser, no API key) are the priority — the safety properties are
  tested there. Run with `pytest`.
- Live tests are marked `live` and skipped by default: `pytest -m live`.
- Prefer recorded fixtures (`tests/fixtures/*_raw_extract.json`) over live pages for
  anything not specifically testing browser behavior.
