from django.core.exceptions import ValidationError
from django.db import models

from apps.wallets.models import MONEY_DECIMAL_PLACES, MONEY_MAX_DIGITS, ZERO


class LedgerEntryType(models.TextChoices):
    DEBIT = "debit", "Debit"
    CREDIT = "credit", "Credit"


class LedgerEntry(models.Model):
    """Append-only balance change record. Immutable after insert."""

    wallet = models.ForeignKey(
        "wallets.Wallet",
        on_delete=models.PROTECT,
        related_name="ledger_entries",
    )
    transaction = models.ForeignKey(
        "transactions.Transaction",
        on_delete=models.PROTECT,
        related_name="ledger_entries",
    )
    entry_type = models.CharField(max_length=16, choices=LedgerEntryType.choices)
    amount = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
    )
    # Available balance on this wallet after the entry is applied.
    balance_after = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
    )
    # Locked balance after the entry (needed for withdrawal lock/unlock audit).
    locked_balance_after = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
        default=ZERO,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        verbose_name_plural = "ledger entries"
        indexes = [
            models.Index(fields=["wallet", "created_at"]),
            models.Index(fields=["transaction"]),
        ]

    def __str__(self) -> str:
        return (
            f"LedgerEntry({self.entry_type}, {self.amount}, "
            f"wallet={self.wallet_id}, tx={self.transaction_id})"
        )

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError("Ledger entries are immutable and cannot be updated.")
        if self.amount is not None and self.amount <= ZERO:
            raise ValidationError({"amount": "Ledger amount must be positive."})
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Ledger entries are immutable and cannot be deleted.")
