"""API URL routes."""

from django.urls import path
from rest_framework.authtoken.views import obtain_auth_token

from api.transfers import TransferCreateView
from api.webhooks import DepositWebhookView

urlpatterns = [
    path("auth/token/", obtain_auth_token, name="api-token-auth"),
    path("transfers/", TransferCreateView.as_view(), name="api-transfers"),
    path(
        "webhooks/deposit/",
        DepositWebhookView.as_view(),
        name="api-webhooks-deposit",
    ),
    # path("withdrawals/", ...),  # P4
]
