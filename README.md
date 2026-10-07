# centrAlign — Autonomous AI Task Worker

A prototype of an AI worker that takes a natural-language goal and autonomously
completes it by operating a real browser against a small simulated company
environment: a vendor email inbox and an internal Accounts Payable (AP) system.

Example goal: *"Find the latest invoice from Nimbus Cloud Services, extract the
amount and due date, and enter it into our internal AP system."*

The agent plans its own steps, reads real rendered pages (text + screenshot) after
every action, remembers facts it discovers, retries or adapts when something fails,
asks a human when it's genuinely stuck or the data is ambiguous, and — critically —
the result is checked by the orchestrator against the real internal database
independently of whatever the agent itself claims.

## Quick start

Requirements: Python 3.11+, a Google Gemini API key (free tier, no credit card -
get one at [aistudio.google.com](https://aistudio.google.com/) → "Get API key").

```bash
cd centrAlign
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

cp .env.example .env
# edit .env and set GEMINI_API_KEY=AIza...

RESET_DB=1 ./run.sh   # first run: wipes the AP system DB so demo scenarios are fresh
```

Then open **http://127.0.0.1:5050** — this is the dashboard: type a goal, watch
the agent work, answer it if it asks a question, and read the final verified
report. (Port 5050, not 5000 - macOS's AirPlay Receiver squats on 5000 by
default and will return a confusing "403 Forbidden" instead of your dashboard
if you point it there.) You can also open the two simulated company apps
directly to see the starting state the agent sees: **http://127.0.0.1:5001**
(Vendor Mail) and **http://127.0.0.1:5002** (Internal AP System, login `ap_agent` /
`CentrAlign#2026`).

On subsequent runs, omit `RESET_DB=1` to keep previously entered invoices (or set
it again to reset back to the clean demo state).

### Demo scenarios to try

- **Happy path, multi-step extraction:** *"Find the latest invoice from Nimbus
  Cloud Services, extract the amount and due date, and record it in the internal
  AP system."* There are three Nimbus emails; two share a vendor but different
  invoice numbers/dates, and one is a reminder with no attachment — the agent has
  to correctly identify the single latest invoice. The AP system also silently
  fails the first submission attempt (simulated transient error), so this run
  also exercises failure-detection + retry.
- **Ambiguous data → clarification:** *"Record the latest invoice from Brightline
  Logistics in the AP system."* That invoice's amount is marked "TBD pending
  reconciliation" — a well-built agent should refuse to guess and call `ask_user`
  instead of inventing a number.
- **Nonexistent data → honest failure:** *"Find and record the latest invoice
  from Acme Papers."* Acme has no invoices in the inbox at all. The agent should
  report that it could not find one rather than fabricating one.
- **Generalization check:** *"Check what's recorded in the AP system for Harbor
  Office Supplies and tell me if anything is missing."* A read-only task with no
  "enter data" step at all, to show the same tool set and loop handle a
  differently-shaped goal with no code changes.

### Running tests (no LLM calls, fast)

```bash
RESET_DB=1 python3 apps/vendor_mail/app.py &
RESET_DB=1 python3 apps/ap_system/app.py &
sleep 1
python3 tests/test_browser_flow.py     # scripted browser automation smoke test
python3 tests/test_verification.py     # independent-verification logic test
```

## Architecture

```
┌─────────────────┐        ┌───────────────────────────────────────┐
│    Dashboard     │  HTTP  │              Orchestrator              │
│  (Flask + JS,    │◄──────►│  agent/orchestrator.py                 │
│   port 5050)     │        │  - deterministic state machine         │
└─────────────────┘        │  - Gemini tool-use loop (per task,     │
                             │    on its own thread)                  │
                             │  - step cap / consecutive-error cap    │
                             │  - pauses for ask_user, resumes on     │
                             │    human answer                        │
                             │  - independent post-hoc verification   │
                             └──────┬─────────────────┬───────────────┘
                                    │                 │
                        tool calls  │                 │ real HTTP (ground truth)
                                    ▼                 ▼
                     ┌───────────────────┐   ┌──────────────────────┐
                     │  Playwright        │   │ /api/verify           │
                     │  (real Chromium)   │   │ /api/latest_invoice   │
                     └─────────┬──────────┘   │ (read-only oracle     │
                               │               │  endpoints)           │
                   ┌───────────┴───────────┐   └──────────┬────────────┘
                   ▼                       ▼              │
         ┌───────────────────┐   ┌──────────────────────┐ │
         │   Vendor Mail      │   │  Internal AP System   │◄┘
         │   (Flask, :5001)   │   │  (Flask, :5002)       │
         │   mock inbox +     │   │  login, vendor search, │
         │   invoice docs     │   │  invoice entry form,   │
         │   (seeded data)    │   │  real SQLite DB        │
         └───────────────────┘   └──────────────────────┘
```

**The two "company" apps are real, independently running Flask web apps with real
HTML forms and a real SQLite database** — not mocked API calls. The agent
operates them the same way a human would: by navigating, reading rendered pages,
clicking, and typing. This was a deliberate scope choice (see Decisions below):
narrow but genuinely working, rather than broad but mocked.

### The agentic loop (`agent/orchestrator.py`)

1. The human submits a goal via the dashboard → `create_task()` spawns a
   background thread running `_run_task()`.
2. Each iteration calls the Gemini `generate_content` API (`google-genai` SDK)
   with the full tool set (`agent/tools.py`, converted to Gemini
   `FunctionDeclaration`s via `parameters_json_schema`) and the conversation
   so far.
3. Every `browser_navigate` / `browser_click` / `browser_type` tool call
   **automatically returns an observation** of the resulting page — visible
   text, the links/buttons/input fields present, and a screenshot (sent back to
   Gemini as an inline image part in the very next turn, so it is genuinely
   looking at the result, not just reading a text diff). This directly
   implements "observe the result of each action, decide the next one."
4. `remember` / `recall` give the agent an explicit scratchpad, separate from
   conversation history, for facts worth keeping (e.g. an extracted amount) —
   shown in the final report under "Facts remembered."
5. Tool errors (a missing element, a 500 from the AP system, a 404) are caught
   and fed back to the model as a tool error, not raised — the model sees the
   failure and decides whether to retry, try a different approach, or escalate.
   If 4 tool calls in a row error out, the orchestrator injects a direct nudge
   ("stop retrying the same approach") rather than letting it loop forever; a
   hard step cap (`AGENT_MAX_STEPS`, default 40) is the final backstop.
6. `ask_user` blocks the task thread on a `threading.Event`; the dashboard shows
   the question and a reply box; `POST /api/tasks/<id>/answer` sets the event
   and the loop resumes with the human's answer as the tool result.
7. `done(...)` ends the loop. The agent must state what it believes it
   accomplished (`success`, `summary`, and structured fields like
   `vendor_name`/`invoice_number`/`amount`/`due_date`).
8. **Independent verification runs after `done()` regardless of what the agent
   claimed.** The orchestrator calls the AP system's own `/api/verify` endpoint
   directly (bypassing the agent and the browser entirely) to check the invoice
   actually exists in the database, and cross-checks the stored amount/due date
   against `/api/latest_invoice` — a second, separate oracle endpoint exposing
   the vendor-mail seed data's ground truth, which the agent's browser tools
   never see. A task can therefore end in `verified_complete` (agent succeeded
   and the data provably matches), `verification_mismatch` (agent claimed
   success but the data is wrong/missing — a real bug the system catches),
   `completed_unverified` (task type the orchestrator doesn't know how to check,
   e.g. a read-only question), or `failed` (agent itself reported failure).

### Generalization

Only `agent/prompts.py` (environment description) and the seed data under
`apps/*/seed_data.py` / `apps/ap_system/db.py` are specific to the invoice
workflow. `agent/tools.py`, `agent/browser.py`, `agent/memory.py`, and the entire
orchestration loop in `agent/orchestrator.py` know nothing about invoices — they
operate on "a page", "a field", "a click target". The "read-only question" demo
scenario above (checking Harbor Office Supplies' records) exercises the identical
tool set and loop with no `done()` fields populated beyond `summary`, and no code
changes. The one piece that is intentionally domain-aware is the independent
verification step, since "did it work" has to mean something concrete for this
narrow prototype — see Limitations.

## Key design decisions

- **A real browser against real HTML, not a mocked tool API.** The brief
  explicitly rewards "execution" over "explains what should be done" and warns
  against broad-but-mocked systems. Driving actual Playwright against actual
  Flask-rendered forms means the agent has to deal with real DOM structure, real
  redirects, and a real (simulated) transient failure — not a scripted happy path.
- **Action and observation are one tool call, not two.** Early designs had a
  separate `browser_read` the model had to remember to call after every action.
  Folding the observation into the result of `navigate`/`click`/`type` makes the
  "observe before deciding" behavior structural rather than something the prompt
  has to beg for.
- **Screenshots are sent to the model as images, not just described in text.**
  Gemini has vision; using it means the agent is closer to genuinely "using a
  computer" rather than parsing a text dump, and it's a more honest test of
  whether the underlying model can ground itself in a real UI.
- **Verification is independent of the agent, on purpose.** An agent grading its
  own homework ("I did it, trust me") is close to meaningless. The orchestrator
  re-derives the answer from a read-only oracle the agent's tools never touch and
  compares it to what's actually in the AP system's database — this is the
  difference between "claims to have verified" and "was actually verified." This
  caught a real bug during testing: in an "ambiguous data → ask the user" run, the
  user told the agent to hold off on submitting, and the agent correctly complied
  — but the orchestrator still went and checked the AP system for an entry that
  was never meant to exist, and flagged it as a "mismatch." The fix was adding an
  explicit `submitted_to_ap_system` field to `done()` so the agent states whether
  it actually wrote data this run; verification now checks *that claim* against
  ground truth (including sanity-checking a "no action taken" claim by confirming
  nothing was actually written), instead of assuming every `done()` call implies a
  write happened. Independent verification is only as good as what it chooses to
  check — this was a useful reminder of that.
- **A hard step cap and a consecutive-error cap, not just prompting.** Autonomy
  without a backstop is a liability. The LLM decides strategy; the orchestrator
  guarantees termination.
- **`ask_user` is a tool, not a fallback.** Clarification is modeled as a first
  class action the agent can choose (ambiguous amount, destructive-feeling
  action, stuck after retries) rather than a special "I give up" path — the
  system prompt tells it explicitly when to prefer asking over guessing.
- **Flask + server-rendered HTML + vanilla JS, no Node/build step.** The
  environment this was built in has no Node/npm installed; a build-free stack
  also means fewer moving parts to explain/debug under time pressure, which
  the brief explicitly values ("engineering judgment").
- **Polling instead of websockets/SSE for the dashboard.** Simpler, and at a
  1.2s interval the live trace still feels real-time for a demo; not worth the
  added complexity for this scope.

## Models, APIs, frameworks, and external services used

- **Google Gemini** (model configurable via `AGENT_MODEL`, default
  `gemini-flash-lite-latest`) via the official `google-genai` Python SDK, using
  native tool use (function calling, via `FunctionDeclaration`/
  `parameters_json_schema`) and vision (screenshots as inline image parts).
  Used on its free tier - no payment method required. This is the only
  external service the prototype depends on.
- **Playwright** (Python, Chromium) for real browser automation.
- **Flask** for all three web processes (dashboard, Vendor Mail, AP System).
- **SQLite** for the AP system's persistent ground-truth database.
- No other third-party services, no cloud infra, no pre-built agent framework —
  the tool-use loop, memory, retry logic, and verification are hand-written in
  `agent/` so the full decision logic is inspectable in one place.

## Assumptions

- A single human operator is driving one task at a time per browser session;
  the prototype does not attempt multi-tenant isolation or concurrent-task
  browser pooling (each task does get its own `BrowserSession`/Chromium
  instance, so concurrent tasks don't collide, but this wasn't stress-tested).
- "Verification" is scoped to what this environment can actually check: that an
  AP invoice entry exists and matches the source invoice. A general-purpose
  worker would need a more general notion of "did this achieve the goal."
- The AP login credentials are given to the agent in its system prompt, as a
  stand-in for "credentials a human operator has already been granted" — the
  agent is not expected to discover or guess them.
- No real company data, credentials, or third-party systems are used anywhere;
  both "company applications" are fully synthetic Flask apps with seeded fake
  data, per the brief's instruction to use sandbox/mock environments.

## Known limitations

- Verification logic (`_independent_verify` in `agent/orchestrator.py`) is
  specific to the invoice-entry shape of task (`vendor_name` + `invoice_number`
  reported by `done()`). A task with a different success shape (e.g. "send a
  summary email") would report `completed_unverified` rather than being
  meaningfully checked — the orchestrator loop and tools generalize, the
  verification oracle does not (yet).
  
- Element targeting in `agent/browser.py` resolves by visible text/label with a
  few fallback heuristics. It works well against the two apps here, but a
  visually-dense or heavily-JS-driven real-world site would need a more
  robust targeting strategy (e.g. accessibility-tree-based grounding, or a true
  computer-use model that clicks by (x, y) screen coordinates).
- Single-process, in-memory task registry (`agent/orchestrator.py: TASKS` dict)
  — restarting the dashboard process loses the task list and in-flight tasks.
  Fine for a prototype demo, not for production.
- No authentication on the dashboard itself; it's a local prototype.
- The "flaky first submission" failure is a fixed, deterministic script (always
  fails exactly once per unique vendor+invoice). It proves the retry path works
  but isn't a stand-in for the long tail of real-world failure modes (timeouts,
  rate limits, partial writes, CAPTCHAs, session expiry mid-task).
- `ask_user` blocks the task's worker thread indefinitely if the human never
  answers — there's no timeout/auto-escalation if a question is left hanging.
- No persistent cross-task memory (e.g. "what did we pay this vendor last
  month") — memory is scoped to a single task run by design (see
  `agent/memory.py`), which keeps this prototype simple but is a real gap for
  a long-lived worker.
- Running on Gemini's free tier means real requests-per-minute/day quotas
  apply. `_generate_with_retry` in `agent/orchestrator.py` retries transient
  429/5xx responses from the API itself with exponential backoff (visible in
  the trace as "provider retry"), but if the free-tier quota is genuinely
  exhausted the task will still fail - there's no fallback provider.

## What I'd build next with more time

1. **A general verification DSL or LLM-as-judge step**, so `done()` could carry
   arbitrary, task-specific success criteria that get checked by a second,
   independent model call or a pluggable oracle function, instead of the
   hand-written invoice-shaped check.
2. **A richer, more adversarial set of simulated company apps** — multi-page
   pagination, session timeouts mid-task, inconsistent vendor naming across
   systems, a second internal tool the agent has to cross-reference — to stress
   generalization and failure-handling harder than one happy path + two edge
   cases.
3. **Persistent, queryable cross-task memory** (a real vector/KV store keyed by
   vendor/entity) so a later task benefits from facts an earlier one learned.
4. **Timeouts and escalation policies around `ask_user`** (e.g. auto-escalate
   to a different channel, or fail gracefully, if unanswered after N minutes).
5. **Parallel/concurrent task execution with proper browser-pool management**
   and a persistent task store (SQLite/Postgres instead of an in-memory dict)
   so the dashboard survives restarts.
6. **Cost/step telemetry** surfaced in the dashboard (tokens used, tool-call
   count, wall-clock time) — useful both for debugging and for a real
   production deployment's budget guardrails.
7. Replace text/label-based element targeting with a proper accessibility-tree
   snapshot (Playwright's `accessibility.snapshot()` or an ARIA-based
   approach) for more robust grounding on unfamiliar pages.

## Repository layout

```
agent/            orchestration loop, tool definitions, browser wrapper, memory
apps/vendor_mail/ mock vendor inbox (Flask, :5001) + seed data
apps/ap_system/   mock internal AP system (Flask, :5002) + SQLite DB
dashboard/        task submission UI, live trace, ask_user, final report (:5050)
tests/            LLM-free smoke tests for browser automation + verification logic
data/             SQLite DB + per-task memory scratchpads (gitignored)
evidence/         per-task screenshots captured during execution (gitignored)
run.sh            launches all three Flask processes together
```
