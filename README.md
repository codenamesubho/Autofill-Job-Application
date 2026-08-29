# Autofill-Job-Application

Fills job application forms automatically and stops before Submit. A human always
reviews the filled form and clicks the button.

See [PLAN.md](PLAN.md) for the full design.

## Status

**M0 (skeleton) complete.** The contract, state machine, store and CLI exist; no
browser automation yet. Milestones M1-M8 are tracked in PLAN.md section 7.

| Milestone | What | State |
|---|---|---|
| M0 | `models.py`, config, SQLite, CLI | done |
| M1 | Ingestion + deterministic resolver | next |
| M2 | Corpus harness (the measuring instrument) | |
| M3 | Tier-1 extractor + label cascade, scored on the corpus | |
| M4 | Interaction primitives, upload, submit guards | |
| M5 | Context agent + cache + confidence routing | |
| M6 | Orchestrator loop + forward classifier | |
| M7 | Tier-2 vision fallback + site memory | |
| M8 | HITL review UX + coverage dashboard | |

## Quick start

```sh
python3.11 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

autofill init     # creates state.db and starter data/profile.yaml + data/jobs.yaml
autofill status   # queue counts and answer-cache size
pytest
```

`autofill ingest | corpus | run | review | resume` exit with code 2 until their
milestone lands.

Browser support installs separately, so `autofill status` works on a machine with
no browsers at all:

```sh
pip install -e ".[browser]" && playwright install chromium
```

## Design rules worth knowing before editing

- **Nothing ever clicks Submit.** The state machine has no path to a submitted
  state that the agent can take on its own; only `AWAITING_REVIEW ->
  SUBMITTED_BY_HUMAN` exists, and a human drives it. M3 adds two further
  independent guards (a click denylist and an injected page-level interceptor).
- **The answering layer never sees the DOM.** `autofill/models.py` is the whole
  contract between the two halves; `Selector` is opaque to anything that answers
  questions. This is what keeps answering testable without a browser.
- **Guardrails are data, not code.** `config/never_answer.yaml` decides what the
  agent refuses to invent (salary, visa status, attestations, EEO fields). Add
  cases there, not as conditionals in the resolver.
- **No vendor names in code.** There are no per-ATS adapters. Controls are
  classified by behaviour (`WidgetKind`: native / aria / custom), and learned
  page knowledge goes in the `site_memory` table instead of a hand-written
  adapter. Vendor names belong in `tests/corpus/` as measurement only.
- **Ambiguity stops the run.** `ForwardControl.AMBIGUOUS` is treated exactly like
  a terminal submit. A wrongly stopped wizard costs one click; a wrongly
  advanced one sends a real application.
- **`autofill status` must never import Playwright.** There is a test for it.

## Personal data

`data/profile.yaml`, your resume and the experience document are gitignored. They
stay on your machine; nothing is uploaded anywhere but the ATS form you are applying to.
