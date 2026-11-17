# FXPay — Wallet & Transaction Service

Django + DRF backend that maintains per-user, per-currency wallet balances and implements concurrency-safe, idempotent, ledger-traceable financial operations:

1. **Internal transfers** — authenticated user → another user (same currency)
2. **Crypto deposit webhook** — HMAC-verified, at-most-once credit
3. **Withdrawals** — lock funds, mock gateway via Celery, settle or unlock

## Stack

| Layer | Choice |
| --- | --- |
| Language | Python 3.12 |
| Web | Django + Django REST Framework |
| DB | PostgreSQL |
| Jobs | Celery + Redis |
| Tests | pytest + pytest-django |
| DX | Docker Compose |

## Quick start

```bash
cp .env.example .env   # set DJANGO_SECRET_KEY and DB password
docker compose up --build
```

Services:

| Service | Role | Port |
| --- | --- | --- |
| `web` | Django API | `8000` |
| `db` | PostgreSQL | `5432` |
| `redis` | Celery broker/backend | `6379` |
| `worker` | Celery worker | — |

API base: `http://localhost:8000/api/`

Interactive API docs (Swagger UI): [http://localhost:8000/api/docs/](http://localhost:8000/api/docs/)  
OpenAPI schema: `/api/schema/` · ReDoc: `/api/redoc/`

In Swagger, use **Authorize** with `Token <your-key>` (include the word `Token`).

## Auth

DRF **TokenAuthentication**.

```bash
# Obtain a token
curl -X POST http://localhost:8000/api/auth/token/ \
  -H "Content-Type: application/json" \
  -d '{"username":"YOUR_USER","password":"YOUR_PASSWORD"}'

# Authenticated requests
curl http://localhost:8000/api/... \
  -H "Authorization: Token YOUR_TOKEN"
```

Create users via Django admin or `createsuperuser` inside the web container:

```bash
docker compose exec web python manage.py createsuperuser
```

Deposit webhooks do **not** use tokens: they verify `X-Signature` (HMAC-SHA256 of the raw body) with `DEPOSIT_WEBHOOK_SECRET`.

## Domain (high level)

- **Wallet** — one per user per currency (`USD`, `EUR`, `BTC`); `available_balance` + `locked_balance` (`Decimal`)
- **Transaction** — `deposit` / `withdrawal` / `transfer` with explicit status transitions
- **LedgerEntry** — append-only audit of every balance change
- **IdempotencyKey** — stores original responses for client retries (transfers)

## API map

| Endpoint | Auth | Notes |
| --- | --- | --- |
| `GET /api/docs/` | — | Swagger UI |
| `GET /api/schema/` | — | OpenAPI 3 schema |
| `GET /api/redoc/` | — | ReDoc |
| `POST /api/auth/token/` | — | Token obtain |
| `POST /api/transfers/` | Token | Internal transfer; optional `Idempotency-Key` |
| `POST /api/webhooks/deposit/` | HMAC | Deposit events keyed by `external_id` |
| `POST /api/withdrawals/` | Token | Lock → Celery gateway → settle/unlock |

### Example: transfer

```bash
curl -X POST http://localhost:8000/api/transfers/ \
  -H "Authorization: Token YOUR_TOKEN" \
  -H "Idempotency-Key: retry-safe-1" \
  -H "Content-Type: application/json" \
  -d '{"to_user_id":2,"amount":"10.00","currency":"USD"}'
```

### Example: deposit webhook

```bash
BODY='{"external_id":"evt-1","user_id":1,"amount":"0.5","currency":"BTC","status":"done"}'
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$DEPOSIT_WEBHOOK_SECRET" | awk '{print $2}')
curl -X POST http://localhost:8000/api/webhooks/deposit/ \
  -H "Content-Type: application/json" \
  -H "X-Signature: $SIG" \
  -d "$BODY"
```

### Example: withdrawal

```bash
curl -X POST http://localhost:8000/api/withdrawals/ \
  -H "Authorization: Token YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"amount":"25.00","currency":"USD"}'
```

## Tests

```bash
docker compose exec web pytest
```

Includes transfer concurrency (overdraft safety + opposing A↔B), deposit signature/idempotency/transitions, and withdrawal lock/settle/retry idempotency.

## Project docs

- [`docs/BACKLOG.md`](docs/BACKLOG.md) — phased task checklist
- [`AGENTS.md`](AGENTS.md) — architecture map, conventions, ask-before-scope

## Design decisions

### Money and ledger

- All money uses `Decimal` (never `float`), with DB check constraints `available_balance >= 0` and `locked_balance >= 0`.
- Every balance mutation writes an append-only **LedgerEntry** (`debit` / `credit`) with `balance_after` and `locked_balance_after`.
- Ledger rows refuse update/delete in application code; treat the table as an audit log.

### Concurrency

- Critical sections use `transaction.atomic()` + `select_for_update()`.
- Transfers lock **both** wallets in ascending primary-key order so A→B and B→A cannot deadlock.
- Balance checks happen **after** locks are held, so concurrent debits cannot drive available below zero.
- Withdrawal settlement locks the transaction row with `select_for_update(of=("self",))` to avoid Postgres “FOR UPDATE on nullable outer join” issues with nullable FKs.

### Idempotency

| Flow | Key | Behavior |
| --- | --- | --- |
| Transfer | `Idempotency-Key` + user | Claim key **before** moving funds; retries return stored HTTP payload/status |
| Deposit webhook | `external_id` | At most one credit; duplicates / late `pending` after `done` are no-ops |
| Withdrawal task | Transaction id + status guards | Re-running after crash is a no-op once `done`/`failed`; gateway is keyed by `withdrawal:<id>` |

### Status machines

- Deposits: only `pending → done|failed`; finals immutable (`409` on illegal transitions).
- Withdrawals: `pending → processing → done|failed` (lock first, then gateway, then settle/unlock).
- Transfers: created as `done` on successful synchronous settlement.

### Layering

- Thin DRF views/serializers for HTTP concerns.
- Business rules live in `services/` (transfers, deposits, withdrawals, gateway).
- Celery tasks in `tasks/` call services; they do not embed ledger logic.

### Configuration

- Secrets and connection settings come from the environment (`.env` gitignored; `.env.example` committed).
- Compose overrides DB/Redis hostnames for the container network.

## Trade-offs

| Choice | Why | Cost |
| --- | --- | --- |
| Row locks over optimistic versioning | Simpler correctness story for a take-home; easy to reason about under pytest threads | Lower write throughput under extreme contention |
| Synchronous transfer path | Immediate 201 + ledger in one request | Long-held DB locks if we later add slow side effects |
| In-process mock gateway | Deterministic tests without external deps | Not a real PSP; production would swap the client |
| Auto-create wallet on deposit | Gateway events can credit users before app pre-provisioning | Transfers/withdrawals still require an existing wallet |
| Token auth (not JWT/OAuth) | Matches brief defaults; minimal setup | No refresh/rotation story |
| SQLite fallback when `POSTGRES_HOST` unset | Convenient host-side `manage.py` checks | Concurrency tests require Postgres (Compose) |

## Future improvements

- Real payment gateway adapter + outbox / webhook reconciliation
- Withdrawal `Idempotency-Key` at the HTTP layer (today idempotency is task/settlement-focused)
- Stronger ledger schema (per-entry `available_delta` / `locked_delta`, hash-chaining)
- Observability: structured logs, metrics for lock wait / retry / imbalance alerts
- Horizontal scaling notes: statement timeouts, `SKIP LOCKED` for workers, read replicas for reporting
- FX / cross-currency transfers (out of scope today)

## Layout

```
fxpay/           # Django project (settings, urls, celery)
apps/            # wallets, ledger, transactions
services/        # business logic
tasks/           # Celery tasks
api/             # HTTP layer under /api/
tests/           # pytest suite
docs/BACKLOG.md
```
