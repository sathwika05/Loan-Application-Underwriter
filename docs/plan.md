# UC24.1 — GenAI-Powered Loan Application Underwriter Assignment: 30-Minute Build Plan

Goal: one working end-to-end slice running locally. A submitter logs a loan application, the rules engine filters and ranks underwriters, the GenAI step refines the pick and writes a rationale, the underwriter is assigned and notified, and the submitter sees the rationale and can ask questions about it.

Stack: React + Vite · Python + FastAPI · PostgreSQL 18 (local) · SQLAlchemy · OpenAI (`openai` SDK) · LangGraph (AI graph).

---

## 1. Assumptions and out of scope

**Assumptions**
- A1. Single local machine: Postgres on `localhost:5432`, database `underwriter`, credentials in `backend/.env`.
- A2. `OPENAI_API_KEY` is set in `backend/.env`. If it's missing or the call fails, the system falls back to the rules-engine pick and a templated rationale, so the demo never breaks.
- A3. No login. The submitter and manager are free-text names in the UI.
- A4. Underwriters are seeded (about 8), with loan types, regions, an authority limit, a maximum caseload, and a profile.
- A5. Loan types: mortgage, auto, personal, business. Regions: East, West, Midwest, South.
- A6. "Notified" means an in-app notification row, not email.
- A7. Embeddings are stored as `float8[]` and cosine similarity is computed in Python. pgvector is optional and comes later, once the extension is installed with admin rights.

**Out of scope (for 30 min)**
- Authentication and roles, email/SMS, document upload
- Message queue (SQS / Service Bus). Routing runs synchronously inside one transaction.
- Alembic migrations (we use `create_all`), Docker, deployment, CI
- Adaptive learning loop (weights retuned from outcomes)
- Milvus, Elastic, semantic ranker

---

## 2. Requirements

### Functional
| ID | Requirement |
|---|---|
| FR1 | A submitter creates a loan application (applicant, type, amount, region, credit score, priority, notes). |
| FR2 | The system finds eligible underwriters using hard rules: active, loan type match, region match, amount ≤ authority limit, current load < maximum caseload. Each excluded underwriter is recorded with a reason. |
| FR3 | The rules engine scores eligible underwriters (expertise, capacity, SLA risk, fairness) deterministically, with ties broken by ID. |
| FR4 | GenAI reviews the top 3, applies bounded adjustments (±0.10) for contextual fit, fairness and SLA risk, and writes a rationale. It can only choose from the shortlist. |
| FR5 | The chosen underwriter is assigned and notified, and the submitter sees the rationale and score breakdown. |
| FR6 | If no underwriter is eligible, the application is set to `QUEUED` with reasons, and a "re-route" action retries it. |
| FR7 | A manager can override an assignment. A reason is required and is recorded in the audit trail. |
| FR8 | Every routing action is written to an append-only audit trail. |
| FR9 | Natural-language questions about a decision, e.g. "Why Priya and not Raj?", are answered from stored decision data. |
| FR10 | A dashboard shows caseload per underwriter and the recent assignments. |

### Non-functional
| ID | Requirement | How |
|---|---|---|
| NFR1 | No duplicate routings | Unique `idempotency_key` on applications, plus a partial unique index allowing one active assignment per application, plus `SELECT … FOR UPDATE` on the application row |
| NFR2 | No missed routings | Status is `ROUTED` or `QUEUED`, never silently dropped; `QUEUED` is visible on the dashboard and can be re-routed |
| NFR3 | Auditable | `audit_events` stores the inputs, scores, AI output and the actor for every step |
| NFR4 | Explainable and fair | Rationale, score breakdown, exclusion reasons, fairness signal, overrides with a reason |
| NFR5 | Deterministic | Rules ranking is reproducible; AI changes are bounded and validated; AI output is stored, so a decision can be replayed without calling the LLM again |
| NFR6 | Low latency | One LLM call per routing, 15 s timeout, falls back to rules on any failure |
| NFR7 | Secrets safe | Keys only in `.env`, which is in `.gitignore` |

