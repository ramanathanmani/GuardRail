"""PaymentsClient: Dodo (test mode) | Simulated (§5.6). Only refunds ever call this."""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Protocol

from app.config import Settings

logger = logging.getLogger("guardrail.payments")


@dataclass
class RefundResult:
    refund_id: str
    status: str
    amount: float
    currency: str
    provider: str  # dodo | simulated


class PaymentsError(Exception):
    pass


class PaymentsClient(Protocol):
    provider: str

    async def refund(self, payment_id: str, amount_inr: float, reason: str, metadata: dict) -> RefundResult: ...


class SimulatedPayments:
    provider = "simulated"

    async def refund(self, payment_id: str, amount_inr: float, reason: str, metadata: dict) -> RefundResult:
        return RefundResult(refund_id=f"sim_ref_{uuid.uuid4().hex[:10]}", status="succeeded",
                            amount=amount_inr, currency="INR", provider="simulated")


class DodoPayments:
    provider = "dodo"

    def __init__(self, settings: Settings) -> None:
        from dodopayments import AsyncDodoPayments

        self._client = AsyncDodoPayments(
            bearer_token=settings.DODO_PAYMENTS_API_KEY, environment=settings.DODO_ENVIRONMENT, max_retries=0,
        )

    async def refund(self, payment_id: str, amount_inr: float, reason: str, metadata: dict) -> RefundResult:
        try:
            refund = await self._client.refunds.create(
                payment_id=payment_id, reason=reason, metadata={k: str(v) for k, v in metadata.items()}, timeout=20,
            )
        except Exception as exc:  # noqa: BLE001 — never auto-retry; surface as a failure
            raise PaymentsError(f"{type(exc).__name__}: {str(exc)[:200]}") from exc
        status = str(getattr(refund.status, "value", refund.status))
        if status == "failed":
            raise PaymentsError(f"Dodo refund {refund.refund_id} failed")
        amount = (refund.amount / 100) if refund.amount is not None else amount_inr
        currency = str(getattr(refund.currency, "value", refund.currency or "INR"))
        return RefundResult(refund_id=refund.refund_id, status=status, amount=amount, currency=currency, provider="dodo")


def get_payments_client(settings: Settings) -> PaymentsClient:
    settings.require_payments()
    if settings.PAYMENTS == "dodo":
        return DodoPayments(settings)
    if settings.PAYMENTS == "simulated":
        return SimulatedPayments()
    raise ValueError(f"Unsupported PAYMENTS: {settings.PAYMENTS}")
