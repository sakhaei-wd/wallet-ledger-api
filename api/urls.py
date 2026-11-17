"""API URL routes. Endpoints are added in later phases (transfers, webhooks, withdrawals)."""

from django.urls import path
from rest_framework.authtoken.views import obtain_auth_token

from api.transfers import TransferCreateView

urlpatterns = [
    path("auth/token/", obtain_auth_token, name="api-token-auth"),
    path("transfers/", TransferCreateView.as_view(), name="api-transfers"),
    # path("webhooks/deposit/", ...),  # P3
    # path("withdrawals/", ...),       # P4
]
