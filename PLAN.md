# Autofill Job Application — Execution Plan

**Status:** proposed design. No implementation code written yet.
**Stack assumption:** Python 3.11+ (inferred from `.gitignore`), Playwright, SQLite, Pydantic, Typer.

**Design commitment (v2):** **one generic browser path. No per-ATS adapters, no vendor-specific
code.** SmartRecruiters, Greenhouse, Workday, Eightfold, Lever, Ashby, iCIMS and unknown career
pages all go through the same extractor and the same interaction primitives. Vendor diversity
lives in the **test corpus**, never in the code.

---

## 1. What the system does

```
inputs                    per-job agentic loop                        output
──────                    ────────────────────                        ──────
resume.pdf        ┐
experience.md     ├─►  Candidate Context Agent ◄──┐
profile.yaml      ┘                               │ Question/Answer
                                                  │
jobs.yaml ──► Navigator ──► Extractor ──► Resolver┘──► Filler ──► Verifier
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
    field_id: str              # stable hash of (domain, frame_path, name/id/label)
    canonical_key: str | None  # "email", "phone", "years_experience", "work_auth", ...
    label: str                 # human-readable question text
    help_text: str | None
    type: FieldType            # text|textarea|select|combobox|multiselect|radio|checkbox|
                               # file|date|phone|email|url|number|consent|unknown
    widget: WidgetKind         # native | aria | custom | unknown  ← how to drive it, not who built it
    options: list[Option]      # for enum types — the ONLY legal values (may be lazily probed)
    required: bool
    max_length: int | None
    current_value: str | None
    locator: Locator           # frame path + selector; opaque to the answering layer
    confidence: float          # extractor's confidence it parsed this correctly
```

`widget` is the replacement for "which ATS is this". We classify **behaviour**, not vendor.

### 2.2 One extraction path, two tiers of evidence

```
Tier 1  Structural extraction   default, ~90% of fields
        Frame-tree walk → form-control discovery (incl. ARIA widgets, shadow DOM) →
        label derivation → type/option inference → confidence score

Tier 2  Perceptual fallback     only for fields tier 1 scores low, or canvas/opaque regions
        Screenshot + accessibility tree → LLM → field list → re-anchored to real locators
```

There is no tier 0. Nothing keys off `greenhouse.io` or `myworkdayjobs.com`.

**Label derivation cascade** (first hit wins, confidence decays down the list):
`<label for>` → wrapping `<label>` → `aria-labelledby` → `aria-label` → `<fieldset><legend>` →
nearest preceding text node in the same layout block → `placeholder` → `name`/`id` humanized.
Sub-labels (`help_text`) come from `aria-describedby` and adjacent hint elements.

**Trade-off, stated plainly:** a hand-written Greenhouse adapter would beat this on Greenhouse.
The generic path trades a few points of per-vendor accuracy for working on the long tail of
company career pages you've never seen — which is the actual requirement. The cost shows up as
more tier-2 calls and more low-confidence flags on unusual forms. Accept it, and measure it.

### 2.3 Site memory replaces adapters

Adapters are hand-written knowledge about a vendor. **Site memory is learned knowledge about a
page**, produced by the generic machinery at runtime and cached:

```sql
site_memory(domain, page_fingerprint, apply_entry_selector, extraction_tier_used,
            widget_strategies_json, field_id_map_json, successes, failures, updated_at)
```

Second time you hit a SmartRecruiters form — or the same company's bespoke page — the run skips
straight to the strategies that worked, with no vendor code and no manual maintenance. Entries
are invalidated on failure and re-learned. This gives most of the speed and reliability benefit
of adapters while staying fully general.

### 2.4 Generic strategies for the hard cases

This is where "no adapters" has to earn its keep. Each hard case gets a **behavioural protocol**,
not a vendor branch.

