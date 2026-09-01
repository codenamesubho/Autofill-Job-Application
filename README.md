# Autofill-Job-Application

Three tools:

1. **`autofill-snapshot`** takes a list of job URLs, drives a real browser to each
   one, clicks through to the application form, and writes a structured snapshot
   of every question it asks.
2. **`autofill-answer`** reads that snapshot plus a markdown document about you,
   and drafts an answer to each question — grounded in quotes from the document,
   with anything it can't support left for you.
3. **`autofill-fill`** takes job URLs and that same document directly, and writes
   the vetted answers into the live form's fields for you to review.

**None of them ever submits an application. `autofill-fill` can write into a
form's fields, but cannot submit one either** — two independent layers make
that structurally true regardless of what any agent decides to click. See "How
the safety works" below.

## Requirements

- Python 3.11+ (developed on 3.12)
- Google Chrome installed (macOS path is auto-detected; override with
  `AUTOFILL_CHROME_PATH`)
- An LLM API key — the agent needs a model for **every** run; there is no offline
  path. Any provider works (see Configuration)

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env      # add your LLM key and model
```

## Configuration

The model is chosen entirely by environment; no vendor is hardcoded.

| Variable | Required | Meaning |
|---|---|---|
| `AUTOFILL_LLM_API_KEY` | **yes** | key for whichever backend you route through |
| `AUTOFILL_LLM_MODEL` | **yes** | e.g. `anthropic/claude-opus-5`, `openai/gpt-4o` |
| `AUTOFILL_LLM_PROVIDER` | no | `openrouter` (default) `litellm` `anthropic` `openai` `groq` `google` |
| `AUTOFILL_LLM_BASE_URL` | no | override the endpoint (gateway or proxy) |

Routing defaults to **OpenRouter**: one key and one `provider/model` string reach
every major model, and it needs no dependency beyond what is already installed.
Switching model or vendor never requires a code change:

```bash
AUTOFILL_LLM_MODEL=openai/gpt-4o            autofill-snapshot urls.txt
AUTOFILL_LLM_MODEL=google/gemini-2.5-pro    autofill-snapshot urls.txt

# or talk straight to a vendor, with that vendor's own model id
AUTOFILL_LLM_PROVIDER=anthropic AUTOFILL_LLM_MODEL=claude-opus-5 \
  autofill-snapshot urls.txt
```

`litellm` is an optional extra (`pip install -e ".[litellm]"`); every other
provider works out of the box. `--model` and `--provider` override the environment
for a single run.

`browser-use` pulls its own Chromium tooling, but this project drives your **system
Chrome binary** against a **dedicated profile** at `~/.autofill/chrome-profile`.
Your real Chrome profile is never opened — sharing it would put your live logged-in
sessions under an autonomous agent, and Chrome refuses a second process on the same
profile anyway. Log into an ATS once in this profile and the session persists.

**No tool here ever closes the browser itself** — that's left to you, deliberately,
so `autofill-fill` doesn't yank away the window the moment before you'd want to
review what it filled in. Close it when you're done, or the next run against the
same `--profile-dir` will fail to start (a profile can only have one Chrome process
at a time — see above).

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
| `--job-timeout` | `600`s | wall-clock cap per job; a job that blows through it is recorded as failed and the batch moves on |
| `--model` | `$AUTOFILL_LLM_MODEL` | model id for this run |
| `--provider` | `$AUTOFILL_LLM_PROVIDER` or `openrouter` | routing backend |
| `--profile-dir` | `~/.autofill/chrome-profile` | dedicated Chrome profile |

Prints one line per job — `⚠` appears only if the guard actually had to stop a
submission:

```
https://job-boards.greenhouse.io/acme/jobs/1234    form_open    18 questions   7 required  steps=14
```

Exits `2` on a configuration problem — missing key, missing model, unknown
provider — reported **before** Chrome launches, with the variable named. Exits `1`
if no job yielded any questions, `0` otherwise; a partial batch still writes a
usable artifact.


## Drafting answers (`autofill-answer`)

```bash
cp about-me.example.md about-me.md   # fill in real details about yourself
autofill-answer snapshots/20260830T140212Z.json --context about-me.md --out ./answers/
```

`--context` is a **markdown (or plain text) file about you** — experience, links,
preferences, whatever you'd want a recruiter to know. It's read verbatim and
handed to the model as the only source of truth; nothing is inferred about you
from anywhere else.

```markdown
# About Alex Doe

