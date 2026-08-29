# Autofill Job Application — Execution Plan

**Status:** proposed design. No implementation code written yet.
**Stack assumption:** Python 3.11+ (inferred from `.gitignore`), Playwright, SQLite, Pydantic, Typer.

---

## 1. What the system does

```
inputs                    per-job agentic loop                        output
──────                    ────────────────────                        ──────
resume.pdf        ┐
experience.md     ├─►  Candidate Context Agent ◄──┐
profile.yaml      ┘                               │ Question/Answer
                                                  │
jobs.yaml ──► Navigator ──► Extractor ──► Resolver┘──► Filler ──► Validator
                  ▲                                                  │
                  └──────── re-extract if page changed ◄─────────────┘
                                                                     │
                                                          form filled, NOT submitted
                                                          → review artifact + open tab
                                                          → human reviews & clicks Submit
```

The loop terminates when there are no unfilled required fields, or when it needs a human.
**It never clicks Submit.**

---

## 2. Core architectural decisions

### 2.1 The `FormSchema` / `Question` contract is the spine

The single most important decision: **the answering agent never sees the DOM.** The browser
layer normalizes any page into a list of `Question` objects; the answering layer returns
`Answer` objects; the filler translates back to DOM actions.

```python
class Question(BaseModel):
    field_id: str              # stable hash of (url_template, name/id/label)
    canonical_key: str | None  # "email", "phone", "years_experience", "work_auth", ...
    label: str                 # human-readable question text
    help_text: str | None
    type: FieldType            # text|textarea|select|multiselect|radio|checkbox|file|date|
                               # phone|email|url|number|consent|unknown
    options: list[Option]      # for enum types — the ONLY legal values
    required: bool
    max_length: int | None
    current_value: str | None
    selector: Selector         # opaque to the answering layer
    confidence: float          # extractor's confidence it parsed this correctly
```

This buys: unit-testable answering with zero browser, a reusable answer cache across every
ATS, and the ability to swap Playwright for something else later.

### 2.2 Generic-first, adapters as thin patches

Do **not** write 12 ATS scrapers. Write one good semantic DOM extractor and let per-ATS
adapters override only what breaks.

| Tier | Mechanism | When |
|---|---|---|
| 0 | Known-ATS adapter | URL pattern + DOM fingerprint matches (Greenhouse, Lever, Ashby, Workday, …) |
| 1 | **Generic DOM extractor** | Default. Walks form controls, derives label from `<label for>`, `aria-label`, `aria-labelledby`, `placeholder`, wrapping fieldset legend, nearest preceding text node |
| 2 | Vision / a11y-tree fallback | Tier-1 confidence below threshold, or canvas/shadow-DOM/exotic widgets. Screenshot + accessibility tree → LLM → field list |

An adapter implements only the methods it needs:

```python
class ATSAdapter(Protocol):
    name: str
    def detect(self, page) -> float: ...             # 0..1 confidence
    def enter_application(self, page) -> Page: ...   # "Apply" button, modal, iframe, new tab
    def extract(self, page) -> FormSchema | None: ...# None → fall through to generic
    def fill(self, page, q, a) -> bool: ...          # custom widgets (react-select, Workday)
    def advance(self, page) -> StepResult: ...       # multi-page wizards
    def upload_resume(self, page, path) -> bool: ...
```

**Trade-off:** adapters-first would be more reliable per site but doesn't satisfy "the job
board is not fixed." Generic-first degrades gracefully on unknown sites; the cost is more
LLM calls and more low-confidence flags on unusual forms. Accept it.

### 2.3 Pipeline of narrow LLM calls, not one autonomous mega-agent

Determinism, cost, cacheability, and auditability all favour a pipeline. The LLM is used at
exactly three points: (a) field understanding when tier-1 extraction is uncertain, (b) answer
generation for questions with no deterministic mapping, (c) submit-button classification as a
secondary check. Everything else is code.

### 2.4 Answer resolution order (cheapest → most expensive)

1. **Deterministic profile map** — `canonical_key` → value from `profile.yaml`. Name, email,
   phone, location, LinkedIn, GitHub, portfolio, work authorization, notice period, current
   CTC / expected CTC, willingness to relocate. Never LLM-generated. ~60–70% of fields.
