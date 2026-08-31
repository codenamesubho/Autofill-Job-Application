# Autofill-Job-Application

Takes a list of job URLs, drives a real browser to each one, clicks through to the
application form, and writes a structured snapshot of every question that form asks.

**It never submits an application, and in this version it cannot even fill one in.**

## Requirements

- Python 3.11+ (developed on 3.12)
- Google Chrome installed (macOS path is auto-detected; override with
  `AUTOFILL_CHROME_PATH`)
- An `ANTHROPIC_API_KEY` — the agent needs an LLM for **every** run; there is no
  offline path

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env      # add your ANTHROPIC_API_KEY
```

`browser-use` pulls its own Chromium tooling, but this project drives your **system
Chrome binary** against a **dedicated profile** at `~/.autofill/chrome-profile`.
Your real Chrome profile is never opened — sharing it would put your live logged-in
sessions under an autonomous agent, and Chrome refuses a second process on the same
profile anyway. Log into an ATS once in this profile and the session persists.

## Usage

```bash
cp urls.example.txt urls.txt      # one job URL per line, # comments ok
autofill-snapshot urls.txt --out ./snapshots/ --headful
```

| Option | Default | Meaning |
|---|---|---|
| `--out` | `./snapshots` | artifact directory |
| `--headful` | off | show the browser window |
| `--max-steps` | `25` | agent step budget per job |
| `--model` | `claude-opus-5` | chat model |
| `--profile-dir` | `~/.autofill/chrome-profile` | dedicated Chrome profile |

Prints one line per job — `⚠` appears only if the guard actually had to stop a
submission:

```
https://job-boards.greenhouse.io/acme/jobs/1234    form_open    18 questions   7 required  steps=14
```

Exits `2` if no API key is set (before Chrome launches), `1` if no job yielded any
questions, `0` otherwise — a partial batch still writes a usable artifact.

## Output

`snapshots/<timestamp>.json`, one entry per URL:

```jsonc
{
  "input_url": "https://job-boards.greenhouse.io/acme/jobs/1234",
  "final_url": "https://job-boards.greenhouse.io/acme/jobs/1234#app",
  "outcome": "form_open",          // login_wall | no_apply_control_found | navigation_error
  "questions": [
    {
      "label": "Are you legally authorized to work?",
      "widget": "select_native",
      "required": true,
      "options": [{"value": "Yes", "label": "Yes", "selected": false},
                  {"value": "No",  "label": "No",  "selected": false}],
      "label_source": "llm",
      "confidence": 0.7,
      "source": "tier2",
      "notes": "section: Application; reported by LLM agent; no DOM locator",
      "ref": {"frame_url": "", "css_selector": null, "name_attr": null}
    }
  ],
  "tier2":  {"ran": true, "model": "claude-opus-5", "steps": 14},
  "guard":  {"installed": true, "blocked": 0, "reasons": []},
  "error": null
}
```

Two fields are worth reading carefully. `options: null` means *"this is a choice
question whose choices could not be read"* — deliberately different from `[]`,
which means "no choices". And `guard.blocked > 0` means something on the page
actually attempted a submission and was stopped; it should normally be `0`.

## How the safety works

A [browser-use](https://github.com/browser-use/browser-use) `Agent` (v0.13.x) does
the whole job: navigate, find the Apply control, open the form, read the questions,
return them as a validated Pydantic model via `output_model_schema`.

Because the agent chooses its own clicks, safety cannot live in a gate the agent
calls — a gate it never calls protects nothing. So it lives in two places the agent
cannot reach:

**1. It has no write-capable tools.** Before the agent is constructed, `input`,
`send_keys`, `select_dropdown`, `upload_file` and `evaluate` are removed from its
action registry. It keeps `navigate`, `click`, `scroll`, `extract`, `find_elements`
and `dropdown_options` — so it can *read* a dropdown's choices but not *pick* one.
A form it cannot fill is a form it cannot meaningfully submit.
`tests/test_excluded_actions.py` asserts this against the real registry and includes
a control case proving those actions exist by default, so the test cannot pass
vacuously.

**2. A CDP guard blocks submission itself.** An init script overrides
`HTMLFormElement.submit`/`requestSubmit`, cancels `submit` events, intercepts clicks
on anything submit-shaped — by type *and* by accessible text, since ATS submit
controls are often `<a>` or `<div role=button>` — and swallows Enter inside a form.
It is re-injected per CDP target, because a cross-origin form frame does not inherit
the parent's init script.

Verified against real Chrome: `form.submit()`, `requestSubmit()` and a real click on
a submit button were all blocked, and the page never navigated.

## Tests

```bash
pytest             # 117 offline tests: no browser, no API key, ~1s
pytest -m live     # needs real Chrome; the end-to-end test also needs a key
```

| File | Tests | Covers |
|---|---|---|
| `test_click_classifier.py` | 66 | default-deny click gate (parked module) |
| `test_excluded_actions.py` | 27 | **the safety property** — write actions absent from the registry |
| `test_guard_js.py` | 9 | all four guard interception points still present |
| `test_cli.py` | 9 | URL parsing, flags, blocked-submission reporting |
| `test_models.py` | 6 | agent-output schema, widget mapping, round-trip |

The safety properties are all in the offline suite, so they are checked on every run
without spending a token or opening a browser.

## Layout

```
src/autofill_job_application/
  agent_runner.py   the one execution path: Tools(exclude_actions=...) + Agent
  browser.py        session factory: system Chrome, dedicated profile
  guard.py          submit-blocking init script + status readout
  models.py         snapshot types and the agent-facing schema
  artifact.py       JSON artifact + stdout report
  cli.py            autofill-snapshot entry point
```

## Status and known limitations

**The agent path has not been run end to end.** It was built and offline-tested
without an `ANTHROPIC_API_KEY` available. The guard and the cross-origin iframe
behavior *were* verified against real Chrome; the agent's accuracy on a live form
is still unmeasured. The first real run is the outstanding validation step.

**Snapshot questions carry no CSS selector.** The LLM reports what it sees rather
than reading the DOM, so `ref` comes back empty and `confidence` is capped at 0.7 to
say so honestly. A future fill step would need the parked extractor below, or a
re-resolve pass.

Two modules are **parked and imported by nothing** (verified: neither loads when the
CLI is imported):

- `extract/js_extractor.py` — a deterministic CDP DOM extractor. It works: on a live
  Greenhouse posting it returned all 28 questions with correct labels, widgets and
  required flags, and it reads cross-origin iframes that top-frame JavaScript cannot
  see at all. No API key, fully deterministic, zero tokens.
- `click_classifier.py` — a default-deny click gate for a deterministic navigator.

Bring them back if agent accuracy or cost disappoints. Their file headers list the
rough edges to fix first — on the real Greenhouse run the extractor leaked
custom-combobox internals as duplicate `Select...` rows, reported invisible footer
elements, and left generic labels like `Attach` unresolved.

## Notes for contributors

Work happens on a feature branch, never `main`; commits are only made when asked,
and nothing is ever pushed from here. See `CLAUDE.md`.
