from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


class Currency(models.TextChoices):
    USD = "USD", "US Dollar"
    EUR = "EUR", "Euro"
    BTC = "BTC", "Bitcoin"


# Supports fiat and BTC without float rounding issues.
MONEY_MAX_DIGITS = 28
MONEY_DECIMAL_PLACES = 8
ZERO = Decimal("0")


class Wallet(models.Model):
    """One wallet per user per currency. Balances are never negative."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="wallets",
    )
    currency = models.CharField(max_length=8, choices=Currency.choices)
    available_balance = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
        default=ZERO,
        validators=[MinValueValidator(ZERO)],
    )
    locked_balance = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
        default=ZERO,
        validators=[MinValueValidator(ZERO)],
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "currency"],
                name="uniq_wallet_user_currency",
            ),
            models.CheckConstraint(
                condition=models.Q(available_balance__gte=0),
                name="wallet_available_balance_gte_0",
            ),
            models.CheckConstraint(
                condition=models.Q(locked_balance__gte=0),
                name="wallet_locked_balance_gte_0",
            ),
        ]
        ordering = ["user_id", "currency"]

    def __str__(self) -> str:
        return f"Wallet(user={self.user_id}, {self.currency})"
