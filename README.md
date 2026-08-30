# Autofill-Job-Application

Takes a list of job URLs, drives a real browser to each one, clicks through to the
application form, and writes a structured snapshot of every question that form asks.

**It never submits an application, and in this version it cannot even fill one in.**

## How it works

A [browser-use](https://github.com/browser-use/browser-use) `Agent` does the whole
job: it navigates, finds the Apply control, opens the form, reads the questions, and
returns them as a validated Pydantic model.

Two independent mechanisms keep that safe:

1. **The agent has no write-capable tools.** Before the agent is built, `input`,
   `send_keys`, `select_dropdown`, `upload_file` and `evaluate` are removed from its
   action registry. It can navigate, click, scroll, and read dropdown choices — it
   cannot type, choose, or upload. A form it cannot fill is a form it cannot
   meaningfully submit. `tests/test_excluded_actions.py` asserts this against the
   real registry, and includes a control case proving those actions exist by default.
2. **A CDP guard blocks submission itself.** An init script overrides
   `HTMLFormElement.submit`/`requestSubmit`, cancels `submit` events, intercepts
   clicks on anything submit-shaped (by type *and* by accessible text, since ATS
   submit buttons are often `<a>` or `<div role=button>`), and swallows Enter inside
   a form. Verified against real Chrome: all three submission vectors blocked, no
   navigation.

If the guard ever fires, the run report says so loudly rather than hiding it.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env      # add your ANTHROPIC_API_KEY
```

Uses your system Chrome binary against a **dedicated** profile at
`~/.autofill/chrome-profile`. Your real Chrome profile is never opened — log into an
ATS once in this profile and the session persists across runs.

## Usage

```bash
cp urls.example.txt urls.txt      # one job URL per line, # comments ok
autofill-snapshot urls.txt --out ./snapshots/ --headful
```

Writes `snapshots/<timestamp>.json` and prints one line per job:

```
https://job-boards.greenhouse.io/gitlab/jobs/8705017002   form_open   28 questions   9 required  steps=14
```

Options: `--headful` (watch it work), `--max-steps` (agent budget per job, default
25), `--model`, `--profile-dir`.

## Tests

```bash
pytest             # offline suite: no browser, no API key needed
pytest -m live     # needs real Chrome; the end-to-end test also needs a key
```

The offline suite covers the safety properties — action exclusion, guard content,
schema mapping — so they are checked on every run without spending a token.

## Status

v1 is the agent-only path. Two modules are **parked and imported by nothing**:

- `extract/js_extractor.py` — a deterministic CDP DOM extractor. It works: on a live
  Greenhouse posting it returned all 28 questions with correct labels and required
  flags, and it reads cross-origin iframes that top-frame JavaScript cannot see. It
  needs no API key and is fully deterministic.
- `click_classifier.py` — a default-deny click gate (66 tests) for a deterministic
  navigator.

Bring them back if agent accuracy or cost disappoints. Their headers list the known
rough edges to fix first.

**Known limitation:** because the LLM reports what it sees rather than reading the
DOM, snapshot questions carry no CSS selector. A future fill step would need the
parked extractor, or a re-resolve pass, to act on them.
