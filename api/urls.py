"""API URL routes."""

from django.urls import path
from drf_spectacular.utils import extend_schema, extend_schema_view
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)
from rest_framework import serializers
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.permissions import AllowAny

from api.transfers import TransferCreateView
from api.webhooks import DepositWebhookView
from api.withdrawals import WithdrawalCreateView

_schema_kwargs = {
    "authentication_classes": [],
    "permission_classes": [AllowAny],
}


class TokenObtainRequestSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(style={"input_type": "password"})


class TokenObtainResponseSerializer(serializers.Serializer):
    token = serializers.CharField()


@extend_schema_view(
    post=extend_schema(
        tags=["auth"],
        summary="Obtain API token",
        description="Exchange username/password for a DRF auth token.",
        auth=[],
        request=TokenObtainRequestSerializer,
        responses={200: TokenObtainResponseSerializer},
    )
)
class TokenObtainView(ObtainAuthToken):
    authentication_classes: list = []
    permission_classes = [AllowAny]


urlpatterns = [
    path(
        "schema/",
        SpectacularAPIView.as_view(**_schema_kwargs),
        name="api-schema",
    ),
    path(
        "docs/",
        SpectacularSwaggerView.as_view(url_name="api-schema", **_schema_kwargs),
        name="api-docs",
    ),
    path(
        "redoc/",
        SpectacularRedocView.as_view(url_name="api-schema", **_schema_kwargs),
        name="api-redoc",
    ),
    path("auth/token/", TokenObtainView.as_view(), name="api-token-auth"),
    path("transfers/", TransferCreateView.as_view(), name="api-transfers"),
    path(
        "webhooks/deposit/",
        DepositWebhookView.as_view(),
        name="api-webhooks-deposit",
    ),
    path("withdrawals/", WithdrawalCreateView.as_view(), name="api-withdrawals"),
]