**Finding the application form on an unknown page.**
Walk the full frame tree; score every frame and container by form density (count of fillable
controls × presence of an identity field like email). If nothing scores, rank clickable elements
by apply-intent — text (`apply`, `apply now`, `submit application`, i18n variants), `href`
patterns, button prominence — click the top candidate, wait for network idle, re-scan. Bounded to
2 hops, then `NEEDS_HUMAN`. This handles the JD-page → application-page hop uniformly, whether
that's a modal, a new tab, or a redirect to a different domain.

**Iframes.** Frames are first-class: extraction always runs over the frame tree, and every
`Locator` carries a frame path. No special-casing — a Greenhouse form embedded in a company page
is just a frame with high form density.

**Custom dropdowns / comboboxes / typeaheads.** Probe, don't assume:

1. If it's a native `<select>`, use it. Done.
2. If it has ARIA (`role=combobox|listbox`, `aria-expanded`, `aria-controls`), follow the ARIA
   contract: expand, read `role=option` descendants, click the match.
3. Otherwise **click-and-diff**: click the control, snapshot the DOM before/after, and look for a
   newly-visible overlay whose children look option-like (repeated sibling structure, short text,
   pointer cursor). Those become `options`. Match by exact → normalized → fuzzy text.
4. If the list is empty until typed into (async typeahead), type the intended value, wait for the
   list to populate, then match. If the intended value isn't offered, escalate rather than
   forcing free text.
5. **Always verify by read-back.** After selecting, re-read the control's rendered value. Read-back
   verification is what makes speculative interaction safe — a wrong guess is caught, not shipped.

Option enumeration is lazy: we only probe a combobox when we actually have an answer to place in
it, because probing costs a click and a DOM diff.

**File upload (resume, cover letter, portfolio).**
Query *all* `input[type=file]` in the frame tree including hidden and zero-size ones (most styled
"Upload" buttons are a label over a hidden input) and call `set_input_files` directly — this
bypasses the OS file dialog entirely and is the most reliable path. If no input exists, click the
upload-looking control with a `filechooser` listener armed. If that fails, dispatch a synthetic
`DataTransfer` drop onto the drag-and-drop zone. Then verify by read-back: the filename should
appear in the DOM. Three strategies, tried in order, zero vendor knowledge.

**Multi-step wizards (Workday-style, but not Workday-specific).**
No step model is hardcoded. After each fill batch we re-extract and compute a page fingerprint.
When no required fields remain unfilled on the current view, we look for a **forward control** and
classify it:

- Signals for *advance*: text matches next/continue/save-and-continue; a progress indicator exists
  and shows remaining steps; the page has no review/confirm summary heading.
- Signals for *terminal submit*: text matches submit/send application/finish; the page presents a
  read-only review summary; an attestation checkbox is present.
- **Ambiguity resolves to SUBMIT.** The classifier fails safe toward stopping. A wrongly-stopped
  wizard costs the human one click; a wrongly-advanced one sends a real application.

Progress is asserted by fingerprint change; unchanged fingerprint after an advance attempt →
`NEEDS_HUMAN`. Wizards therefore need no special code path at all — they are just the fill loop
iterating more times.

**Shadow DOM.** Pierce open shadow roots during the walk. Closed roots and canvas-rendered forms
(Eightfold-style SPAs sometimes get close) fall through to tier 2.

**Login walls / per-company accounts.** Detected generically (password field + no application
form). The agent never creates accounts or handles credentials — status `BLOCKED_LOGIN`, handed to
the human, then resumed on the same persistent browser profile.

### 2.5 Pipeline of narrow LLM calls, not one autonomous mega-agent

Determinism, cost, cacheability and auditability all favour a pipeline. The LLM is used at exactly
four points: (a) tier-2 field extraction, (b) answer generation for questions with no deterministic
mapping, (c) forward-control classification when heuristics are ambiguous, (d) fuzzy option
matching when text matching fails. Everything else is code.

### 2.6 Answer resolution order (cheapest → most expensive)

1. **Deterministic profile map** — `canonical_key` → value from `profile.yaml`. Name, email, phone,
   location, LinkedIn, GitHub, portfolio, work authorization, notice period, current/expected CTC,
   relocation. Never LLM-generated. ~60–70% of fields.
