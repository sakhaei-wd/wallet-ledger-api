"""Deposit webhook business logic: HMAC verify helpers, idempotent credit."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.ledger.models import LedgerEntry, LedgerEntryType
from apps.transactions.models import Transaction, TransactionStatus, TransactionType
from apps.wallets.models import ZERO, Wallet

User = get_user_model()


class DepositError(Exception):
    """Base deposit processing error."""


class DepositValidationError(DepositError):
    """Invalid payload / business rule failure."""


class IllegalDepositTransition(DepositError):
    """Attempt to mutate a final deposit status or illegal transition."""


@dataclass(frozen=True)
class DepositResult:
    status_code: int
    body: dict[str, Any]
    transaction: Transaction | None = None


def verify_deposit_signature(*, body: bytes, signature: str) -> bool:
    """Return True if `signature` is hex HMAC-SHA256 of `body` with the shared secret."""
    secret = settings.DEPOSIT_WEBHOOK_SECRET or ""
    if not secret or not signature:
        return False
    expected = hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature.strip().lower())


def _serialize_deposit(tx: Transaction) -> dict[str, Any]:
    return {
        "id": tx.id,
        "type": tx.type,
        "status": tx.status,
        "amount": format(tx.amount, "f"),
        "currency": tx.currency,
        "external_id": tx.external_id,
        "to_wallet_id": tx.to_wallet_id,
        "failure_reason": tx.failure_reason,
        "created_at": tx.created_at.isoformat().replace("+00:00", "Z"),
    }


def _get_or_create_wallet(*, user: User, currency: str) -> Wallet:
    wallet, _ = Wallet.objects.get_or_create(
        user=user,
        currency=currency,
        defaults={
            "available_balance": ZERO,
            "locked_balance": ZERO,
        },
    )
    return wallet


def _credit_wallet(*, wallet: Wallet, amount: Decimal, tx: Transaction) -> None:
    wallet.available_balance += amount
    wallet.save(update_fields=["available_balance", "updated_at"])
    LedgerEntry.objects.create(
        wallet=wallet,
        transaction=tx,
        entry_type=LedgerEntryType.CREDIT,
        amount=amount,
        balance_after=wallet.available_balance,
        locked_balance_after=wallet.locked_balance,
    )


def process_deposit_event(
    *,
    external_id: str,
    user_id: int,
    amount: Decimal,
    currency: str,
    status: str,
    failure_reason: str = "",
) -> DepositResult:
    """
    Apply a gateway deposit event idempotently by `external_id`.

    Allowed status transitions: PENDING → DONE | FAILED. Final states are immutable.
    Out-of-order DONE/FAILED (before PENDING) still creates the deposit once and settles.
    """
    external_id = (external_id or "").strip()
    if not external_id:
        raise DepositValidationError("external_id is required.")
    if amount <= ZERO:
        raise DepositValidationError("Amount must be positive.")

    status = (status or "").strip().lower()
    if status not in {
        TransactionStatus.PENDING,
        TransactionStatus.DONE,
        TransactionStatus.FAILED,
    }:
        raise DepositValidationError(
            "status must be pending, done, or failed."
        )

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist as exc:
        raise DepositValidationError("user_id does not exist.") from exc

    with transaction.atomic():
        tx = (
            Transaction.objects.select_for_update()
            .filter(external_id=external_id, type=TransactionType.DEPOSIT)
            .first()
        )

        if tx is None:
            wallet = _get_or_create_wallet(user=user, currency=currency)
            # Lock wallet before creating/settling to serialize balance updates.
            wallet = Wallet.objects.select_for_update().get(pk=wallet.pk)
            try:
                with transaction.atomic():
                    tx = Transaction.objects.create(
                        type=TransactionType.DEPOSIT,
                        status=TransactionStatus.PENDING,
                        amount=amount,
                        currency=currency,
                        to_wallet=wallet,
                        initiated_by=user,
                        external_id=external_id,
                    )
            except IntegrityError:
                tx = (
                    Transaction.objects.select_for_update()
                    .filter(external_id=external_id, type=TransactionType.DEPOSIT)
                    .get()
                )
                wallet = Wallet.objects.select_for_update().get(pk=tx.to_wallet_id)
        else:
            wallet = Wallet.objects.select_for_update().get(pk=tx.to_wallet_id)
            # Ignore conflicting amount/currency on retries; external_id is source of truth.
            if tx.currency != currency or tx.amount != amount:
                # Still return current state for idempotent retries with same external_id
                # when already final; for pending, keep original amounts.
                pass

        if status == TransactionStatus.PENDING:
            return DepositResult(status_code=200, body=_serialize_deposit(tx), transaction=tx)

        if status == TransactionStatus.DONE:
            if tx.status == TransactionStatus.DONE:
                return DepositResult(
                    status_code=200, body=_serialize_deposit(tx), transaction=tx
                )
            if tx.status == TransactionStatus.FAILED:
                raise IllegalDepositTransition(
                    "Cannot transition deposit from failed to done."
                )
            try:
                tx.transition_to(TransactionStatus.DONE)
            except ValidationError as exc:
                raise IllegalDepositTransition(str(exc)) from exc
            tx.failure_reason = ""
            tx.save(update_fields=["status", "failure_reason", "updated_at"])
            _credit_wallet(wallet=wallet, amount=tx.amount, tx=tx)
            return DepositResult(status_code=200, body=_serialize_deposit(tx), transaction=tx)

        # FAILED
        if tx.status == TransactionStatus.FAILED:
            return DepositResult(status_code=200, body=_serialize_deposit(tx), transaction=tx)
        if tx.status == TransactionStatus.DONE:
            raise IllegalDepositTransition(
                "Cannot transition deposit from done to failed."
            )
        try:
            tx.transition_to(TransactionStatus.FAILED)
        except ValidationError as exc:
            raise IllegalDepositTransition(str(exc)) from exc
        tx.failure_reason = failure_reason or "gateway_failed"
        tx.save(update_fields=["status", "failure_reason", "updated_at"])
        return DepositResult(status_code=200, body=_serialize_deposit(tx), transaction=tx)
