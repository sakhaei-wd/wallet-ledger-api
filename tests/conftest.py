import pytest
from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.wallets.models import Currency, Wallet

User = get_user_model()


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def user_a(db):
    return User.objects.create_user(username="alice", password="pass-alice")


@pytest.fixture
def user_b(db):
    return User.objects.create_user(username="bob", password="pass-bob")


@pytest.fixture
def auth_client_a(api_client, user_a):
    token = Token.objects.create(user=user_a)
    api_client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
    return api_client


@pytest.fixture
def wallet_pair(user_a, user_b):
    """USD wallets for alice (100) and bob (100)."""
    from decimal import Decimal

    wa = Wallet.objects.create(
        user=user_a,
        currency=Currency.USD,
        available_balance=Decimal("100.00"),
    )
    wb = Wallet.objects.create(
        user=user_b,
        currency=Currency.USD,
        available_balance=Decimal("100.00"),
    )
    return wa, wb
