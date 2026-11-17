"""Withdrawal API + settlement tests (lock/unlock, retries, idempotency)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.test import override_settings

from apps.ledger.models import LedgerEntry
from apps.transactions.models import Transaction, TransactionStatus, TransactionType
from apps.wallets.models import Currency, Wallet
from services.gateway import withdrawal_gateway
from services.withdrawals import (
    request_withdrawal,
    settle_withdrawal_failure,
    settle_withdrawal_success,
)
from tasks.withdrawals import process_withdrawal

WITHDRAW_URL = "/api/withdrawals/"


@pytest.fixture(autouse=True)
def _reset_gateway():
    withdrawal_gateway.reset()
    yield
    withdrawal_gateway.reset()


@pytest.fixture
def funded_wallet(user_a):
    return Wallet.objects.create(
        user=user_a,
        currency=Currency.USD,
        available_balance=Decimal("100.00"),
        locked_balance=Decimal("0.00"),
    )


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=True,
)
@pytest.mark.django_db
def test_withdrawal_success_locks_then_settles(
    auth_client_a, user_a, funded_wallet
):
    response = auth_client_a.post(
        WITHDRAW_URL,
        {"amount": "40.00", "currency": "USD"},
        format="json",
    )
    assert response.status_code == 201
    # Eager task runs before response returns from delay(); status may already be done.
    tx = Transaction.objects.get(pk=response.data["id"])
    assert tx.type == TransactionType.WITHDRAWAL
    assert tx.status == TransactionStatus.DONE

    funded_wallet.refresh_from_db()
    assert funded_wallet.available_balance == Decimal("60.00")
    assert funded_wallet.locked_balance == Decimal("0.00")
    # lock debit + settle debit
    assert LedgerEntry.objects.filter(transaction=tx).count() == 2
    assert len(withdrawal_gateway.calls) == 1


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=True,
)
@pytest.mark.django_db
def test_withdrawal_permanent_failure_unlocks(
    auth_client_a, funded_wallet
):
    withdrawal_gateway.mode = "permanent_fail"
    response = auth_client_a.post(
        WITHDRAW_URL,
        {"amount": "25.00", "currency": "USD"},
        format="json",
    )
    assert response.status_code == 201
    tx = Transaction.objects.get(pk=response.data["id"])
    assert tx.status == TransactionStatus.FAILED

    funded_wallet.refresh_from_db()
    assert funded_wallet.available_balance == Decimal("100.00")
    assert funded_wallet.locked_balance == Decimal("0.00")
    # lock debit + unlock credit
    assert LedgerEntry.objects.filter(transaction=tx).count() == 2


@pytest.mark.django_db
def test_withdrawal_retries_then_succeeds(user_a, funded_wallet):
    """Simulate Celery retries: temporary gateway errors then success."""
    from services.gateway import GatewayTemporaryError
    from services.withdrawals import mark_withdrawal_processing

    withdrawal_gateway.mode = "fail_then_success"
    withdrawal_gateway.fail_times = 2

    tx = request_withdrawal(
        user=user_a, amount=Decimal("10.00"), currency=Currency.USD
    )

    # Eager Celery raises Retry instead of re-running; mirror worker retries here.
    assert process_withdrawal.autoretry_for == (GatewayTemporaryError,)
    assert process_withdrawal.retry_backoff is True

    last_error = None
    for _ in range(5):
        mark_withdrawal_processing(tx.id)
        try:
            withdrawal_gateway.withdraw(
                reference=f"withdrawal:{tx.id}",
                amount=tx.amount,
                currency=tx.currency,
            )
            settle_withdrawal_success(tx.id)
            last_error = None
            break
        except GatewayTemporaryError as exc:
            last_error = exc
            continue
    assert last_error is None

    funded_wallet.refresh_from_db()
    assert funded_wallet.available_balance == Decimal("90.00")
    assert funded_wallet.locked_balance == Decimal("0.00")
    assert Transaction.objects.get(pk=tx.id).status == TransactionStatus.DONE
    # 2 temporary failures + 1 success
    assert len(withdrawal_gateway.calls) == 3


@pytest.mark.django_db
def test_settle_success_idempotent(user_a, funded_wallet):
    tx = request_withdrawal(
        user=user_a, amount=Decimal("15.00"), currency=Currency.USD
    )
    settle_withdrawal_success(tx.id)
    settle_withdrawal_success(tx.id)

    funded_wallet.refresh_from_db()
    assert funded_wallet.available_balance == Decimal("85.00")
    assert funded_wallet.locked_balance == Decimal("0.00")
    assert Transaction.objects.get(pk=tx.id).status == TransactionStatus.DONE
    # lock + one settle (second settle no-op)
    assert LedgerEntry.objects.filter(transaction_id=tx.id).count() == 2


@pytest.mark.django_db
def test_settle_failure_idempotent(user_a, funded_wallet):
    tx = request_withdrawal(
        user=user_a, amount=Decimal("15.00"), currency=Currency.USD
    )
    settle_withdrawal_failure(tx.id, reason="rejected")
    settle_withdrawal_failure(tx.id, reason="rejected-again")

    funded_wallet.refresh_from_db()
    assert funded_wallet.available_balance == Decimal("100.00")
    assert funded_wallet.locked_balance == Decimal("0.00")
    tx.refresh_from_db()
    assert tx.status == TransactionStatus.FAILED
    assert tx.failure_reason == "rejected"
    assert LedgerEntry.objects.filter(transaction_id=tx.id).count() == 2


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=True,
)
@pytest.mark.django_db
def test_task_reexecution_after_success_does_not_double_settle(
    user_a, funded_wallet
):
    tx = request_withdrawal(
        user=user_a, amount=Decimal("20.00"), currency=Currency.USD
    )
    process_withdrawal.delay(tx.id)
    process_withdrawal.delay(tx.id)

    funded_wallet.refresh_from_db()
    assert funded_wallet.available_balance == Decimal("80.00")
    assert funded_wallet.locked_balance == Decimal("0.00")
    assert Transaction.objects.get(pk=tx.id).status == TransactionStatus.DONE
    assert LedgerEntry.objects.filter(transaction_id=tx.id).count() == 2


@pytest.mark.django_db
def test_withdrawal_insufficient_funds(auth_client_a, funded_wallet):
    response = auth_client_a.post(
        WITHDRAW_URL,
        {"amount": "500.00", "currency": "USD"},
        format="json",
    )
    assert response.status_code == 400
    funded_wallet.refresh_from_db()
    assert funded_wallet.available_balance == Decimal("100.00")
    assert Transaction.objects.count() == 0
