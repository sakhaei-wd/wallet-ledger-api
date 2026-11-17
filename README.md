# FXPay — Wallet & Transaction Service

Django + DRF backend that maintains per-user, per-currency wallet balances and implements concurrency-safe, idempotent, ledger-traceable financial operations:

1. **Internal transfers** — authenticated user → another user (same currency)
2. **Crypto deposit webhook** — HMAC-verified, at-most-once credit
3. **Withdrawals** — lock funds, mock gateway via Celery, settle or unlock

> Deeper design decisions, trade-offs, and future work: see **P5-01** (to be expanded here).

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

## Domain (high level)

- **Wallet** — one per user per currency (`USD`, `EUR`, `BTC`); `available_balance` + `locked_balance` (`Decimal`)
- **Transaction** — `deposit` / `withdrawal` / `transfer` with explicit status transitions
- **LedgerEntry** — append-only audit of every balance change
- **IdempotencyKey** — stores original responses for client retries (transfers)

## API map (phased)

| Endpoint | Phase | Notes |
| --- | --- | --- |
| `POST /api/auth/token/` | 1 | Token obtain |
| `POST /api/transfers/` | 2 | Internal transfer + `Idempotency-Key` |
| `POST /api/webhooks/deposit/` | 3 | HMAC-SHA256 (`X-Signature`) deposit events by `external_id` |
| `POST /api/withdrawals/` | 4 | Lock → gateway → settle/unlock |

## Tests

```bash
docker compose exec web pytest
```

Transfer suite includes idempotency and concurrency tests (including opposing A↔B transfers).

## Project docs

- [`docs/BACKLOG.md`](docs/BACKLOG.md) — phased task checklist
- [`AGENTS.md`](AGENTS.md) — architecture map, conventions, ask-before-scope

## Design notes (P5-01)

_Placeholder — expand in P5-01:_

- Concurrency approach (`select_for_update`, lock ordering)
- Idempotency for transfers, webhooks, and withdrawal tasks
- Ledger immutability and trade-offs
- Future improvements