2. **Answer cache** — normalized question hash → prior answer. Job applications ask the same
   ~200 questions forever. After a dozen applications this covers most of the remainder.
3. **Candidate Context Agent (LLM)** — retrieves from the experience doc + resume + the scraped
   job description, and answers. Returns `{value, confidence, rationale, sources}`.
4. **Escalate to human** — confidence below threshold, or the question is on the never-answer list.

Enum-typed questions are **constrained**: the LLM is given `options` and its output is validated
against them; on failure it is re-asked once, then escalated. No free-text into a `<select>`.

### 2.5 Guardrails — what the agent must never invent

Hard-coded refusal list, escalates to human regardless of confidence:

- Salary / compensation figures not present in `profile.yaml`
- Years of experience, degrees, certifications, GPA, employment dates not in the resume
- Work authorization, visa status, sponsorship requirement
- EEO / demographic fields (race, gender, veteran, disability) — **default: leave blank for human**
- Criminal history, background-check consents, drug-test consents
- Any legally-binding attestation checkbox ("I certify the above is true")
- References' contact details

This is a config file (`config/never_answer.yaml`), not scattered `if` statements.

### 2.6 Submit is blocked in three independent layers

Belt and braces, because a single missed heuristic means a real application is sent uninvited.

1. **Denylist + classifier** on button text/attributes; the filler simply never issues a click
   on anything classified as terminal submit.
2. **Injected page guard** (`browser/guards.py`) — an init script that intercepts
   `HTMLFormElement.prototype.submit`, `form` `submit` events, and clicks on
   `[type=submit]`, calling `preventDefault()` and recording the attempt. Removed only when
   the human takes over the tab.
3. **Navigation assertion** — if the URL changes to something matching
   `/thank|confirm|success|submitted/` the run is halted and loudly logged as a defect.

"Next / Continue" in a multi-step wizard is allowed; the classifier distinguishes it from
"Submit application" by text, position, and whether unfilled required fields remain.

---

## 3. The per-job loop (fixed point, not a single pass)

```
open(url)
  → resolve redirects, detect login wall → if login required: hand to human, mark BLOCKED
  → detect ATS (registry)
  → enter_application()            # click Apply, follow modal/iframe/new tab
  → scrape job description (for essay context)
LOOP (max N iterations, N≈8):
  → schema = extract(page)                       # tier 0/1/2
  → open = [q for q in schema if q.required and not q.filled]
  → if not open: break
  → answers = resolver.batch_answer(open)        # deterministic → cache → agent
  → filler.apply(page, answers)                  # types, selects, uploads, waits
  → validator.check(page)                        # read back values; catch client-side errors
  → if page fingerprint unchanged AND no progress: break with NEEDS_HUMAN
  → if wizard and current step complete: adapter.advance(page)   # never submit
→ screenshot + review artifact
→ status = AWAITING_REVIEW, notify human, leave tab open
```

**Why a loop and not one pass:** conditional fields ("Do you require sponsorship?" → reveals
three more), multi-page Workday-style wizards, and client-side validation that only surfaces
after a blur event. Re-extraction after every fill batch is the only robust approach.

**Loop safety:** iteration cap, page-fingerprint no-progress detection, per-job wall-clock
timeout, and a per-job LLM token budget.

---

## 4. State, resumability, concurrency

**SQLite (`state.db`) + per-job JSON/MD artifacts.** SQLite for the queue, the answer cache and
idempotency; files for anything a human reads.

```sql
applications(id, job_url, canonical_url, company, title, ats, status, attempt,
             last_error, artifact_dir, created_at, updated_at)
questions(id, application_id, field_id, label, type, answer, source, confidence, flagged)
answer_cache(question_hash, label_sample, type, answer, uses, last_used)
events(id, application_id, ts, kind, payload_json)      -- JSONL-equivalent audit trail
```

**Status machine:**
`PENDING → OPENING → DETECTED → FILLING → (NEEDS_HUMAN | BLOCKED_LOGIN | AWAITING_REVIEW) → SUBMITTED_BY_HUMAN | SKIPPED | FAILED`

