"""Extract a small, deterministic subset of the tau2-bench retail DB.

Source: sierra-research/tau2-bench, data/tau2/domains/retail/db.json
Commit: c06b07dd7c4d4ec61ba6ff9bcdf0b15dd7ef36d4 (MIT licence, (c) 2025 Sierra Research)

Usage: python scripts/extract_seed_db.py <path/to/tau2/db.json> ablate/world/retail/seed_db.json

Selection (deterministic, no RNG): users sorted by user_id; keep a user when
they have at least one pending or delivered order; take all their orders while
the total stays <= 60; stop at 30 users. Products keep only the variants that
appear in the kept orders.
"""
from __future__ import annotations

import json
import sys

MAX_USERS = 30
MAX_ORDERS = 60


def main(src: str, dst: str) -> None:
    with open(src, encoding="utf-8") as f:
        db = json.load(f)
    users, orders = {}, {}
    for uid in sorted(db["users"]):
        u = db["users"][uid]
        his = [db["orders"][o] for o in u["orders"] if o in db["orders"]]
        if not any(o["status"] in ("pending", "delivered") for o in his):
            continue
        if len(orders) + len(his) > MAX_ORDERS:
            continue
        users[uid] = u
        for o in his:
            orders[o["order_id"]] = o
        if len(users) >= MAX_USERS:
            break
    products = {}
    for o in orders.values():
        for it in o["items"]:
            p = db["products"][it["product_id"]]
            dst_p = products.setdefault(p["product_id"], {"name": p["name"], "product_id": p["product_id"], "variants": {}})
            if it["item_id"] in p["variants"]:
                dst_p["variants"][it["item_id"]] = p["variants"][it["item_id"]]
    out = {"products": dict(sorted(products.items())), "users": users, "orders": dict(sorted(orders.items()))}
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, sort_keys=True)
    from collections import Counter
    print(len(users), "users", len(orders), "orders", len(products), "products",
          Counter(o["status"] for o in orders.values()))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
