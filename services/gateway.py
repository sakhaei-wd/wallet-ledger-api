"""Mock external withdrawal gateway (deterministic, idempotent by reference)."""

from __future__ import annotations

from decimal import Decimal


class GatewayError(Exception):
    """Base gateway error."""


class GatewayTemporaryError(GatewayError):
    """Transient failure — safe for Celery retry."""


class GatewayPermanentError(GatewayError):
    """Terminal failure — do not retry; unlock funds."""


class MockWithdrawalGateway:
    """
    In-process mock used by Celery tasks and tests.

    `mode`:
      - success: accept withdrawal
      - temporary_fail: always raise GatewayTemporaryError
      - permanent_fail: raise GatewayPermanentError
      - fail_then_success: fail `fail_times` then succeed
    """

    def __init__(self) -> None:
        self.mode = "success"
        self.fail_times = 0
        self._fail_counts: dict[str, int] = {}
        self.calls: list[dict] = []
        self.completed_refs: set[str] = set()

    def reset(self) -> None:
        self.mode = "success"
        self.fail_times = 0
        self._fail_counts.clear()
        self.calls.clear()
        self.completed_refs.clear()

    def withdraw(
        self,
        *,
        reference: str,
        amount: Decimal,
        currency: str,
    ) -> dict:
        self.calls.append(
            {"reference": reference, "amount": amount, "currency": currency}
        )

        # Idempotent re-submission after a prior success.
        if reference in self.completed_refs:
            return {"status": "ok", "duplicate": True, "reference": reference}

        if self.mode == "temporary_fail":
            raise GatewayTemporaryError("gateway temporarily unavailable")

        if self.mode == "permanent_fail":
            raise GatewayPermanentError("gateway rejected withdrawal")

        if self.mode == "fail_then_success":
            count = self._fail_counts.get(reference, 0)
            if count < self.fail_times:
                self._fail_counts[reference] = count + 1
                raise GatewayTemporaryError("gateway temporary glitch")

        self.completed_refs.add(reference)
        return {"status": "ok", "duplicate": False, "reference": reference}


withdrawal_gateway = MockWithdrawalGateway()
