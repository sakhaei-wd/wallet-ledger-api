"""Transfer business logic: locking, balance checks, ledger writes, idempotency."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from apps.ledger.models import LedgerEntry, LedgerEntryType
from apps.transactions.models import (
    IdempotencyKey,
    Transaction,
    TransactionStatus,
    TransactionType,
)
from apps.wallets.models import ZERO, Wallet

User = get_user_model()

# Sentinel while the first request holds the idempotency row and runs the transfer.
IDEMPOTENCY_PENDING_STATUS = 0


class TransferError(Exception):
    """Base error for transfer failures mapped by the API layer."""


class TransferValidationError(TransferError):
    """Business-rule validation failure (e.g. missing wallet, currency mismatch)."""


class InsufficientFunds(TransferError):
    """Sender available balance is too low."""


@dataclass(frozen=True)
class TransferAPIResult:
    """Outcome of an HTTP-facing transfer attempt (fresh or idempotent replay)."""

    status_code: int
    body: dict[str, Any]
    transaction: Transaction | None = None
    replayed: bool = False


def create_transfer(
    *,
    sender: User,
    to_user_id: int,
    amount: Decimal,
    currency: str,
) -> Transaction:
    """
    Move `amount` of `currency` from `sender`'s wallet to `to_user_id`'s wallet.

    - Runs in a single DB transaction with `select_for_update` on both wallets.
    - Wallets are locked in ascending primary-key order (deadlock-safe for A↔B).
    - Rejects the transfer if available balance would go negative.
    - On success creates exactly two ledger entries (debit + credit).
    """
    if amount <= ZERO:
        raise TransferValidationError("Amount must be positive.")

    try:
        recipient = User.objects.get(pk=to_user_id)
    except User.DoesNotExist as exc:
        raise TransferValidationError("Recipient user does not exist.") from exc

    if recipient.pk == sender.pk:
        raise TransferValidationError("Cannot transfer to yourself.")

    try:
        sender_wallet = Wallet.objects.get(user=sender, currency=currency)
        recipient_wallet = Wallet.objects.get(user=recipient, currency=currency)
    except Wallet.DoesNotExist as exc:
        raise TransferValidationError(
            "Both users must have a wallet in this currency."
        ) from exc

    if sender_wallet.pk == recipient_wallet.pk:
        raise TransferValidationError("Transfer wallets must differ.")

    with transaction.atomic():
        # Deterministic lock order prevents A→B / B→A deadlocks.
        locked_wallets = list(
            Wallet.objects.select_for_update()
            .filter(pk__in=[sender_wallet.pk, recipient_wallet.pk])
            .order_by("pk")
        )
        if len(locked_wallets) != 2:
            raise TransferValidationError("One or both wallets no longer exist.")

        locked_by_id = {wallet.pk: wallet for wallet in locked_wallets}
        sender_wallet = locked_by_id[sender_wallet.pk]
        recipient_wallet = locked_by_id[recipient_wallet.pk]

        if sender_wallet.available_balance < amount:
            raise InsufficientFunds("Insufficient available balance.")

        sender_wallet.available_balance -= amount
        recipient_wallet.available_balance += amount
        sender_wallet.save(update_fields=["available_balance", "updated_at"])
        recipient_wallet.save(update_fields=["available_balance", "updated_at"])

        tx = Transaction.objects.create(
            type=TransactionType.TRANSFER,
            status=TransactionStatus.DONE,
            amount=amount,
            currency=currency,
            from_wallet=sender_wallet,
            to_wallet=recipient_wallet,
            initiated_by=sender,
        )

        LedgerEntry.objects.create(
            wallet=sender_wallet,
            transaction=tx,
            entry_type=LedgerEntryType.DEBIT,
            amount=amount,
            balance_after=sender_wallet.available_balance,
            locked_balance_after=sender_wallet.locked_balance,
        )
        LedgerEntry.objects.create(
            wallet=recipient_wallet,
            transaction=tx,
            entry_type=LedgerEntryType.CREDIT,
            amount=amount,
            balance_after=recipient_wallet.available_balance,
            locked_balance_after=recipient_wallet.locked_balance,
        )

        return tx


def _serialize_transaction(tx: Transaction) -> dict[str, Any]:
    # Keep payload JSON-stable for IdempotencyKey.response_body (no Decimal).
    return {
        "id": tx.id,
        "type": tx.type,
        "status": tx.status,
        "amount": f"{tx.amount:f}",
        "currency": tx.currency,
        "from_wallet_id": tx.from_wallet_id,
        "to_wallet_id": tx.to_wallet_id,
        "created_at": tx.created_at.isoformat().replace("+00:00", "Z"),
    }


def execute_transfer_request(
    *,
    sender: User,
    to_user_id: int,
    amount: Decimal,
    currency: str,
    idempotency_key: str | None = None,
    request_path: str = "/api/transfers/",
) -> TransferAPIResult:
    """
    Run a transfer for the API layer, optionally under an Idempotency-Key.

    Claims the key before moving funds so concurrent retries cannot double-transfer.
    """
    key = (idempotency_key or "").strip() or None

    if key is None:
        try:
            tx = create_transfer(
                sender=sender,
                to_user_id=to_user_id,
                amount=amount,
                currency=currency,
            )
        except InsufficientFunds as exc:
            return TransferAPIResult(status_code=400, body={"detail": str(exc)})
        except TransferValidationError as exc:
            return TransferAPIResult(status_code=400, body={"detail": str(exc)})
        except TransferError as exc:
            return TransferAPIResult(status_code=400, body={"detail": str(exc)})
        return TransferAPIResult(
            status_code=201,
            body=_serialize_transaction(tx),
            transaction=tx,
        )

    with transaction.atomic():
        existing = (
            IdempotencyKey.objects.select_for_update()
            .filter(user=sender, key=key)
            .first()
        )
        if existing is not None and existing.response_status_code != IDEMPOTENCY_PENDING_STATUS:
            return TransferAPIResult(
                status_code=existing.response_status_code,
                body=existing.response_body,
                transaction=existing.transaction,
                replayed=True,
            )

        if existing is None:
            try:
                with transaction.atomic():
                    IdempotencyKey.objects.create(
                        user=sender,
                        key=key,
                        request_path=request_path,
                        response_status_code=IDEMPOTENCY_PENDING_STATUS,
                        response_body={},
                    )
            except IntegrityError:
                existing = (
                    IdempotencyKey.objects.select_for_update()
                    .filter(user=sender, key=key)
                    .get()
                )
                if existing.response_status_code != IDEMPOTENCY_PENDING_STATUS:
                    return TransferAPIResult(
                        status_code=existing.response_status_code,
                        body=existing.response_body,
                        transaction=existing.transaction,
                        replayed=True,
                    )

        record = IdempotencyKey.objects.select_for_update().get(user=sender, key=key)

        try:
            tx = create_transfer(
                sender=sender,
                to_user_id=to_user_id,
                amount=amount,
                currency=currency,
            )
        except InsufficientFunds as exc:
            body = {"detail": str(exc)}
            record.response_status_code = 400
            record.response_body = body
            record.save(
                update_fields=["response_status_code", "response_body"]
            )
            return TransferAPIResult(status_code=400, body=body)
        except TransferValidationError as exc:
            body = {"detail": str(exc)}
            record.response_status_code = 400
            record.response_body = body
            record.save(
                update_fields=["response_status_code", "response_body"]
            )
            return TransferAPIResult(status_code=400, body=body)
        except TransferError as exc:
            body = {"detail": str(exc)}
            record.response_status_code = 400
            record.response_body = body
            record.save(
                update_fields=["response_status_code", "response_body"]
            )
            return TransferAPIResult(status_code=400, body=body)

        body = _serialize_transaction(tx)
        record.response_status_code = 201
        record.response_body = body
        record.transaction = tx
        record.save(
            update_fields=["response_status_code", "response_body", "transaction"]
        )
        return TransferAPIResult(status_code=201, body=body, transaction=tx)