Senior backend engineer, San Francisco. Email: alex.doe@example.com.
Willing to relocate for the right role.
Most proud of rebuilding a payments ledger that cut reconciliation time in half.
```

Every answer is one of three things, and the artifact says which:

| `source` | Meaning |
|---|---|
| `context_doc` | Backed by a verbatim quote from your document — check `evidence` |
| `llm_inference` | The model reasoned to it without a supporting quote — always flagged |
| `escalated` | Not answered. Either the document doesn't cover it, or it's on the never-answer list |

```json
{
  "input_url": "https://job-boards.greenhouse.io/acme/jobs/1234",
  "answers": [
    {
      "question_label": "Expected Salary",
      "value": null,
      "source": "escalated",
      "escalation_reason": "a salary figure must be your decision, not a model's",
      "category": "compensation"
    },
    {
      "question_label": "Email",
      "value": "alex.doe@example.com",
      "source": "context_doc",
      "confidence": 0.95,
      "flagged": false,
      "evidence": "Email: alex.doe@example.com"
    },
    {
      "question_label": "Are you willing to relocate?",
      "value": "Yes",
      "source": "context_doc",
      "confidence": 0.85,
      "evidence": "Alex is willing to relocate for the right role"
    }
  ]
}
```

**Guardrails run before the model ever sees the question**, not as a filter on
its output. Compensation, work authorization, years-of-experience and other
verifiable credentials, EEO/demographic fields, background-check questions,
attestations, and reference contact details are withheld and escalated
unconditionally — `src/autofill_job_application/answering/guardrails.py` lists
the exact patterns. A withheld question is never sent to the model at all, so
there is no output to filter in the first place.

**A quote is checked, not trusted.** If the model cites a span that doesn't
actually appear in your document, the answer is kept but downgraded to
`llm_inference`, its confidence is capped, and it's flagged — a fabricated
citation is treated as no citation. Answers to choice questions (`select`,
`radio`, …) are validated against the snapshot's actual options; anything that
isn't one of them, or that came from an unreadable dropdown, is escalated
instead of guessed.

Exit codes mirror `autofill-snapshot`: `2` for a configuration problem, `1` if a
file couldn't be found or parsed **or if every question across every job ended up
escalated** (mirrors `autofill-snapshot`'s "no job yielded any questions"), `0`
otherwise.

## Filling forms (`autofill-fill`)

```bash
autofill-fill urls.txt --context about-me.md --resume resume.pdf --out ./fills/
```

Takes job URLs directly, not a snapshot file — batches discover fields live as
the form is filled (a "Next" click can reveal a page of fields that didn't
exist a moment ago), so a snapshot from an earlier run would already be stale.

| Option | Default | Meaning |
|---|---|---|
| `--context` | *(required)* | same context document as `autofill-answer` |
| `--resume` | none | local path to a resume file, for FILE-widget fields — never LLM-chosen |
| `--cover-letter` | none | local path used for fields whose label mentions "cover" |
| `--out` | `./fills` | artifact directory |
| `--max-steps` | `25` | phase-1 (reach the form) step budget per job |
| `--max-batches` | `15` | fill-loop batch budget per job, independent of the "nothing new" stop signal |
| `--job-timeout` | `600`s | wall-clock cap per job |
| `--batch-timeout` | `120`s | wall-clock cap per residual-agent turn |
| `--headful`, `--model`, `--provider`, `--profile-dir` | same as `autofill-snapshot` | |

**Ordinary fields are written deterministically — no LLM in the write path.**
The Candidate Agent (the same `answering.resolver.answer_job` used by
`autofill-answer`, guardrails-first, citation-checked, enum-validated) decides
*values*; a CDP writer sets them directly by selector, using the deterministic
extractor's real DOM references. The LLM only ever transcribes a value that
already passed guardrails — it never invents one.

**Custom dropdowns (a country picker being the canonical case) are handled by
typing, not scanning.** Rather than asking an agent to open a 195-country list
and scroll/read its way to a match, the writer types the answer into the
combobox first to trigger the widget's own filter — the same thing a human
does typing "Uni" to narrow the list to a handful — then clicks whichever
resulting option's text matches. Only when that finds no confident match (a
non-searchable widget, or genuinely no matching option) does it fall back to
the agent below.

**A scoped, click-gated agent handles what's left**: a combobox the typing
approach couldn't resolve, a field whose selector didn't resolve at all, or a
legitimate structural control a fixed selector can't anticipate ("Add another
employment entry", "Next" on a multi-page wizard, an accordion toggle). Its
click tool is gated separately from anything a value-write requires — see "How
the safety works".

Every field lands in the artifact as one of `written`, `escalated` (guardrails
withheld it, or the Candidate Agent didn't answer it — same categories as
`autofill-answer`), or `failed` (a write was attempted and didn't verify on
read-back — e.g. a framework reverted it):

```json
{
  "input_url": "https://job-boards.greenhouse.io/acme/jobs/1234",
  "fields": [
    {"question_label": "Email", "widget": "email", "value_written": "alex.doe@example.com",
     "path": "deterministic", "answer_source": "context_doc", "write_status": "written"},
    {"question_label": "Expected Salary", "widget": "number", "value_written": null,
     "answer_source": "escalated", "write_status": "escalated",
     "failure_reason": "a salary figure must be your decision, not a model's"}
  ],
  "batches_run": 3,
  "guard": {"installed": true, "blocked": 0, "reasons": []}
}
```

Exit codes mirror `autofill-answer`: `2` for a configuration problem, `1` if a
file couldn't be found or parsed, or **`--resume`/`--cover-letter` was given a
path that doesn't exist** (checked before Chrome launches), `0` otherwise.

## Snapshot output

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
  "tier2":  {"ran": true, "provider": "openrouter",
             "model": "anthropic/claude-opus-5", "steps": 14},
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
the browser-facing work in all three tools. `autofill-snapshot`'s agent
navigates, finds Apply, opens the form, and reports questions via
`output_model_schema`. `autofill-fill` reuses that exact agent for the same
"reach the form" phase, then hands off to a batch loop: a deterministic
extractor reads the open form's fields, the Candidate Agent (`autofill-answer`'s
unmodified `answering.resolver.answer_job`) decides values, and a CDP writer
sets them directly by selector — no LLM in that write path at all.

Because an agent chooses its own clicks, safety cannot live in a gate the
agent merely *could* call — a gate it never calls protects nothing. Two
different safety models coexist, one per agent, and neither is a weaker
version of the other:

### The snapshot agent: zero write tools

Before the agent is constructed, `input`, `send_keys`, `select_dropdown`,
`upload_file` and `evaluate` are removed from its action registry. It keeps
`navigate`, `click`, `scroll`, `extract`, `find_elements` and
`dropdown_options` — so it can *read* a dropdown's choices but not *pick* one.
A form it cannot fill is a form it cannot meaningfully submit.
`tests/test_excluded_actions.py` asserts this against the real registry and
includes a control case proving those actions exist by default, so the test
cannot pass vacuously.

### The fill agent: narrowed tools + a click gate, not zero tools

`autofill-fill`'s residual agent — used only for a combobox the deterministic
type-to-filter attempt couldn't resolve, an unresolved selector, or a
legitimate structural click ("Add another entry") — gets exactly two write
actions back: `input` and `select_dropdown`
(`agent_runner.build_tools(allow_fill=True)`). Everything else stays excluded,
including `upload_file` (resumes are attached deterministically, from a
pre-approved local path, never chosen by the LLM). `tests/test_filling_tools.py`
proves this against the real registry the same way, and separately proves the
*default* `build_tools()` call is untouched by the new parameter's existence.

Since this agent can click, its clicks are gated *before* they happen — the
first time a click gate is actually wired into a live agent in this codebase.
`filling/click_gate.py` is default-**allow** once the form is open (a fill
agent has to be able to click things a fixed allowlist can't anticipate) and
denies only submit-shaped names, using a pattern deliberately written
independently of the guard's own pattern below — a blind spot in one isn't a
blind spot in both. `tests/test_filling_click_gate_wiring.py` proves the gate
is actually reachable through `browser_use.Tools`' real click-dispatch path,
not just correct in isolation.

### The second, independent layer: a CDP guard, for both agents

An init script overrides `HTMLFormElement.submit`/`requestSubmit`, cancels
`submit` events, intercepts clicks on anything submit-shaped — by type *and*
by accessible text, since ATS submit controls are often `<a>` or
`<div role=button>` — and swallows Enter inside a form. It is re-injected per
CDP target, because a cross-origin form frame does not inherit the parent's
init script. This layer is unmodified and unconditional: even a click the
fill agent's gate allows that turns out to be submit-shaped is still stopped
here, before its default action fires.

Verified against real Chrome: `form.submit()`, `requestSubmit()` and a real click on
a submit button were all blocked, and the page never navigated.

### Writing a value never means guessing one

`filling/dom_writer.py` only ever writes a value `answering.resolver` already
vetted through the same guardrails-first, citation-checked pipeline
`autofill-answer` uses — escalated questions (compensation, work
authorization, EEO, background-check, attestation, references) are never sent
to the fill loop's Candidate Agent call any more than they're sent to
`autofill-answer`'s. Every write is verified by reading the field back and
comparing it to the intended value — a React-controlled input reverting a
naive assignment is reported as `failed`, not silently trusted.

## Tests

```bash
pytest             # 302 offline tests: no browser, no API key, ~2s
pytest -m live     # needs real Chrome; some tests also need an LLM key
```

| File | Tests | Covers |
|---|---|---|
| `test_click_classifier.py` | 66 | phase-1 default-deny click gate (parked module) |
| `test_excluded_actions.py` | 27 | **the safety property** — write actions absent from the snapshot agent's registry |
| `test_filling_tools.py` | 12 | the fill agent gets exactly `input`/`select_dropdown` back, nothing else; snapshot agent unaffected |
| `test_filling_click_gate.py` | 40 | phase-2 default-allow click gate — denies only submit-shaped names |
| `test_filling_click_gate_wiring.py` | 3 | the gate is reachable through `browser_use.Tools`' real click-dispatch path, not just correct in isolation |
| `test_dom_writer_js.py` | 13 | native-setter writes; combobox type-to-filter, matching, and document-wide option fallback |
| `test_extractor_radio_values.py` | 3 | radio options carry their real HTML value, not a label or element id |
| `test_filling_runner.py` | 6 | batch-loop stop condition, `max_batches` cap, job timeout, phase-1 failure handling, combobox deterministic-first/residual-fallback routing |
| `test_browser.py` | 2 | the browser session survives an Agent.run() completing (keep_alive) |
| `test_filling_cli.py` | 9 | bad-input failure modes for `autofill-fill`, including `--resume`/`--cover-letter` |
| `test_answering_guardrails.py` | 58 | never-answer categories are withheld before the model sees them |
| `test_guard_js.py` | 9 | all four guard interception points still present |
| `test_cli.py` | 10 | URL parsing, flags, blocked-submission reporting |
| `test_agent_runner.py` | 2 | a job that never finishes is recorded as failed, not left hanging |
| `test_answering_resolver.py` | 14 | citation checking, enum validation, guardrail-first ordering |
| `test_answering_cli.py` | 6 | bad-input failure modes for `autofill-answer` |
| `test_models.py` | 6 | agent-output schema, widget mapping, round-trip |
| `test_llm_config.py` | 16 | provider resolution, precedence, actionable errors |

The safety properties are all in the offline suite, so they are checked on every run
without spending a token or opening a browser. `tests/live/test_dom_writer_injection.py`
is the one live test that can catch a silently-reverted write (a React-controlled
input, simulated in the test fixture) — needs real Chrome, no LLM key.

## Layout

```
src/autofill_job_application/
  agent_runner.py   the one execution path: Tools(exclude_actions=...) + Agent;
                     build_tools(allow_fill=True) re-enables input/select_dropdown
                     for the fill agent only
  llm.py            provider registry; the only module that names a vendor
  browser.py        session factory: system Chrome, dedicated profile
  guard.py          submit-blocking init script + status readout (both agents)
  click_classifier.py  phase-1 default-deny click gate (parked module)
  models.py         snapshot types and the agent-facing schema
  artifact.py       JSON artifact + stdout report
  extract/
    js_extractor.py deterministic CDP extractor; filling/ is its first consumer
  answering/
    guardrails.py   never-answer categories, checked before the model is called
    resolver.py     one LLM call + citation and enum validation
    models.py       Answer/JobAnswers/AnswerRun; no path to a browser
    cli.py          autofill-answer entry point
  filling/
    click_gate.py   phase-2 default-allow click gate, separate deny pattern from guard.py
    tools.py        wires click_gate into a browser_use.Tools registry
    dom_writer.py   deterministic CDP writes; never decides a value, only transcribes one
    runner.py       the batch loop: extract -> answer_job -> write -> residual turn
    models.py       FieldFillResult/JobFillResult/FillRun; reuses answering's AnswerSource
    cli.py          autofill-fill entry point
  cli.py            autofill-snapshot entry point
