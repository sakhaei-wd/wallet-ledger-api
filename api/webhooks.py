"""HMAC-authenticated deposit webhook endpoint."""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.wallets.models import Currency
from services.deposits import (
    DepositValidationError,
    IllegalDepositTransition,
    process_deposit_event,
    verify_deposit_signature,
)

SIGNATURE_HEADER = "X-Signature"


class DepositWebhookView(APIView):
    """POST /api/webhooks/deposit/ — gateway deposit events (HMAC-SHA256)."""

    authentication_classes: list = []
    permission_classes = [AllowAny]

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
