"""HMAC-authenticated deposit webhook endpoint."""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.wallets.models import MONEY_DECIMAL_PLACES, MONEY_MAX_DIGITS, Currency
from services.deposits import (
    DepositValidationError,
    IllegalDepositTransition,
    process_deposit_event,
    verify_deposit_signature,
)

SIGNATURE_HEADER = "X-Signature"


class DepositWebhookSerializer(serializers.Serializer):
    external_id = serializers.CharField()
    user_id = serializers.IntegerField(min_value=1)
    amount = serializers.DecimalField(
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
        min_value=Decimal("0.00000001"),
    )
    currency = serializers.ChoiceField(choices=Currency.choices)
    status = serializers.ChoiceField(choices=["pending", "done", "failed"])
    failure_reason = serializers.CharField(required=False, allow_blank=True)


class DepositWebhookResponseSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    type = serializers.CharField()
    status = serializers.CharField()
    amount = serializers.CharField(help_text="Decimal string")
    currency = serializers.ChoiceField(choices=Currency.choices)
    external_id = serializers.CharField()
    to_wallet_id = serializers.IntegerField(required=False)
    failure_reason = serializers.CharField(required=False, allow_blank=True)
    created_at = serializers.CharField(help_text="ISO-8601 timestamp")


class DepositWebhookView(APIView):
    """POST /api/webhooks/deposit/ — gateway deposit events (HMAC-SHA256)."""

    authentication_classes: list = []
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["webhooks"],
        summary="Deposit webhook",
        description=(
            "HMAC-SHA256 of the raw body with `DEPOSIT_WEBHOOK_SECRET`, sent as "
            f"`{SIGNATURE_HEADER}`. Idempotent on `external_id`; finals "
            "(`done` / `failed`) are immutable."
        ),
        request=DepositWebhookSerializer,
        responses={
            200: DepositWebhookResponseSerializer,
            201: DepositWebhookResponseSerializer,
            400: OpenApiResponse(description="Invalid payload"),
            401: OpenApiResponse(description="Invalid signature"),
            409: OpenApiResponse(description="Illegal status transition"),
        },
        parameters=[
            OpenApiParameter(
                name=SIGNATURE_HEADER,
                type=str,
                location=OpenApiParameter.HEADER,
                required=True,
                description="hex HMAC-SHA256 of the raw request body",
            ),
        ],
        auth=[],
    )
    def post(self, request: Request) -> Response:
        raw_body = request.body
        signature = request.headers.get(SIGNATURE_HEADER, "")
        if not verify_deposit_signature(body=raw_body, signature=signature):
            return Response(
                {"detail": "Invalid signature."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return Response(
                {"detail": "Invalid JSON body."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not isinstance(payload, dict):
            return Response(
                {"detail": "JSON body must be an object."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            external_id = str(payload["external_id"])
            user_id = int(payload["user_id"])
            amount = Decimal(str(payload["amount"]))
            currency = str(payload["currency"]).upper()
            event_status = str(payload["status"]).lower()
        except (KeyError, TypeError, ValueError, InvalidOperation):
            return Response(
                {
                    "detail": (
                        "Required fields: external_id, user_id, amount, "
                        "currency, status."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if currency not in Currency.values:
            return Response(
                {"detail": f"Unsupported currency: {currency}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        failure_reason = str(payload.get("failure_reason", "") or "")

        try:
            result = process_deposit_event(
                external_id=external_id,
                user_id=user_id,
                amount=amount,
                currency=currency,
                status=event_status,
                failure_reason=failure_reason,
            )
        except DepositValidationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except IllegalDepositTransition as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)

        return Response(result.body, status=result.status_code)