---

## 3. Core entities

| Entity | Field | Type | Constraints (DB = database-level) |
|---|---|---|---|
| **underwriter** | id | serial | PK (DB) |
| | name | varchar(100) | NOT NULL (DB) |
| | email | varchar(200) | NOT NULL, UNIQUE (DB) |
| | loan_types | text[] | NOT NULL (DB) |
| | regions | text[] | NOT NULL (DB) |
| | max_amount | numeric(14,2) | NOT NULL, CHECK > 0 (DB) |
| | max_caseload | int | NOT NULL, CHECK > 0 (DB) |
| | avg_turnaround_hours | numeric(6,2) | NOT NULL, default 24 |
| | quality_score | numeric(3,2) | CHECK 0–1 (DB) |
| | profile | text | |
| | embedding | float8[] | nullable |
| | active | boolean | NOT NULL, default true (DB) |
| **loan_application** | id | serial | PK (DB) |
| | idempotency_key | varchar(64) | NOT NULL, UNIQUE (DB) |
| | applicant_name, submitter | varchar(100) | NOT NULL (DB) |
| | loan_type | varchar(20) | NOT NULL, CHECK in set (DB) |
| | amount | numeric(14,2) | NOT NULL, CHECK > 0 (DB) |
| | region | varchar(20) | NOT NULL (DB) |
| | credit_score | int | CHECK 300–850 (DB) |
| | priority | varchar(10) | CHECK in (normal, high, urgent) (DB) |
| | notes | text | |
| | sla_due_at | timestamptz | NOT NULL (app sets from priority) |
| | status | varchar(12) | CHECK in (NEW, ROUTED, QUEUED) (DB) |
| | created_at | timestamptz | default now() (DB) |
| **assignment** | id | serial | PK (DB) |
| | application_id | int | FK → loan_application.id (DB), NOT NULL |
| | underwriter_id | int | FK → underwriter.id (DB), NOT NULL |
| | method | varchar(10) | CHECK in (AUTO, OVERRIDE) (DB) |
| | is_active | boolean | NOT NULL; **partial UNIQUE (application_id) WHERE is_active** (DB) |
| | rationale | text | NOT NULL |
| | decision | jsonb | candidates, scores, exclusions, AI adjustments, model |
| | override_reason | text | required when method = OVERRIDE (CHECK, DB) |
| | created_by | varchar(100) | |
| | created_at | timestamptz | default now() (DB) |
| **audit_event** | id | bigserial | PK (DB) |
| | application_id | int | FK → loan_application.id (DB), nullable |
| | event_type | varchar(30) | NOT NULL (DB) |
| | actor | varchar(100) | NOT NULL (DB) |
| | payload | jsonb | NOT NULL (DB) |
| | created_at | timestamptz | default now() (DB) |
| **notification** | id | serial | PK (DB) |
| | recipient | varchar(200) | NOT NULL (DB) |
| | application_id | int | FK → loan_application.id (DB) |
| | message | text | NOT NULL (DB) |
| | created_at | timestamptz | default now() (DB) |

The app layer checks eligibility rules, sets the SLA due time from priority, keeps fairness counts, and checks AI output stays within bounds.

**Relationships**
- loan_application **1 — 0..N** assignment (history); **at most 1 active** assignment per application (partial unique index)
- underwriter **1 — 0..N** assignment
- loan_application **1 — 0..N** audit_event
- loan_application **1 — 0..N** notification
- Current load = `COUNT(assignment WHERE is_active AND underwriter_id = X)`

---

## 4. Endpoints (FastAPI, prefix `/api`)

