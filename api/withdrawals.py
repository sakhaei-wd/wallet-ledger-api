"""HTTP layer for POST /api/withdrawals/."""

from decimal import Decimal

from drf_spectacular.utils import OpenApiResponse, extend_schema
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


class WithdrawalResponseSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    type = serializers.CharField()
    status = serializers.CharField()
    amount = serializers.CharField(help_text="Decimal string")
    currency = serializers.ChoiceField(choices=Currency.choices)
    from_wallet_id = serializers.IntegerField()
    failure_reason = serializers.CharField(allow_blank=True)
    created_at = serializers.CharField(help_text="ISO-8601 timestamp")


class WithdrawalCreateView(APIView):
    """Lock funds and enqueue gateway settlement for a withdrawal."""

    @extend_schema(
        tags=["withdrawals"],
        summary="Request withdrawal",
        description=(
            "Locks `amount` from available balance and enqueues a Celery mock "
            "gateway job that settles (DONE) or unlocks (FAILED)."
        ),
        request=WithdrawalCreateSerializer,
        responses={
            201: WithdrawalResponseSerializer,
            400: OpenApiResponse(description="Validation or insufficient funds"),
        },
    )
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
