"""API URL routes. Endpoints are added in later phases (transfers, webhooks, withdrawals)."""

from django.urls import path
from rest_framework.authtoken.views import obtain_auth_token

urlpatterns = [
    # Obtain a DRF auth token: POST {"username": "...", "password": "..."}
    path("auth/token/", obtain_auth_token, name="api-token-auth"),
    # path("transfers/", ...),         # P2
    # path("webhooks/deposit/", ...),  # P3
    # path("withdrawals/", ...),       # P4
]
