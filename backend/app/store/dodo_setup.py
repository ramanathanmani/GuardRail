"""Dodo test payments for the refundable demo orders (§5.6).

  python -m app.store.dodo_setup --product ORD-66540=pdt_xxx --product ORD-77102=pdt_yyy [--count 5]
      create payment links (metadata.order_id) and print them — pay each with a Dodo test card
  python -m app.store.dodo_setup --sync     store succeeded payments in `payments`
  python -m app.store.dodo_setup --status   unrefunded payments left per order

The product ids come from the Dodo dashboard (test mode) products you created by hand.
"""
from __future__ import annotations

import argparse

from sqlmodel import select

from app.config import get_settings
from app.store.db import Order, Payment, get_session, init_db

DEFAULT_COUNTS = {"ORD-66540": 5, "ORD-77102": 3}


def _client():
    from dodopayments import DodoPayments

    settings = get_settings()
    settings.require("DODO_PAYMENTS_API_KEY")
    return DodoPayments(bearer_token=settings.DODO_PAYMENTS_API_KEY, environment=settings.DODO_ENVIRONMENT)


def create_links(products: dict[str, str], count: int | None) -> None:
    client = _client()
    for order_id, product_id in products.items():
        n = count or DEFAULT_COUNTS.get(order_id, 3)
        print(f"\n{order_id} ({product_id}) — {n} links:")
        for _ in range(n):
            resp = client.payments.create(
                billing={"country": "IN", "city": "Bengaluru", "state": "KA", "street": "1 Demo St", "zipcode": "560001"},
                customer={"email": "guardrail-demo@example.com", "name": "GuardRail Demo"},
                product_cart=[{"product_id": product_id, "quantity": 1}],
                payment_link=True,
                metadata={"order_id": order_id},
            )
            print(f"  {resp.payment_link}  (payment_id {resp.payment_id})")
    print("\nPay each link with a Dodo test card, then run: python -m app.store.dodo_setup --sync")


def sync() -> None:
    client = _client()
    added = 0
    with get_session() as s:
        orders = {o.id: o for o in s.exec(select(Order)).all()}
        for p in client.payments.list():
            status = str(getattr(p.status, "value", p.status))
            order_id = (p.metadata or {}).get("order_id")
            if status != "succeeded" or order_id not in orders or s.get(Payment, p.payment_id):
                continue
            refunded = str(getattr(p.refund_status, "value", p.refund_status or "")) not in ("", "None")
            s.add(Payment(payment_id=p.payment_id, order_id=order_id, amount_inr=p.total_amount / 100, refunded=refunded))
            added += 1
        s.commit()
    print(f"Stored {added} new succeeded payment(s).")
    status()


def status() -> None:
    with get_session() as s:
        payments = s.exec(select(Payment)).all()
    per: dict[str, list[int]] = {}
    for p in payments:
        left_total = per.setdefault(p.order_id, [0, 0])
        left_total[1] += 1
        if not p.refunded:
            left_total[0] += 1
    if not per:
        print("No payments stored yet.")
    for order_id, (left, total) in sorted(per.items()):
        print(f"{order_id}: {left} unrefunded of {total}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--product", action="append", default=[], help="ORDER_ID=DODO_PRODUCT_ID")
    parser.add_argument("--count", type=int)
    parser.add_argument("--sync", action="store_true")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()
    init_db(get_settings())
    if args.sync:
        sync()
    elif args.status:
        status()
    elif args.product:
        create_links(dict(p.split("=", 1) for p in args.product), args.count)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
