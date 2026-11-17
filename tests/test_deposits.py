"""Deposit webhook tests: HMAC, idempotency, transitions, out-of-order."""

from __future__ import annotations

import hashlib
import hmac
import json
from decimal import Decimal

import pytest
from django.test import override_settings

from apps.ledger.models import LedgerEntry, LedgerEntryType
from apps.transactions.models import Transaction, TransactionStatus, TransactionType
from apps.wallets.models import Currency, Wallet

WEBHOOK_URL = "/api/webhooks/deposit/"
SECRET = "test-deposit-webhook-secret"


def _sign(body: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _post_webhook(api_client, payload: dict, *, secret: str = SECRET, signature: str | None = None):
    body = json.dumps(payload).encode("utf-8")
    sig = signature if signature is not None else _sign(body, secret)
    return api_client.post(
        WEBHOOK_URL,
        data=body,
        content_type="application/json",
        HTTP_X_SIGNATURE=sig,
    )


@pytest.fixture
def deposit_user(db, django_user_model):
    return django_user_model.objects.create_user(username="depositor", password="x")


@override_settings(DEPOSIT_WEBHOOK_SECRET=SECRET)
@pytest.mark.django_db
def test_invalid_signature_rejected(api_client, deposit_user):
    payload = {
        "external_id": "evt-bad-sig",
        "user_id": deposit_user.id,
        "amount": "1.0",
        "currency": "BTC",
        "status": "done",
    }
    response = _post_webhook(api_client, payload, signature="deadbeef")
    assert response.status_code == 401
    assert Transaction.objects.count() == 0


@override_settings(DEPOSIT_WEBHOOK_SECRET=SECRET)
@pytest.mark.django_db
def test_pending_then_done_credits_once(api_client, deposit_user):
    base = {
        "external_id": "evt-1",
        "user_id": deposit_user.id,
        "amount": "0.5",
        "currency": "BTC",
    }
    pending = _post_webhook(api_client, {**base, "status": "pending"})
    assert pending.status_code == 200
    assert pending.data["status"] == TransactionStatus.PENDING

    wallet = Wallet.objects.get(user=deposit_user, currency=Currency.BTC)
    assert wallet.available_balance == Decimal("0")

    done = _post_webhook(api_client, {**base, "status": "done"})
    assert done.status_code == 200
    assert done.data["status"] == TransactionStatus.DONE

    wallet.refresh_from_db()
    assert wallet.available_balance == Decimal("0.5")
    assert Transaction.objects.filter(external_id="evt-1").count() == 1
    entries = LedgerEntry.objects.filter(transaction_id=done.data["id"])
    assert entries.count() == 1
    assert entries.get().entry_type == LedgerEntryType.CREDIT


@override_settings(DEPOSIT_WEBHOOK_SECRET=SECRET)
@pytest.mark.django_db
def test_duplicate_done_does_not_double_credit(api_client, deposit_user):
    payload = {
        "external_id": "evt-dup",
        "user_id": deposit_user.id,
        "amount": "2.0",
        "currency": "USD",
        "status": "done",
    }
    first = _post_webhook(api_client, payload)
    second = _post_webhook(api_client, payload)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.data["id"] == second.data["id"]

    wallet = Wallet.objects.get(user=deposit_user, currency=Currency.USD)
    assert wallet.available_balance == Decimal("2.0")
    assert Transaction.objects.filter(external_id="evt-dup").count() == 1
    assert LedgerEntry.objects.count() == 1


@override_settings(DEPOSIT_WEBHOOK_SECRET=SECRET)
@pytest.mark.django_db
def test_out_of_order_pending_after_done_is_noop(api_client, deposit_user):
    base = {
        "external_id": "evt-ooo",
        "user_id": deposit_user.id,
        "amount": "1.25",
        "currency": "EUR",
    }
    done = _post_webhook(api_client, {**base, "status": "done"})
    assert done.status_code == 200
    assert done.data["status"] == TransactionStatus.DONE

    pending = _post_webhook(api_client, {**base, "status": "pending"})
    assert pending.status_code == 200
    assert pending.data["status"] == TransactionStatus.DONE

    wallet = Wallet.objects.get(user=deposit_user, currency=Currency.EUR)
    assert wallet.available_balance == Decimal("1.25")
    assert LedgerEntry.objects.count() == 1


@override_settings(DEPOSIT_WEBHOOK_SECRET=SECRET)
@pytest.mark.django_db
def test_illegal_transition_done_then_failed(api_client, deposit_user):
    base = {
        "external_id": "evt-illegal",
        "user_id": deposit_user.id,
        "amount": "3.0",
        "currency": "USD",
    }
    assert _post_webhook(api_client, {**base, "status": "done"}).status_code == 200
    failed = _post_webhook(
        api_client, {**base, "status": "failed", "failure_reason": "too late"}
    )
    assert failed.status_code == 409

    tx = Transaction.objects.get(external_id="evt-illegal")
    assert tx.status == TransactionStatus.DONE
    wallet = Wallet.objects.get(user=deposit_user, currency=Currency.USD)
    assert wallet.available_balance == Decimal("3.0")


@override_settings(DEPOSIT_WEBHOOK_SECRET=SECRET)
@pytest.mark.django_db
def test_pending_then_failed_does_not_credit(api_client, deposit_user):
    base = {
        "external_id": "evt-fail",
        "user_id": deposit_user.id,
        "amount": "9.0",
        "currency": "USD",
    }
    assert _post_webhook(api_client, {**base, "status": "pending"}).status_code == 200
    failed = _post_webhook(
        api_client, {**base, "status": "failed", "failure_reason": "rejected"}
    )
    assert failed.status_code == 200
    assert failed.data["status"] == TransactionStatus.FAILED

    wallet = Wallet.objects.get(user=deposit_user, currency=Currency.USD)
    assert wallet.available_balance == Decimal("0")
    assert LedgerEntry.objects.count() == 0
    assert Transaction.objects.filter(type=TransactionType.DEPOSIT).count() == 1


@override_settings(DEPOSIT_WEBHOOK_SECRET=SECRET)
@pytest.mark.django_db
def test_failed_then_done_is_illegal(api_client, deposit_user):
    base = {
        "external_id": "evt-fail-done",
        "user_id": deposit_user.id,
        "amount": "1.0",
        "currency": "USD",
    }
    assert _post_webhook(api_client, {**base, "status": "failed"}).status_code == 200
    done = _post_webhook(api_client, {**base, "status": "done"})
    assert done.status_code == 409
    wallet = Wallet.objects.get(user=deposit_user, currency=Currency.USD)
    assert wallet.available_balance == Decimal("0")
