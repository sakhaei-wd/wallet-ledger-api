"""HTTP layer for POST /api/transfers/."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.transactions.models import Transaction
from apps.wallets.models import MONEY_DECIMAL_PLACES, MONEY_MAX_DIGITS, Currency
from services.transfers import execute_transfer_request

User = get_user_model()

IDEMPOTENCY_HEADER = "Idempotency-Key"


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

    @extend_schema(
        tags=["transfers"],
        summary="Create internal transfer",
        description=(
            "Debit the authenticated user's wallet and credit another user "
            "in the same currency. Pass `Idempotency-Key` to safely retry."
        ),
        request=TransferCreateSerializer,
        responses={
            201: TransferResponseSerializer,
            400: OpenApiResponse(description="Validation or insufficient funds"),
        },
        parameters=[
            OpenApiParameter(
                name=IDEMPOTENCY_HEADER,
                type=str,
                location=OpenApiParameter.HEADER,
                required=False,
                description="Client-supplied key; identical retries return the stored response.",
            ),
        ],
    )
    def post(self, request: Request) -> Response:
        serializer = TransferCreateSerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)

        result = execute_transfer_request(
            sender=request.user,
            to_user_id=serializer.validated_data["to_user_id"],
            amount=serializer.validated_data["amount"],
            currency=serializer.validated_data["currency"],
            idempotency_key=request.headers.get(IDEMPOTENCY_HEADER),
            request_path=request.path,
        )
        return Response(result.body, status=result.status_code)
