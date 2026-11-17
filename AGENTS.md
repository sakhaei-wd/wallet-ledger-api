# AGENTS.md — FXPay Wallet & Transaction Service

Guidance for humans and coding agents working in this repository. Prefer clarity, correctness, and concurrency safety over cleverness.

## What this project is

A Django + DRF backend that maintains per-user, per-currency wallet balances and implements three financial operations in a concurrency-safe, idempotent, and ledger-traceable way:

1. **Internal transfers** — authenticated user → another user, same currency
2. **Crypto deposit webhook** — HMAC-verified, at-most-once credit
3. **Withdrawals** — lock funds, call mock gateway via Celery, settle or unlock

## Stack

| Layer | Choice |
| --- | --- |
| Language | Python 3.12 |
| Web | Django + Django REST Framework |
| DB | PostgreSQL |
| Async jobs | Celery + Redis |
| Tests | pytest + pytest-django |
| DX | Docker Compose (`docker compose up` = full stack) |

**Suggested defaults** (override only if the user says otherwise):

- Auth: DRF `TokenAuthentication` (`Authorization: Token <key>`)
- Token obtain: `POST /api/auth/token/` with `username` + `password`
- Currencies: `USD`, `EUR`, `BTC` (string codes)
- Part 3 (withdrawals): implement
- Layout: single Django project / monorepo
- Transfer idempotency: dedicated model (user + `Idempotency-Key` + response payload/status)

## Architecture map (finalized in P1-02)

```
manage.py
requirements.txt
fxpay/                    # Django project package (settings, urls, wsgi/asgi, celery)
apps/
  wallets/                # Wallet model + balance invariants
  ledger/                 # LedgerEntry (append-only)
  transactions/           # Transaction + idempotency records / flow ownership
services/                 # Business logic lives here (not in views)
  transfers.py
  deposits.py
  withdrawals.py
tasks/                    # Celery tasks (withdrawal gateway, etc.)
api/                      # HTTP layer; urls mounted at /api/
docs/
  BACKLOG.md              # phased task checklist — single source of progress
tests/                    # pytest suite (concurrency tests for transfers required)
```

Domain boundaries:

| Concern | Responsibility |
| --- | --- |
| **Views / serializers** | HTTP parsing, auth, validation shape, status codes |
| **Services** | DB transactions, `select_for_update`, balance rules, ledger writes, status transitions |
| **Models** | Persistence, enums, constraints; no heavy orchestration |
| **Celery tasks** | Gateway I/O + calling services for settlement; idempotent on re-run |
| **LedgerEntry** | Immutable audit trail; every balance change appends a row |

### Domain invariants (non-negotiable)

- Money as `Decimal` — never `float`
- Balances never go negative under concurrency (`select_for_update` + checks inside a DB transaction)
- Opposite transfers (A→B and B→A): lock wallets in **deterministic order** (e.g. by primary key) to avoid deadlocks
- Transfers: exactly **two** ledger entries on success (debit + credit)
- Ledger is append-only in normal flow (no updates/deletes)
- Explicit status enums; reject illegal transitions
- Deposit finals (`DONE`, `FAILED`) are immutable; only `PENDING → DONE|FAILED`
- Idempotency for client transfers (`Idempotency-Key`), deposit webhooks (external id), and withdrawal task re-execution

## Conventions

- **One task at a time**: implement only the task id the user named (e.g. `do P2-03`). Stop after that task. Do not start the next unless asked.
- **Larger remaining slices**: from P2-03 onward, backlog items are batched (feature + hardening + tests in one id) to keep commits meaningful. Still do **only** the named id.
- **Backlog**: after each task, update `docs/BACKLOG.md` checkboxes to match reality.
- **Suggested commit**: after each completed task, end the summary with a single copyable commit message (message text only, in a fenced code block). Do not run `git commit` unless the user explicitly asks.
- **Commits / remotes**: commit, push, or create GitHub repos **only** when the user explicitly asks.
- **Scope**: do not invent product features beyond the brief (OpenAPI UI, admin polish, FX conversion, web UI, multi-currency conversion, etc.).
- **Diffs**: keep changes focused; no drive-by refactors.
- **Secrets**: env-based config only; never commit secrets (see P1-04).
- **Services over fat views**: business rules belong in service modules.
- **Tests**: first-class; transfer concurrency coverage is required (included in **P2-03**).

## Ask before scope

Before adding anything **not** in the implementation brief, **ask the user**. Examples that need a yes:

- OpenAPI / Swagger UI
- Django admin customization beyond what’s needed to develop
- FX / currency conversion
- Web UI or dashboards
- Extra payment rails, KYC, notifications, analytics
- Changing auth scheme, currencies, or skipping Part 3

When in doubt: implement the named backlog task only, then wait.

## How to run (target — after Docker tasks)

```bash
docker compose up --build
```

Expected services: web (Django), postgres, redis, celery worker.

Env vars live in `.env` (gitignored) with `.env.example` committed as a template.

```bash
cp .env.example .env   # then edit secrets
docker compose up --build
```

`DJANGO_SECRET_KEY` and DB credentials must come from the environment — never commit `.env`.

## How to test (target — after test scaffolding)

```bash
# Typical in-container or local pytest once the project exists
docker compose exec web pytest
# or, with local venv configured against the same settings:
pytest
```

Transfer suite must include at least one **concurrency** test (simultaneous transfers / opposing directions).

## Execution mode for agents

1. User names a task id → implement **only** that task.
2. Update `docs/BACKLOG.md`.
3. Summarize what changed.
4. End with a **Suggested commit** (copyable message only).
5. **Stop** and wait for the next task id.

Do not start the next backlog id unless the user asks. Remaining ids are intentionally larger (often a full feature vertical).
