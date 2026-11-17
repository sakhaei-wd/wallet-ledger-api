"""HTTP layer for POST /api/withdrawals/."""

from decimal import Decimal

from rest_framework import serializers, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.wallets.models import MONEY_DECIMAL_PLACES, MONEY_MAX_DIGITS, Currency
from services.withdrawals import (
    InsufficientFunds,
    WithdrawalError,
    WithdrawalValidationError,
    request_withdrawal,
    serialize_withdrawal,
)
from tasks.withdrawals import process_withdrawal


class WithdrawalCreateSerializer(serializers.Serializer):
    amount = serializers.DecimalField(
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
        min_value=Decimal("0.00000001"),
    )
    currency = serializers.ChoiceField(choices=Currency.choices)


class WithdrawalCreateView(APIView):
    """Lock funds and enqueue gateway settlement for a withdrawal."""

    def post(self, request: Request) -> Response:
        serializer = WithdrawalCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            tx = request_withdrawal(
                user=request.user,
                amount=serializer.validated_data["amount"],
                currency=serializer.validated_data["currency"],
            )
        except InsufficientFunds as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except WithdrawalValidationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except WithdrawalError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        process_withdrawal.delay(tx.id)
        return Response(serialize_withdrawal(tx), status=status.HTTP_201_CREATED)