| Method | Path | Purpose | Request | Response |
|---|---|---|---|---|
| POST | /applications | Create and route (FR1–FR6) | `{idempotency_key, applicant_name, submitter, loan_type, amount, region, credit_score, priority, notes}` | `{application, assignment?: {underwriter, rationale, decision}, status}`; repeating the same key returns the same result |
| GET | /applications | List | `?status=` | `[{id, applicant_name, loan_type, amount, status, underwriter_name}]` |
| GET | /applications/{id} | Detail | — | `{application, active_assignment, history[], audit[]}` |
| POST | /applications/{id}/route | Re-route a QUEUED application | — | same as POST /applications |
| POST | /applications/{id}/override | Manual reassignment (FR7) | `{underwriter_id, reason (min 10 chars), actor}` | `{assignment}`; 422 if the reason is missing |
| POST | /applications/{id}/ask | Natural-language question (FR9) | `{question}` | `{answer}` |
| GET | /underwriters | Underwriters with live load | — | `[{id, name, loan_types, regions, load, max_caseload, active}]` |
| GET | /dashboard | Caseloads and recent activity | — | `{caseloads[], counts_by_status, recent_assignments[]}` |
| GET | /notifications | Notification list | `?recipient=` | `[{recipient, message, created_at}]` |

---

## 5. AI layer (LangGraph)

**Routing graph** (one run per application)

```
load_application → find_eligible ──(none)──→ queue → END
                         │(≥1)
                         ▼
                   score_rules → genai_optimize → guard → assign_and_notify → END
```

| Node | Does |
|---|---|
| load_application | Locks the application row (`FOR UPDATE`); exits if an active assignment already exists (idempotent) |
| find_eligible | Applies the hard rules and records exclusion reasons |
| score_rules | Weighted deterministic score: expertise 0.3, capacity 0.3, SLA risk 0.2, fairness 0.2, plus embedding similarity when available |
| genai_optimize | One OpenAI call with structured JSON output: `{adjustments[{id, delta, reason}], chosen_id, rationale}` |
| guard | Clamps deltas to ±0.10, keeps the pick within the top 3, takes the highest final score; on any LLM failure falls back to the rules pick and a templated rationale |
| assign_and_notify | Inserts the assignment, audit event and 2 notifications in one transaction |
| queue | Sets status = QUEUED and writes an audit event with the exclusion reasons |

**Edges:** linear, plus one conditional edge after `find_eligible` (`eligible empty → queue`).

**State (TypedDict):** `application_id, application, underwriters, loads, eligible[], excluded[{id, reasons}], scored[{id, components, rules_score}], ai_output, final_choice, rationale, used_fallback, errors[]`

**Q&A agent ("ask" endpoint)** is a small tool-calling loop using these tools:
- `get_decision(application_id)`: candidates, scores, exclusions, rationale
- `get_underwriter(name_or_id)`: profile and current load
- `get_audit_trail(application_id)`

The system prompt says to answer only from tool results, so the agent can't invent facts.

---

## 6. UI pages (React + Vite)

| Page | Workflow served |
|---|---|
| **New Application** | Submitter fills the form, submits, and sees the assigned underwriter, rationale and score table right away (FR1–FR5) |
| **Applications** (list → detail) | Track status. The detail view shows the rationale, candidate scores, excluded underwriters with reasons, the audit timeline, the override form (FR7), the "Ask about this decision" box (FR9), and a re-route button for QUEUED applications (FR6) |
| **Dashboard** | Manager sees caseload bars per underwriter, counts by status, recent assignments, and QUEUED alerts (FR10) |

---

## 7. Cuts to fit 30 minutes (first cut first)

1. Adaptive learning from outcomes
2. pgvector: use `float8[]` with cosine in Python, and drop embeddings entirely if time is tight
3. Q&A tool-calling agent: falls back to a single LLM call over the stored decision JSON
4. LangGraph: falls back to plain Python functions in the same node order, with the same behaviour
5. Notifications page: notifications are still written, but shown only on the detail page
6. Dashboard: caseloads table only
7. Styling polish

**Never cut:** idempotency and the unique active-assignment index, the rules engine, AI output bounded by the guard, rationale, override with reason, audit trail.
