"""Transfer API + service tests, including concurrency and idempotency."""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest
from django.db import connection
from apps.ledger.models import LedgerEntry, LedgerEntryType
from apps.transactions.models import IdempotencyKey, Transaction, TransactionType
from apps.wallets.models import Currency, Wallet
from services.transfers import InsufficientFunds, create_transfer

TRANSFER_URL = "/api/transfers/"


@pytest.mark.django_db
def test_transfer_success_creates_two_ledger_entries(
    auth_client_a, user_a, user_b, wallet_pair
):
    wa, wb = wallet_pair
    response = auth_client_a.post(
        TRANSFER_URL,
        {"to_user_id": user_b.id, "amount": "25.50", "currency": "USD"},
        format="json",
    )
    assert response.status_code == 201
    assert response.data["status"] == "done"
    assert response.data["type"] == TransactionType.TRANSFER

    wa.refresh_from_db()
    wb.refresh_from_db()
    assert wa.available_balance == Decimal("74.50")
    assert wb.available_balance == Decimal("125.50")

    tx = Transaction.objects.get(pk=response.data["id"])
    entries = list(LedgerEntry.objects.filter(transaction=tx).order_by("id"))
    assert len(entries) == 2
    assert entries[0].entry_type == LedgerEntryType.DEBIT
    assert entries[1].entry_type == LedgerEntryType.CREDIT


@pytest.mark.django_db
def test_transfer_insufficient_funds(auth_client_a, user_b, wallet_pair):
    response = auth_client_a.post(
        TRANSFER_URL,
        {"to_user_id": user_b.id, "amount": "1000.00", "currency": "USD"},
        format="json",
    )
    assert response.status_code == 400
    assert "Insufficient" in response.data["detail"]
    assert Transaction.objects.count() == 0


@pytest.mark.django_db
def test_transfer_requires_auth(api_client, user_b):
    response = api_client.post(
        TRANSFER_URL,
        {"to_user_id": user_b.id, "amount": "1.00", "currency": "USD"},
        format="json",
    )
    assert response.status_code == 401


@pytest.mark.django_db
def test_idempotency_key_replays_without_double_transfer(
    auth_client_a, user_a, user_b, wallet_pair
):
    payload = {"to_user_id": user_b.id, "amount": "10.00", "currency": "USD"}
    headers = {"HTTP_IDEMPOTENCY_KEY": "retry-1"}

    first = auth_client_a.post(TRANSFER_URL, payload, format="json", **headers)
    second = auth_client_a.post(TRANSFER_URL, payload, format="json", **headers)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.data["id"] == second.data["id"]
    assert Transaction.objects.filter(type=TransactionType.TRANSFER).count() == 1
    assert IdempotencyKey.objects.filter(user=user_a, key="retry-1").count() == 1

    wa, wb = wallet_pair
    wa.refresh_from_db()
    wb.refresh_from_db()
    assert wa.available_balance == Decimal("90.00")
    assert wb.available_balance == Decimal("110.00")


@pytest.mark.django_db(transaction=True)
def test_concurrent_transfers_never_go_negative(user_a, user_b):
    """Many parallel debits from a limited balance must not overdraw."""
    Wallet.objects.create(
        user=user_a, currency=Currency.USD, available_balance=Decimal("100.00")
    )
    Wallet.objects.create(
        user=user_b, currency=Currency.USD, available_balance=Decimal("0.00")
    )

    results: list[str] = []
    lock = threading.Lock()

    def worker():
        connection.close_if_unusable_or_obsolete()
        try:
            create_transfer(
                sender=user_a,
                to_user_id=user_b.id,
                amount=Decimal("10.00"),
                currency=Currency.USD,
            )
            outcome = "ok"
        except InsufficientFunds:
            outcome = "insufficient"
        except Exception as exc:  # noqa: BLE001 — collect unexpected failures
            outcome = f"error:{exc}"
        finally:
            connection.close()
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert len(results) == 20
    assert results.count("ok") == 10
    assert results.count("insufficient") == 10
    assert not any(r.startswith("error:") for r in results)

    wa = Wallet.objects.get(user=user_a, currency=Currency.USD)
    wb = Wallet.objects.get(user=user_b, currency=Currency.USD)
    assert wa.available_balance == Decimal("0.00")
    assert wb.available_balance == Decimal("100.00")
    assert wa.available_balance >= 0
    assert wb.available_balance >= 0
    assert Transaction.objects.filter(type=TransactionType.TRANSFER).count() == 10
    assert LedgerEntry.objects.count() == 20


@pytest.mark.django_db(transaction=True)
def test_opposing_transfers_do_not_deadlock(user_a, user_b):
    """A→B and B→A at the same time must complete (deterministic lock order)."""
    Wallet.objects.create(
        user=user_a, currency=Currency.USD, available_balance=Decimal("50.00")
    )
    Wallet.objects.create(
        user=user_b, currency=Currency.USD, available_balance=Decimal("50.00")
    )

    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def run(sender, recipient_id):
        connection.close_if_unusable_or_obsolete()
        try:
            barrier.wait(timeout=10)
            create_transfer(
                sender=sender,
                to_user_id=recipient_id,
                amount=Decimal("10.00"),
                currency=Currency.USD,
            )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            connection.close()

    t1 = threading.Thread(target=run, args=(user_a, user_b.id))
    t2 = threading.Thread(target=run, args=(user_b, user_a.id))
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    assert t1.is_alive() is False and t2.is_alive() is False
    assert errors == []

    wa = Wallet.objects.get(user=user_a, currency=Currency.USD)
    wb = Wallet.objects.get(user=user_b, currency=Currency.USD)
    assert wa.available_balance == Decimal("50.00")
    assert wb.available_balance == Decimal("50.00")
    assert Transaction.objects.filter(type=TransactionType.TRANSFER).count() == 2


@pytest.mark.django_db(transaction=True)
def test_concurrent_idempotent_requests_single_transfer(user_a, user_b, wallet_pair):
    from services.transfers import execute_transfer_request

    barrier = threading.Barrier(2)
    results: list[dict] = []
    lock = threading.Lock()

    def worker():
        connection.close_if_unusable_or_obsolete()
        try:
            barrier.wait(timeout=10)
            result = execute_transfer_request(
                sender=user_a,
                to_user_id=user_b.id,
                amount=Decimal("15.00"),
                currency=Currency.USD,
                idempotency_key="parallel-key",
            )
            with lock:
                results.append(
                    {
                        "status_code": result.status_code,
                        "id": result.body.get("id"),
                        "replayed": result.replayed,
                    }
                )
        finally:
            connection.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert len(results) == 2
    assert all(r["status_code"] == 201 for r in results)
    assert results[0]["id"] == results[1]["id"]
    assert Transaction.objects.filter(type=TransactionType.TRANSFER).count() == 1
    wa, wb = wallet_pair
    wa.refresh_from_db()
    wb.refresh_from_db()
    assert wa.available_balance == Decimal("85.00")
    assert wb.available_balance == Decimal("115.00")