2. **Answer cache** — normalized question hash → prior answer. Applications ask the same ~200
   questions forever; after a dozen runs this covers most of the remainder.
3. **Candidate Context Agent (LLM)** — retrieves from experience doc + resume + the scraped job
   description. Returns `{value, confidence, rationale, sources}`.
4. **Escalate to human** — low confidence, or the question is on the never-answer list.

Enum-typed questions are **constrained**: the model gets `options` and its output is validated
against them; on failure re-asked once, then escalated. No free text into a `<select>`.

### 2.7 Guardrails — what the agent must never invent

Hard-coded refusal list (`config/never_answer.yaml`), escalates regardless of confidence:

- Salary/compensation figures not present in `profile.yaml`
- Years of experience, degrees, certifications, GPA, employment dates not in the resume
- Work authorization, visa status, sponsorship requirement
- EEO/demographic fields (race, gender, veteran, disability) — **default: leave blank for human**
- Criminal history, background-check and drug-test consents
- Any legally-binding attestation ("I certify the above is true")
- References' contact details

### 2.8 Submit is blocked in three independent layers

Belt and braces, because one missed heuristic means a real application sent uninvited.

1. **Classifier + denylist** — the filler never issues a click on anything classified terminal.
2. **Injected page guard** (`browser/guards.py`) — init script intercepting
   `HTMLFormElement.prototype.submit`, `submit` events, `requestSubmit`, and clicks on
   `[type=submit]`, calling `preventDefault()` and recording the attempt. Lifted only when the
   human takes the tab.
3. **Navigation assertion** — URL or heading matching `/thank|confirm|success|submitted/` halts the
   run and is logged loudly as a defect.

---

## 3. The per-job loop (fixed point, not a single pass)

```
open(url) → resolve redirects
  → detect login wall → BLOCKED_LOGIN if present
  → locate application form (frame scoring, ≤2 apply-intent hops)
  → scrape job description (essay context)
  → consult site_memory(domain, fingerprint)
LOOP (max N≈10):
  → schema = extract(page)                    # tier 1, tier 2 for low-confidence fields
  → open_q = [q for q in schema if q.required and not q.filled]
  → if open_q:
        answers = resolver.batch_answer(open_q)
        filler.apply(page, answers)           # widget protocols + read-back verification
        verifier.check(page)                  # client-side validation errors
  → else:
        ctrl = classify_forward_control(page)
        if ctrl is TERMINAL_SUBMIT or AMBIGUOUS: break        # ← fail safe
        if ctrl is ADVANCE: click(ctrl)
        else: break with NEEDS_HUMAN
  → if page_fingerprint unchanged and no progress: break with NEEDS_HUMAN
→ screenshot + review artifact → AWAITING_REVIEW, notify human, leave tab open
→ write site_memory on success
```

**Why a loop:** conditional fields ("Do you require sponsorship?" reveals three more), multi-step
wizards, and validation that only surfaces after blur. Re-extraction after every fill batch is the
only robust approach — and it is what lets one code path handle both a single-page Greenhouse form
and an eight-step Workday wizard.

**Loop safety:** iteration cap, no-progress fingerprint detection, per-job wall-clock timeout,
per-job LLM token budget.

---

## 4. State, resumability, concurrency

SQLite (`state.db`) for the queue, answer cache, site memory and idempotency; per-job JSON/MD
artifacts for anything a human reads.

```sql
applications(id, job_url, canonical_url, company, title, status, attempt,
             last_error, artifact_dir, created_at, updated_at)
questions(id, application_id, field_id, label, type, widget, answer, source,
          confidence, flagged)
answer_cache(question_hash, label_sample, type, jd_dependent, answer, uses, last_used)
site_memory(domain, page_fingerprint, apply_entry_selector, extraction_tier_used,
            widget_strategies_json, successes, failures, updated_at)
events(id, application_id, ts, kind, payload_json)
```