**Resumability, honestly stated:** the run *queue* resumes perfectly — re-running skips
completed jobs, `--retry failed` reprocesses. But a *half-filled remote form* generally does
**not** survive closing the browser; most ATS forms hold state client-side only. Two mitigations:

- Persistent Playwright context (`user_data_dir`) keeps cookies/logins and, on ATSs that
  autosave drafts (Workday, iCIMS accounts), the partial application does survive.
- For everything else, resume means *re-fill from the saved answer set*, which is fast and
  cheap because every answer is already cached. Treat re-fill, not restore, as the primary
  recovery path.

**Concurrency:** default serial, one browser context. Human review is serial anyway, parallel
filling makes the review queue chaotic, and it trips bot detection. Optional bounded
parallelism (`--workers N`) for a *detect + extract only* pre-pass that warms the answer cache
before the filling run — this is where parallelism actually pays.

---

## 5. Human-in-the-loop

Two modes, both stopping before submit:

- **Attended (default).** One job at a time, headed browser. Filled → terminal prints a summary
  of flagged answers → user reviews in the live tab, edits, clicks Submit → user presses
  `[s]ubmitted / [k]ipped / [r]etry` in the CLI → next job.
- **Batch.** Fill all N, writing `artifacts/<job>/review.md` + `screenshot.png` + `answers.json`
  for each, then present a review queue. Because of the state caveat above, batch reopens and
  re-fills each job at review time from cached answers (seconds, no LLM calls).

Escalation to human mid-loop (unanswerable question, CAPTCHA, login, file upload the agent
can't perform) uses the same channel: pause, describe what's needed, wait.

**Review artifact** per job: job title/company/URL, every question with answer + source
(`profile` / `cache` / `agent`) + confidence, flagged items first, unanswered required fields,
screenshot, and the exact list of things the agent deliberately left blank and why.

---

## 6. Project structure

```
autofill/
  cli.py                  # typer: init | ingest | run | review | status | resume
  config.py               # pydantic-settings; thresholds, model names, paths
  models.py               # ★ Question, Answer, FormSchema, JobTarget, AppState — the contract
  ingest/
    resume.py             # pdf/docx → text (pdfplumber / python-docx)
    experience.py         # candidate doc → structured sections + chunks
    profile.py            # profile.yaml schema + validation (typed, hand-authored facts)
    jobs.py               # link list → normalized JobTarget (dedupe, canonicalize)
  browser/
    session.py            # Playwright persistent context, tabs, screenshots, human-like pacing
    navigator.py          # ★ find & enter the application: Apply button, iframe, modal, new tab, login wall
    extractor.py          # ★ generic DOM → FormSchema (tier 1, the workhorse)
    filler.py             # FormSchema + Answers → typed DOM interaction, read-back verification
    guards.py             # ★ submit interceptor, navigation assertion
    vision.py             # tier-2 screenshot + a11y-tree fallback
    fingerprint.py        # page-state hash for no-progress detection
  adapters/
    base.py               # ATSAdapter protocol
    registry.py           # ★ detection by URL pattern + DOM fingerprint, ordered by confidence
    generic.py greenhouse.py lever.py ashby.py workable.py smartrecruiters.py workday.py icims.py
  answering/
    context_agent.py      # ★ the Candidate Context Agent (LLM client, retrieval, tools)
    resolver.py           # ★ deterministic → cache → agent → human
    cache.py
    validators.py         # enum/format/length checks, single re-ask
    guardrails.py         # never-answer list enforcement
    prompts/
  orchestrator/
    loop.py               # ★ per-job fixed-point fill loop
    runner.py             # queue across jobs, retries, budgets
    states.py             # status machine
    hitl.py               # pause/resume, review artifact generation
  store/
    db.py repo.py         # sqlite schema, migrations, repositories
  observability/
    logging.py events.py  # structured events, per-step screenshots
data/
  profile.yaml            # hand-authored deterministic facts
  candidate_experience.md
  resume.pdf
  jobs.yaml
config/
  settings.yaml
  never_answer.yaml
artifacts/                # gitignored: screenshots, review docs, run logs
tests/
  fixtures/pages/         # ★ saved HTML of real ATS forms — offline test corpus
  test_extractor.py test_resolver.py test_guards.py test_adapters.py
```

★ = files where the design risk concentrates. Get `models.py`, `extractor.py`, `resolver.py`,
`loop.py` and `guards.py` right and the rest is mechanical.

---

## 7. Milestones

| # | Deliverable | Done when |
|---|---|---|
| M0 | Skeleton: `models.py`, config, SQLite schema, CLI stubs | `autofill status` runs, no browser |
| M1 | Ingestion + `profile.yaml` + deterministic resolver | Given a hand-written `FormSchema` fixture, 60%+ fields answered with zero LLM calls; unit tested |
| M2 | Browser session + generic extractor | Extracts correct `FormSchema` from ≥6 saved ATS HTML fixtures offline |
| M3 | Filler + submit guards + Greenhouse adapter | One real Greenhouse job filled end-to-end, tab left open, guard provably blocks a forced submit |
| M4 | Candidate Context Agent + cache + confidence routing | Free-text and enum questions answered, flagged correctly, cache hit rate measured |
| M5 | Orchestrator loop + state machine + resumability | 5-job queue runs, interruptible, resumes, no duplicate work |
| M6 | Adapters: Lever, Ashby, Workable, SmartRecruiters; **Workday last** | 20-job mixed-board run, ≥80% reach `AWAITING_REVIEW` |
| M7 | HITL review UX + run report + vision fallback | Review flow is pleasant enough to use daily |

Workday is deliberately last: multi-page wizard, account creation, iframes, custom widgets. It
is 3–5× the effort of the others and shouldn't shape the core abstractions.

---

## 8. Trade-offs to decide before M0

1. **Browser driver.** Playwright (recommended: programmatic, resumable, file upload, persistent
   profile, testable) vs. driving the user's Chrome via the Claude-in-Chrome extension (better
   for reusing existing logins, worse for automation and testing) vs. computer-use (slowest,
   most brittle — only useful for native-app edge cases). *Recommendation: Playwright with a
   persistent profile the user logs into once.*
