# FXPay — Implementation Backlog

Phased task list for the Wallet & Transaction Service. Work **one task id at a time**. Mark items `[x]` only when that task is done.

## Phase 1 — Foundation

- [x] **P1-01**: Create `docs/BACKLOG.md` + `AGENTS.md` (architecture map, conventions, how to run/test, ask-before-scope)
- [x] **P1-02**: Scaffold Django project + app layout
- [x] **P1-03**: Docker Compose (web, postgres, redis, celery worker) so `docker compose up` works
- [ ] **P1-04**: Env-based settings; no secrets committed
- [ ] **P1-05**: Models + migrations (User/auth, Wallet, Transaction, LedgerEntry)
- [ ] **P1-06**: API auth setup (DRF Token unless overridden)
- [ ] **P1-07**: README skeleton (fill deeper design notes in P5-01)

## Phase 2 — Transfers

- [ ] **P2-01**: Transfer serializers / views / URLs (`POST /api/transfers/`)
- [ ] **P2-02**: Transfer service (locking, no negative balances, exactly 2 ledger entries)
- [ ] **P2-03**: `Idempotency-Key` handling
- [ ] **P2-04**: Deadlock-safe lock ordering (A→B and B→A)
- [ ] **P2-05**: Pytest for transfers, including at least one concurrency test

## Phase 3 — Deposit webhook

- [ ] **P3-01**: HMAC-SHA256 request signature verification
- [ ] **P3-02**: Idempotent deposit processing by external event/transaction id
- [ ] **P3-03**: Enforce status transitions `PENDING → DONE|FAILED`; final states immutable
- [ ] **P3-04**: Pytest for signature, duplicates, illegal transitions, out-of-order delivery

## Phase 4 — Withdrawals (bonus)

- [ ] **P4-01**: Withdrawal API + move funds available → locked
- [ ] **P4-02**: Celery task + mock gateway client + exponential backoff retries
- [ ] **P4-03**: Success/failure settlement + ledger entries
- [ ] **P4-04**: Task re-execution idempotency (no double processing)
- [ ] **P4-05**: Pytest for lock/unlock/success/failure/idempotent retry

## Phase 5 — Delivery

- [ ] **P5-01**: README design decisions, trade-offs, future improvements
- [ ] **P5-02**: Final backlog pass + smoke check

## Deliverables checklist

- [ ] Git repo with meaningful commit history (when user requests commits)
- [ ] Entire app runnable via `docker compose up`
- [ ] pytest suite including Part 1 concurrency test
- [ ] README with key decisions, trade-offs, future work
- [ ] Phased backlog + agent docs
- [ ] Parts 1 & 2 complete; Part 3 complete unless user skips it