**Status machine:**
`PENDING → OPENING → FORM_FOUND → FILLING → (NEEDS_HUMAN | BLOCKED_LOGIN | AWAITING_REVIEW)
→ SUBMITTED_BY_HUMAN | SKIPPED | FAILED`

**Resumability, honestly:** the run *queue* resumes perfectly. A *half-filled remote form*
generally does **not** survive closing the browser — most ATS forms hold state client-side only.
Mitigations: a persistent Playwright context (`user_data_dir`) keeps cookies/logins, and on ATSs
that autosave drafts the partial application does survive. Otherwise, resume means **re-fill from
cached answers** — fast and nearly free, since every answer is cached and site memory replays the
working strategies. Treat re-fill, not state restore, as the recovery path.

**Concurrency:** serial by default — human review is serial, parallel filling makes the review
queue chaotic and trips bot detection. Optional bounded parallelism for an *extract-only* pre-pass
that warms the answer cache and site memory before the filling run. That's where parallelism pays.

---

## 5. Human-in-the-loop

- **Attended (default).** One job at a time, headed browser. Filled → terminal prints flagged
  answers → user reviews in the live tab, edits, clicks Submit → `[s]ubmitted / [k]ip / [r]etry`.
- **Batch.** Fill all N, write `artifacts/<job>/review.md` + `screenshot.png` + `answers.json`,
  then present a review queue. Because of the state caveat, batch reopens and re-fills each job at
  review time from cached answers (seconds, no LLM calls).

Mid-loop escalation (unanswerable question, CAPTCHA, login, ambiguous submit control) uses the same
channel: pause, describe what's needed, wait.

**Review artifact** per job: title/company/URL, every question with answer + source
(`profile`/`cache`/`agent`) + confidence, flagged items first, unanswered required fields,
screenshot, and an explicit list of what was deliberately left blank and why.

---

## 6. Project structure

```
autofill/
  cli.py                  # typer: init | ingest | run | review | status | resume | corpus
  config.py               # thresholds, model names, paths, rate limits
  models.py               # ★ Question, Answer, FormSchema, WidgetKind, Locator, AppState
  ingest/
    resume.py             # pdf/docx → text
    experience.py         # candidate doc → structured sections
    profile.py            # profile.yaml schema + validation
    jobs.py               # link list → normalized JobTarget (dedupe, canonicalize)
  browser/
    session.py            # Playwright persistent context, tabs, pacing, screenshots
    frames.py             # ★ frame-tree walk, shadow-DOM piercing, form-density scoring
    navigator.py          # ★ locate application form; apply-intent hops; login-wall detection
    extractor.py          # ★ tier-1 structural extraction → FormSchema
    labels.py             # label derivation cascade + confidence
    widgets.py            # ★ widget classification + interaction protocols (combobox, radio,
                          #   date, multiselect) — behaviour-based, no vendor names
    upload.py             # 3-strategy file upload
    filler.py             # apply answers, read-back verification
    forward.py            # ★ advance-vs-submit classifier (fails safe to submit)
    guards.py             # ★ submit interceptor, navigation assertion
    vision.py             # tier-2 screenshot + a11y-tree fallback, re-anchored to locators
    fingerprint.py        # page-state hash for progress/no-progress detection
  answering/
    context_agent.py      # ★ Candidate Context Agent (LLM, retrieval)
    resolver.py           # ★ deterministic → cache → agent → human
    cache.py validators.py guardrails.py prompts/
  orchestrator/
    loop.py               # ★ per-job fixed-point loop
    runner.py states.py hitl.py
  store/
    db.py repo.py site_memory.py
  observability/
    logging.py events.py metrics.py     # extraction accuracy, fill rate, tier-2 rate
data/     profile.yaml  candidate_experience.md  resume.pdf  jobs.yaml
config/   settings.yaml  never_answer.yaml
artifacts/                # gitignored
tests/
  corpus/                 # ★ saved HTML+a11y snapshots of real forms across many ATSs
    smartrecruiters/ greenhouse/ workday/ eightfold/ lever/ ashby/ icims/ bespoke/
    expected/             # hand-labelled FormSchema per snapshot — the ground truth
  test_extractor.py test_widgets.py test_forward.py test_resolver.py test_guards.py
```

