"""Withdrawal lock / settle / unlock business logic."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.ledger.models import LedgerEntry, LedgerEntryType
from apps.transactions.models import Transaction, TransactionStatus, TransactionType
from apps.wallets.models import ZERO, Wallet

User = get_user_model()


class WithdrawalError(Exception):
    """Base withdrawal error."""


class WithdrawalValidationError(WithdrawalError):
    """Invalid request / missing wallet / bad amount."""


class InsufficientFunds(WithdrawalError):
    """Not enough available balance to lock."""


def _serialize_withdrawal(tx: Transaction) -> dict[str, Any]:
    return {
        "id": tx.id,
        "type": tx.type,
        "status": tx.status,
        "amount": format(tx.amount, "f"),
        "currency": tx.currency,
        "from_wallet_id": tx.from_wallet_id,
        "failure_reason": tx.failure_reason,
        "created_at": tx.created_at.isoformat().replace("+00:00", "Z"),
    }


def request_withdrawal(
    *,
    user: User,
    amount: Decimal,
    currency: str,
) -> Transaction:
    """
    Lock `amount` from available → locked and create a PENDING withdrawal.

    Caller is responsible for enqueueing the Celery settlement task.
    """
    if amount <= ZERO:
        raise WithdrawalValidationError("Amount must be positive.")

    try:
        wallet = Wallet.objects.get(user=user, currency=currency)
    except Wallet.DoesNotExist as exc:
        raise WithdrawalValidationError(
            "Wallet not found for this currency."
        ) from exc

    with transaction.atomic():
        wallet = Wallet.objects.select_for_update().get(pk=wallet.pk)
        if wallet.available_balance < amount:
            raise InsufficientFunds("Insufficient available balance.")

        wallet.available_balance -= amount
        wallet.locked_balance += amount
        wallet.save(
            update_fields=["available_balance", "locked_balance", "updated_at"]
        )

        tx = Transaction.objects.create(
            type=TransactionType.WITHDRAWAL,
            status=TransactionStatus.PENDING,
            amount=amount,
            currency=currency,
            from_wallet=wallet,
            initiated_by=user,
        )

        LedgerEntry.objects.create(
            wallet=wallet,
            transaction=tx,
            entry_type=LedgerEntryType.DEBIT,
            amount=amount,
            balance_after=wallet.available_balance,
            locked_balance_after=wallet.locked_balance,
        )
        return tx


def mark_withdrawal_processing(transaction_id: int) -> Transaction | None:
    """
    Transition PENDING → PROCESSING if needed.

    Returns None when the withdrawal is already in a final state (idempotent no-op).
    """
    with transaction.atomic():
        # of=("self",) avoids FOR UPDATE on nullable from_wallet outer join.
        tx = Transaction.objects.select_for_update(of=("self",)).get(
            pk=transaction_id, type=TransactionType.WITHDRAWAL
        )
        if tx.status in {TransactionStatus.DONE, TransactionStatus.FAILED}:
            return None
        if tx.status == TransactionStatus.PENDING:
            try:
                tx.transition_to(TransactionStatus.PROCESSING)
            except ValidationError as exc:
                raise WithdrawalValidationError(str(exc)) from exc
            tx.save(update_fields=["status", "updated_at"])
        return tx


def settle_withdrawal_success(transaction_id: int) -> Transaction:
    """Deduct locked funds after gateway success. Idempotent if already DONE."""
    with transaction.atomic():
        tx = Transaction.objects.select_for_update(of=("self",)).get(
            pk=transaction_id, type=TransactionType.WITHDRAWAL
        )
        if tx.status == TransactionStatus.DONE:
            return tx
        if tx.status == TransactionStatus.FAILED:
            raise WithdrawalValidationError(
                "Cannot settle a failed withdrawal as success."
            )
        if tx.status == TransactionStatus.PENDING:
            tx.transition_to(TransactionStatus.PROCESSING)

        wallet = Wallet.objects.select_for_update().get(pk=tx.from_wallet_id)
        if wallet.locked_balance < tx.amount:
            raise WithdrawalValidationError("Locked balance too low to settle.")

        wallet.locked_balance -= tx.amount
        wallet.save(update_fields=["locked_balance", "updated_at"])

        try:
            tx.transition_to(TransactionStatus.DONE)
        except ValidationError as exc:
            raise WithdrawalValidationError(str(exc)) from exc
        tx.failure_reason = ""
        tx.save(update_fields=["status", "failure_reason", "updated_at"])

        LedgerEntry.objects.create(
            wallet=wallet,
            transaction=tx,
            entry_type=LedgerEntryType.DEBIT,
            amount=tx.amount,
            balance_after=wallet.available_balance,
            locked_balance_after=wallet.locked_balance,
        )
        return tx


def settle_withdrawal_failure(
    transaction_id: int,
    *,
    reason: str = "gateway_failed",
) -> Transaction:
    """Unlock funds back to available after ultimate gateway failure. Idempotent."""
    with transaction.atomic():
        tx = Transaction.objects.select_for_update(of=("self",)).get(
            pk=transaction_id, type=TransactionType.WITHDRAWAL
        )
        if tx.status == TransactionStatus.FAILED:
            return tx
        if tx.status == TransactionStatus.DONE:
            raise WithdrawalValidationError(
                "Cannot fail a completed withdrawal."
            )
        if tx.status == TransactionStatus.PENDING:
            tx.transition_to(TransactionStatus.PROCESSING)

        wallet = Wallet.objects.select_for_update().get(pk=tx.from_wallet_id)
        if wallet.locked_balance < tx.amount:
            raise WithdrawalValidationError("Locked balance too low to unlock.")

        wallet.locked_balance -= tx.amount
        wallet.available_balance += tx.amount
        wallet.save(
            update_fields=["available_balance", "locked_balance", "updated_at"]
        )

        try:
            tx.transition_to(TransactionStatus.FAILED)
        except ValidationError as exc:
            raise WithdrawalValidationError(str(exc)) from exc
        tx.failure_reason = reason
        tx.save(update_fields=["status", "failure_reason", "updated_at"])

        LedgerEntry.objects.create(
            wallet=wallet,
            transaction=tx,
            entry_type=LedgerEntryType.CREDIT,
            amount=tx.amount,
            balance_after=wallet.available_balance,
            locked_balance_after=wallet.locked_balance,
        )
        return tx


def serialize_withdrawal(tx: Transaction) -> dict[str, Any]:
    return _serialize_withdrawal(tx)
