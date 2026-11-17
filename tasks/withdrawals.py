"""Celery task: submit withdrawal to mock gateway and settle."""

from __future__ import annotations

import logging

from celery import shared_task

from services.gateway import (
    GatewayPermanentError,
    GatewayTemporaryError,
    withdrawal_gateway,
)
from services.withdrawals import (
    mark_withdrawal_processing,
    settle_withdrawal_failure,
    settle_withdrawal_success,
)

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    name="tasks.withdrawals.process_withdrawal",
    max_retries=5,
    autoretry_for=(GatewayTemporaryError,),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
)
def process_withdrawal(self, transaction_id: int) -> dict:
    """
    Drive a withdrawal through the mock gateway.

    Idempotent on re-execution: final DONE/FAILED states are no-ops; gateway
    submissions are keyed by transaction id.
    """
    tx = mark_withdrawal_processing(transaction_id)
    if tx is None:
        logger.info(
            "withdrawal %s already finalized; skipping", transaction_id
        )
        return {"status": "already_final", "transaction_id": transaction_id}

    try:
        withdrawal_gateway.withdraw(
            reference=f"withdrawal:{transaction_id}",
            amount=tx.amount,
            currency=tx.currency,
        )
    except GatewayPermanentError as exc:
        settle_withdrawal_failure(transaction_id, reason=str(exc))
        return {
            "status": "failed",
            "transaction_id": transaction_id,
            "reason": str(exc),
        }
    except GatewayTemporaryError:
        # Let Celery autoretry with exponential backoff.
        raise

    settled = settle_withdrawal_success(transaction_id)
    return {"status": settled.status, "transaction_id": transaction_id}