★ = where the design risk concentrates.

**Note the shape of `tests/corpus/`:** vendor names appear there and nowhere else. That's the whole
strategy — vendor diversity as *measurement*, not as *branching*.

---

## 7. Milestones (re-sequenced around the generic path)

| # | Deliverable | Done when |
|---|---|---|
| M0 | Skeleton: `models.py`, config, SQLite, CLI stubs | `autofill status` runs, no browser |
| M1 | Ingestion + `profile.yaml` + deterministic resolver | ≥60% of fields on a fixture schema answered with zero LLM calls |
| M2 | **Corpus harness** — `autofill corpus capture <url>` saves HTML+a11y+screenshot; hand-label expected schemas | ≥25 snapshots across ≥8 distinct ATSs, offline replay works |
| M3 | **Tier-1 extractor + label cascade**, scored against the corpus | ≥90% field detection, ≥85% correct label/type on the corpus, *no vendor branches* |
| M4 | **Interaction primitives**: widget protocols, upload, read-back verification, submit guards | One real job on each of 3 different ATSs filled end-to-end; guard provably blocks a forced submit |
| M5 | Context agent + cache + confidence routing | Free-text and enum questions answered and flagged; cache hit rate measured |
| M6 | Orchestrator loop + wizard traversal + forward classifier + state machine | An 8-step wizard traversed to its final review page and stopped there, with no wizard-specific code |
| M7 | Tier-2 vision fallback + site memory | Corpus fields tier 1 misses get recovered; second visit to a domain is measurably faster |
| M8 | HITL review UX + run report + coverage dashboard | 20-job mixed-board run, ≥80% reach `AWAITING_REVIEW`; review flow pleasant enough to use daily |

M2 before M3 is deliberate: **build the measuring instrument before the thing being measured.**
Without an offline labelled corpus, "make the generic extractor better" is unfalsifiable and every
change is a coin flip against live sites. This is the single biggest process decision in the plan.

The old Workday-specific milestone is gone. Workday now appears only as corpus entries and as the
wizard traversal acceptance test in M6.

---

## 8. Trade-offs still open

1. **Browser driver.** You said "browser use" — worth confirming whether you mean browser
   automation generally or the `browser-use` library specifically. *Recommendation: Playwright
   directly.* It gives frame-tree access, hidden-input file upload, init-script injection for the
   submit guard, and offline HTML replay for the corpus — all four are load-bearing here, and an
   agentic wrapper makes the submit guard harder to enforce. A `browser-use`-style loop can sit on
   top later if you want it.
2. **Retrieval over the experience doc.** Stuff the whole doc (simpler, more accurate, fine to
   ~20k tokens) vs. embed + retrieve. *Start with stuffing.*
3. **Context agent hosting.** In-process LLM calls vs. a separate agent/MCP service. In-process is
   simpler and far easier to test; split it out only if you want it reusable elsewhere.
4. **Answer cache scope.** Global for factual questions; per-job for anything referencing the
   company or JD. The cache key must carry a `jd_dependent` flag or you *will* paste the wrong
   company name into an essay.
5. **Confidence thresholds.** Conservative to start — trust is earned once.
6. **Tier-2 budget.** Vision calls are the main cost driver on unknown pages. Cap per job, and let
   site memory amortize it across repeat domains.
7. **Bot detection.** Human-like pacing, persistent real profile, serial execution, no CAPTCHA
   solving (hand to human), per-domain rate limits in config.

---

## 9. Open questions for you

1. Attended or batch as the default review mode?
2. Cover letters / long-form essays — generated, or always escalated to you?
3. Which LLM path for the context agent — Anthropic API directly, or the Claude Agent SDK?
4. EEO/demographic fields: always blank, or filled from `profile.yaml` if you explicitly declare
   values there?
5. Confirm: one machine, single user, local-only? (Assumed yes — no multi-tenancy, no secrets
   service.)
