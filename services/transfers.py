"""Transfer business logic (locking, ledger writes). Core logic lands in P2-02."""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model

from apps.transactions.models import Transaction

User = get_user_model()


class TransferError(Exception):
    """Base error for transfer failures mapped by the API layer."""


class TransferValidationError(TransferError):
    """Business-rule validation failure (e.g. missing wallet, currency mismatch)."""


class InsufficientFunds(TransferError):
    """Sender available balance is too low."""


def create_transfer(
    *,
    sender: User,
    to_user_id: int,
    amount: Decimal,
    currency: str,
) -> Transaction:
    """
    Move `amount` of `currency` from `sender`'s wallet to `to_user_id`'s wallet.

    Implemented in P2-02 (row locks, ledger entries, no negative balances).
    """
    raise NotImplementedError("Transfer service is implemented in P2-02.")
