# FXPay — Implementation Backlog

Phased task list for the Wallet & Transaction Service. Work **one task id at a time**. Mark items `[x]` only when that task is done.

> **Grain note:** Phase 1 + early Phase 2 used fine-grained tasks. Remaining work is **batched** into larger slices so each turn/commit is more substantial. Older IDs that were merged are listed under each new task for traceability.

## Phase 1 — Foundation

- [x] **P1-01**: Create `docs/BACKLOG.md` + `AGENTS.md` (architecture map, conventions, how to run/test, ask-before-scope)
- [x] **P1-02**: Scaffold Django project + app layout
- [x] **P1-03**: Docker Compose (web, postgres, redis, celery worker) so `docker compose up` works
- [x] **P1-04**: Env-based settings; no secrets committed
- [x] **P1-05**: Models + migrations (User/auth, Wallet, Transaction, LedgerEntry)
- [x] **P1-06**: API auth setup (DRF Token unless overridden)
- [x] **P1-07**: README skeleton (fill deeper design notes in P5-01)

## Phase 2 — Transfers

- [x] **P2-01**: Transfer serializers / views / URLs (`POST /api/transfers/`)
- [x] **P2-02**: Transfer service (locking, no negative balances, exactly 2 ledger entries)
- [x] **P2-03**: Finish transfers — `Idempotency-Key`, confirm deadlock-safe lock ordering (A↔B), pytest suite including at least one concurrency test  
  _(merges former P2-03 + P2-04 + P2-05; lock ordering already started in P2-02 — verify/cover in tests)_

## Phase 3 — Deposit webhook

- [x] **P3-01**: Deposit webhook end-to-end — HMAC-SHA256 verification, idempotent processing by external id, `PENDING → DONE|FAILED` (finals immutable), pytest (signature, duplicates, illegal transitions, out-of-order)  
  _(merges former P3-01 … P3-04)_

## Phase 4 — Withdrawals (bonus)

- [ ] **P4-01**: Withdrawals end-to-end — API (available→locked), Celery mock gateway with exponential backoff, success/failure settlement + ledger, task re-execution idempotency, pytest  
  _(merges former P4-01 … P4-05)_

## Phase 5 — Delivery

- [ ] **P5-01**: Delivery wrap-up — README design decisions/trade-offs/future work, final backlog pass, smoke check (`docker compose up`, pytest)  
  _(merges former P5-01 + P5-02)_

## Deliverables checklist

- [ ] Git repo with meaningful commit history (when user requests commits)
- [ ] Entire app runnable via `docker compose up`
- [ ] pytest suite including Part 1 concurrency test
- [ ] README with key decisions, trade-offs, future work
- [ ] Phased backlog + agent docs
- [ ] Parts 1 & 2 complete; Part 3 complete unless user skips it