```

## Status and known limitations

**The snapshot agent path has not been run end to end.** It was built and
offline-tested without an LLM key available. The guard and the cross-origin
iframe behavior *were* verified against real Chrome; the agent's accuracy on a
live form is still unmeasured. The first real run is the outstanding validation
step.

**`autofill-answer` has been exercised with a fake LLM offline, and once against
a real OpenRouter endpoint** (with an invalid key, to confirm the failure path
reports cleanly rather than crashing — it did). A full run against a real
snapshot with a working key has not happened yet.

**Snapshot questions carry no CSS selector.** The LLM reports what it sees rather
than reading the DOM, so a snapshot's `ref` comes back empty and `confidence` is
capped at 0.7 to say so honestly. `autofill-fill` doesn't use the snapshot agent's
questions for this reason — it re-extracts deterministically instead (see below).

**`autofill-fill` has not been run end to end against a real ATS.** It was built
and offline-tested (293 tests, including the write JS's native-setter pattern
checked as source text) without Chrome available in the build environment. A
live test (`tests/live/test_dom_writer_injection.py`) exercises `dom_writer`
against a real page, including a simulated React-controlled input — that test
is written but has not yet been run against real Chrome either. The first real
run against a live ATS (ideally one of the harder cases: a Workday-style
multi-page wizard, or a form with a custom combobox) is the outstanding
validation step, same as the snapshot agent's own first real run was.

**`extract/js_extractor.py` is no longer parked** — `filling/dom_writer.py` and
`filling/runner.py` are its first real consumers, using the real CSS selectors
and (as of this change) real radio-button values it captures. It was previously
validated against a live Greenhouse posting (28/28 questions correct, including
reading a cross-origin iframe top-frame JavaScript cannot see into) before
`filling/` existed; known rough edges from that run, not yet fixed: custom-combobox
internals can leak as duplicate `Select...` rows, invisible footer elements can be
reported, and generic labels like `Attach` aren't resolved against a nearby
heading — `dom_writer`'s type-to-filter path for a custom combobox
(`select_aria`) reads the *filtered* option list after typing, which sidesteps
duplicate-row leakage in practice (a filtered list rarely contains the
duplicates), but the other two rough edges (invisible footer elements, generic
labels) are unrelated and still open.

`click_classifier.py` remains **parked and imported by nothing** — it was written
for a deterministic navigator's "find and click Apply" step, and that's still
what it's for. `filling/click_gate.py` is a separate, new module for a different
job (gating clicks once the form is open); it does not import or replace
`click_classifier.py`, and reviving `click_classifier.py` for its original
purpose is still open if agent accuracy or cost disappoints there.

## Notes for contributors

Work happens on a feature branch, never `main`; commits are only made when asked,
and nothing is ever pushed from here. See `CLAUDE.md`.
