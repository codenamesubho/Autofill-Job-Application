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

Agentic job-application autofill built on **browser-use** (`browser_use`, v0.13.x).
The current slice takes a list of job URLs, navigates to each, clicks through to the
application form, and emits a structured snapshot of the questions it asks.

**It never submits an application.** That is the single most important property of
this codebase:

- There is no `fill/`, `answer/` or `submit/` module, by design.
- `click_classifier.classify_click()` is the **only** function permitted to gate a
  click, and it is default-deny. Do not add a second click path.
- Once the form is open (field count `>= 3`), clicking is disabled unconditionally.
- A CDP guard script neuters `HTMLFormElement.submit`/`requestSubmit` as a second,
  independent layer.

If a change would weaken any of the above, stop and raise it rather than making it.

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
- `ANTHROPIC_API_KEY` may be unset. Tier-1 extraction must work with zero LLM calls;
  tier-2 must skip cleanly and the run must still exit 0.

## Testing

- Offline tests (no browser, no API key) are the priority — the safety properties are
  tested there. Run with `pytest`.
- Live tests are marked `live` and skipped by default: `pytest -m live`.
- Prefer recorded fixtures (`tests/fixtures/*_raw_extract.json`) over live pages for
  anything not specifically testing browser behavior.
