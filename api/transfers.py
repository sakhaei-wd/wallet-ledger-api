"""HTTP layer for POST /api/transfers/."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework import serializers, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.transactions.models import Transaction
from apps.wallets.models import MONEY_DECIMAL_PLACES, MONEY_MAX_DIGITS, Currency
from services.transfers import (
    InsufficientFunds,
    TransferError,
    TransferValidationError,
    create_transfer,
)

User = get_user_model()


class TransferCreateSerializer(serializers.Serializer):
    to_user_id = serializers.IntegerField(min_value=1)
    amount = serializers.DecimalField(
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
        min_value=Decimal("0.00000001"),
    )
    currency = serializers.ChoiceField(choices=Currency.choices)

    def validate_to_user_id(self, value: int) -> int:
        if not User.objects.filter(pk=value).exists():
            raise serializers.ValidationError("Recipient user does not exist.")
        request = self.context.get("request")
        if request is not None and value == request.user.id:
            raise serializers.ValidationError("Cannot transfer to yourself.")
        return value


class TransferResponseSerializer(serializers.ModelSerializer):
    from_wallet_id = serializers.IntegerField(source="from_wallet.id", read_only=True)
    to_wallet_id = serializers.IntegerField(source="to_wallet.id", read_only=True)

    class Meta:
        model = Transaction
        fields = [
            "id",
            "type",
            "status",
            "amount",
            "currency",
            "from_wallet_id",
            "to_wallet_id",
            "created_at",
        ]


class TransferCreateView(APIView):
    """Create an internal transfer from the authenticated user to another user."""

    def post(self, request: Request) -> Response:
        serializer = TransferCreateSerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)

        try:
            transaction = create_transfer(
                sender=request.user,
                to_user_id=serializer.validated_data["to_user_id"],
                amount=serializer.validated_data["amount"],
                currency=serializer.validated_data["currency"],
            )
        except NotImplementedError:
            return Response(
                {"detail": "Transfer service not implemented yet (P2-02)."},
                status=status.HTTP_501_NOT_IMPLEMENTED,
            )
        except InsufficientFunds as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except TransferValidationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except TransferError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            TransferResponseSerializer(transaction).data,
            status=status.HTTP_201_CREATED,
        )
