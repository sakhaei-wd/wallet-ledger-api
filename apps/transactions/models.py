from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

from apps.wallets.models import MONEY_DECIMAL_PLACES, MONEY_MAX_DIGITS, Currency, ZERO


class TransactionType(models.TextChoices):
    DEPOSIT = "deposit", "Deposit"
    WITHDRAWAL = "withdrawal", "Withdrawal"
    TRANSFER = "transfer", "Transfer"


class TransactionStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PROCESSING = "processing", "Processing"
    DONE = "done", "Done"
    FAILED = "failed", "Failed"


# Type-aware allowed transitions. Final states are immutable.
_STATUS_TRANSITIONS: dict[str, dict[str, set[str]]] = {
    TransactionType.DEPOSIT: {
        TransactionStatus.PENDING: {TransactionStatus.DONE, TransactionStatus.FAILED},
        TransactionStatus.DONE: set(),
        TransactionStatus.FAILED: set(),
    },
    TransactionType.WITHDRAWAL: {
        TransactionStatus.PENDING: {
            TransactionStatus.PROCESSING,
            TransactionStatus.DONE,
            TransactionStatus.FAILED,
        },
        TransactionStatus.PROCESSING: {
            TransactionStatus.DONE,
            TransactionStatus.FAILED,
        },
        TransactionStatus.DONE: set(),
        TransactionStatus.FAILED: set(),
    },
    TransactionType.TRANSFER: {
        TransactionStatus.PENDING: {TransactionStatus.DONE, TransactionStatus.FAILED},
        TransactionStatus.DONE: set(),
        TransactionStatus.FAILED: set(),
    },
}


class Transaction(models.Model):
    """Financial operation record (deposit, withdrawal, or transfer)."""

    type = models.CharField(max_length=16, choices=TransactionType.choices)
    status = models.CharField(
        max_length=16,
        choices=TransactionStatus.choices,
        default=TransactionStatus.PENDING,
    )
    amount = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
        validators=[MinValueValidator(Decimal("0.00000001"))],
    )
    currency = models.CharField(max_length=8, choices=Currency.choices)
    # Deposit: to_wallet set. Withdrawal: from_wallet set. Transfer: both set.
    from_wallet = models.ForeignKey(
        "wallets.Wallet",
        on_delete=models.PROTECT,
        related_name="outgoing_transactions",
        null=True,
        blank=True,
    )
    to_wallet = models.ForeignKey(
        "wallets.Wallet",
        on_delete=models.PROTECT,
        related_name="incoming_transactions",
        null=True,
        blank=True,
    )
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="transactions",
        null=True,
        blank=True,
    )
    # Gateway / webhook event id — at-most-once processing key when present.
    external_id = models.CharField(max_length=255, null=True, blank=True)
    failure_reason = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["external_id"],
                condition=models.Q(external_id__isnull=False),
                name="uniq_transaction_external_id",
            ),
        ]
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["type", "status"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self) -> str:
        return f"Transaction({self.type}, {self.status}, {self.amount} {self.currency})"

    def clean(self) -> None:
        super().clean()
        if self.amount is not None and self.amount <= ZERO:
            raise ValidationError({"amount": "Amount must be positive."})

        if self.type == TransactionType.TRANSFER:
            if not self.from_wallet_id or not self.to_wallet_id:
                raise ValidationError("Transfer requires from_wallet and to_wallet.")
            if self.from_wallet_id == self.to_wallet_id:
                raise ValidationError("Transfer wallets must differ.")
        elif self.type == TransactionType.DEPOSIT:
            if not self.to_wallet_id:
                raise ValidationError("Deposit requires to_wallet.")
        elif self.type == TransactionType.WITHDRAWAL:
            if not self.from_wallet_id:
                raise ValidationError("Withdrawal requires from_wallet.")

    def transition_to(self, new_status: str) -> None:
        """Apply an allowed status change; reject illegal or final-state mutations."""
        allowed = _STATUS_TRANSITIONS.get(self.type, {}).get(self.status, set())
        if new_status not in allowed:
            raise ValidationError(
                f"Illegal status transition for {self.type}: "
                f"{self.status} → {new_status}."
            )
        self.status = new_status


class IdempotencyKey(models.Model):
    """Stores original API response for client retries (e.g. transfers)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="idempotency_keys",
    )
    key = models.CharField(max_length=255)
    request_path = models.CharField(max_length=255, default="/api/transfers/")
    response_status_code = models.PositiveSmallIntegerField()
    response_body = models.JSONField()
    transaction = models.ForeignKey(
        Transaction,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="idempotency_keys",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "key"],
                name="uniq_idempotency_user_key",
            ),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"IdempotencyKey(user={self.user_id}, key={self.key!r})"
