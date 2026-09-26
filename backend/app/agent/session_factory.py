"""Builds a CallSession bound to a profile (§6.1) for either channel."""
from __future__ import annotations

from app.agent.conversation import CallSession
from app.agent.runtime import Runtime, Transport
from app.models import CustomerProfile
from app.store import calls_repo, queries
from app.store.db import get_session

UNKNOWN_CUSTOMER = CustomerProfile(id="cust_unknown", name="Unknown caller", phone="+910000000000",
                                   language_code="hi-IN", account_age_days=0, avg_order_inr=0)


class UnknownProfile(Exception):
    pass


def _load(profile_key: str, caller_number: str | None):
    customer_id: str | None
    if profile_key == "auto":
        customer_id = calls_repo.find_customer_by_phone(caller_number or "")
    else:
        customer_id = profile_key if profile_key.startswith("cust_") else f"cust_{profile_key}"
    with get_session() as s:
        sop = queries.load_sop(s)
        if customer_id is None:
            return UNKNOWN_CUSTOMER, None, sop, 0, False
        customer = queries.load_customer_profile(s, customer_id)
        if customer is None:
            raise UnknownProfile(profile_key)
        order = queries.load_customer_order(s, customer_id)
        refunds = queries.count_refunds_in_window(s, customer_id, sop)
    return customer, order, sop, refunds, True


async def build_session(runtime: Runtime, call_id: str, profile_key: str, transport: Transport | None = None,
                        caller_number: str | None = None, caller_masked: str | None = None) -> CallSession:
    customer, order, sop, refunds, known = await calls_repo.run(_load, profile_key, caller_number)
    return CallSession(call_id=call_id, customer=customer, order=order, sop=sop, refunds_in_window=refunds,
                       runtime=runtime, transport=transport, caller_masked=caller_masked, profile_known=known)