2. **Retrieval over the experience doc.** Stuff the whole doc into context (simpler, more
   accurate, fine up to ~20k tokens) vs. embed + retrieve (needed for a large corpus). *Start
   with stuffing; add retrieval behind an interface only if the doc outgrows the budget.*
3. **How the Candidate Context Agent is hosted.** In-process LLM calls vs. a separate agent
   process/MCP server the orchestrator talks to. In-process is simpler and 10× easier to test;
   a separate service is only worth it if you want the context agent reusable elsewhere.
4. **Answer cache scope.** Global for factual questions; per-job for anything referencing the
   company or JD (cover letters, "why this role"). Cache key must include a flag for
   JD-dependence or you will paste the wrong company name into an essay.
5. **Confidence thresholds.** Aggressive autofill (fewer interruptions, more review burden) vs.
   conservative (more pauses, higher trust). *Start conservative — trust is earned once.*
6. **Where partial state lives.** Re-fill from cached answers vs. attempting true browser-state
   restore. *Re-fill; state restore is a trap.*
7. **Login/account creation.** Some ATSs (Workday, iCIMS, Taleo) require an account per company.
   The agent must not create accounts or handle passwords — design an explicit `BLOCKED_LOGIN`
   status and a human handoff rather than trying to automate it.
8. **Bot detection.** Human-like pacing, real persistent profile, serial execution, no CAPTCHA
   solving (hand to human). Keep per-domain rate limits in config.

---

## 9. Open questions for you

1. Which ATSs actually appear in your job list? That should reorder M6 (and possibly M3).
2. Attended or batch as the default review mode?
3. Do you want cover letters / long-form essays generated, or always escalated to you?
4. Which LLM path — Anthropic API directly, or the Claude Agent SDK, for the context agent?
5. EEO/demographic fields: always blank for you to fill, or fill from `profile.yaml` if you
   explicitly declare values there?
6. One machine, single user, local-only? (Assumed yes — no multi-tenancy, no secrets service.)
