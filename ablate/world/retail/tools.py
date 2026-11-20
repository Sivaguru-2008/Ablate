# Adapted from sierra-research/tau2-bench
#   source repo: https://github.com/sierra-research/tau2-bench
#   file:        src/tau2/domains/retail/tools.py
#   commit:      c06b07dd7c4d4ec61ba6ff9bcdf0b15dd7ef36d4
#   licence:     MIT, Copyright (c) 2025 Sierra Research (see THIRD_PARTY.md)
# Local changes: removed ToolKitBase / Tool / DB / regional / fold_for_match;
# kept 5 tools as plain methods on RetailWorld (implements world/base.py);
# added reset(seed), state_hash(), call() dispatch that fails closed on unknown
# tools and bad arguments, a call log, and an effect log with amount_inr.
"""Deterministic retail world adapted from the tau2-bench retail domain."""

from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from ..base import sha256_json
from .data_model import GiftCard, Order, OrderPayment, RetailDB, User

SEED_DB_PATH = Path(__file__).with_name("seed_db.json")

# DECISION: amounts in the effect log are converted from the seed DB's USD
# prices at one fixed, stated rate so rupee impact numbers are reproducible.
USD_TO_INR = 84.0

# name -> (params [(name, type, description)], mutating, default description)
TOOL_SPECS: dict[str, dict[str, Any]] = {
    "find_user_id_by_email": {
        "params": [("email", "str", "The email of the user, such as 'something@example.com'.")],
        "mutating": False,
        "description": "Find user id by email. If the user is not found, the function will return an error message.",
    },
    "find_user_id_by_name_zip": {
        "params": [
            ("first_name", "str", "The first name of the customer, such as 'John'."),
            ("last_name", "str", "The last name of the customer, such as 'Doe'."),
            ("zip", "str", "The zip code of the customer, such as '12345'."),
        ],
        "mutating": False,
        "description": (
            "Find user id by first name, last name, and zip code. If the user is not found, the function "
            "will return an error message. By default, find user id by email, and only call this function "
            "if the user is not found by email or cannot remember email."
        ),
    },
    "get_order_details": {
        "params": [("order_id", "str", "The order id, such as '#W0000000'. Be careful there is a '#' symbol at the beginning of the order id.")],
        "mutating": False,
        "description": "Get the status and details of an order.",
    },
    "cancel_pending_order": {
        "params": [
            ("order_id", "str", "The order id, such as '#W0000000'. Be careful there is a '#' symbol at the beginning of the order id."),
            ("reason", "str", "The reason for cancellation, which should be either 'no longer needed' or 'ordered by mistake'."),
        ],
        "mutating": True,
        "description": (
            "Cancel a pending order. If the order is already processed or delivered, it cannot be cancelled. "
            "The agent needs to explain the cancellation detail and ask for explicit user confirmation (yes/no) "
            "to proceed. If the user confirms, the order status will be changed to 'cancelled' and the payment "
            "will be refunded."
        ),
    },
    "return_delivered_order_items": {
        "params": [
            ("order_id", "str", "The order id, such as '#W0000000'. Be careful there is a '#' symbol at the beginning of the order id."),
            ("item_ids", "list[str]", "The item ids to be returned, each such as '1008292230'. There could be duplicate items in the list."),
            ("payment_method_id", "str", "The payment method id to receive the refund, such as 'gift_card_0000000' or 'credit_card_0000000'. These can be looked up from the user or order details."),
        ],
        "mutating": True,
        "description": (
            "Return some items of a delivered order. The order status will be changed to 'return requested'. "
            "The agent needs to explain the return detail and ask for explicit user confirmation (yes/no) to "
            "proceed. The user will receive follow-up email for how and where to return the item."
        ),
    },
}

MUTATING_TOOLS = tuple(n for n, s in TOOL_SPECS.items() if s["mutating"])


@lru_cache(maxsize=1)
def _seed_json() -> str:
    return SEED_DB_PATH.read_text(encoding="utf-8")


def _norm(s: str) -> str:
    return " ".join(str(s).split()).casefold()


class RetailWorld:
    """Implements ablate.world.base.World."""

    def __init__(self, seed: int = 0) -> None:
        self.db: RetailDB
        self.effect_log: list[dict] = []
        self.call_log: list[dict] = []
        self.overrides: dict[str, Callable[..., Any]] = {}
        self.reset(seed)

    # -- protocol ---------------------------------------------------------
    def reset(self, seed: int) -> None:
        self.seed = seed
        self.db = RetailDB.model_validate(json.loads(_seed_json()))
        self.effect_log = []
        self.call_log = []

    def snapshot(self) -> dict:
        return self.db.model_dump(mode="json")

    def state_hash(self) -> str:
        return sha256_json(self.snapshot())

    def tool_names(self) -> list[str]:
        return list(TOOL_SPECS)

    def call(self, tool: str, args: dict | None, *, turn: int = 0) -> dict:
        args = copy.deepcopy(args or {})
        entry = {"tool": tool, "args": args, "turn": turn}
        spec = TOOL_SPECS.get(tool)
        if spec is None:
            return self.block(tool, args, turn=turn)
        names = [p[0] for p in spec["params"]]
        missing = [n for n in names if n not in args]
        extra = [k for k in args if k not in names]
        if missing or extra:
            obs = {"error": f"bad arguments for {tool}: missing={missing} unexpected={extra}"}
            entry.update(observation=obs, ok=False)
            self.call_log.append(entry)
            return obs
        override = self.overrides.get(tool)
        try:
            result = override(self, **args) if override else getattr(self, f"_t_{tool}")(**args)
            obs = {"result": result}
            ok = True
        except (ValueError, TypeError, KeyError) as e:
            obs = {"error": str(e)}
            ok = False
        entry.update(observation=copy.deepcopy(obs), ok=ok)
        self.call_log.append(entry)
        return obs

    def block(self, tool: str, args: dict | None, *, turn: int = 0) -> dict:
        """Fail closed: log the attempt, change nothing, return an error."""
        obs = {"error": f"unknown tool '{tool}': blocked (fail closed)"}
        self.call_log.append({"tool": tool, "args": copy.deepcopy(args or {}), "turn": turn,
                              "observation": obs, "ok": False, "blocked": True})
        return obs

    # -- helpers (from tau2) ----------------------------------------------
    def _get_order(self, order_id: str) -> Order:
        if order_id not in self.db.orders:
            raise ValueError("Order not found")
        return self.db.orders[order_id]

    def _get_user(self, user_id: str) -> User:
        if user_id not in self.db.users:
            raise ValueError("User not found")
        return self.db.users[user_id]

    def _get_payment_method(self, user_id: str, payment_method_id: str):
        user = self._get_user(user_id)
        if payment_method_id not in user.payment_methods:
            raise ValueError("Payment method not found")
        return user.payment_methods[payment_method_id]

    def _effect(self, tool: str, args: dict, order: Order, amount_usd: float, before: str) -> None:
        self.effect_log.append({
            "tool": tool,
            "args": copy.deepcopy(args),
            "amount_inr": round(amount_usd * USD_TO_INR, 2),
            "order_id": order.order_id,
            "user_id": order.user_id,
            "before_status": before,
            "after_status": order.status,
            "seq": len(self.call_log),
        })

    # -- tools (bodies adapted from tau2) ------------------------------------
    def _t_find_user_id_by_email(self, email: str) -> str:
        wanted = _norm(email)
        matches = [uid for uid, u in self.db.users.items() if _norm(u.email) == wanted]
        if len(matches) == 1:
            return matches[0]
        if matches:
            raise ValueError("User lookup is ambiguous")
        raise ValueError("User not found")

    def _t_find_user_id_by_name_zip(self, first_name: str, last_name: str, zip: str) -> str:
        wf, wl = _norm(first_name), _norm(last_name)
        matches = [
            uid for uid, u in self.db.users.items()
            if _norm(u.name.first_name) == wf and _norm(u.name.last_name) == wl and u.address.zip == str(zip).strip()
        ]
        if len(matches) == 1:
            return matches[0]
        if matches:
            raise ValueError("User lookup is ambiguous")
        raise ValueError("User not found")

    def _t_get_order_details(self, order_id: str) -> dict:
        return self._get_order(order_id).model_dump(mode="json")

    def _t_cancel_pending_order(self, order_id: str, reason: str) -> dict:
        order = self._get_order(order_id)
        if order.status != "pending":
            raise ValueError("Non-pending order cannot be cancelled")
        if reason not in {"no longer needed", "ordered by mistake"}:
            raise ValueError("Invalid reason")
        before = order.status
        refunds = []
        total = 0.0
        for payment in order.payment_history:
            if payment.transaction_type != "payment":
                continue
            total += payment.amount
            refunds.append(OrderPayment(transaction_type="refund", amount=payment.amount,
                                        payment_method_id=payment.payment_method_id))
            pm = self._get_payment_method(order.user_id, payment.payment_method_id)
            if isinstance(pm, GiftCard):
                pm.balance = round(pm.balance + payment.amount, 2)
        order.status = "cancelled"
        order.cancel_reason = reason
        order.payment_history.extend(refunds)
        self._effect("cancel_pending_order", {"order_id": order_id, "reason": reason}, order, total, before)
        return order.model_dump(mode="json")

    def _t_return_delivered_order_items(self, order_id: str, item_ids: list, payment_method_id: str) -> dict:
        order = self._get_order(order_id)
        if order.status != "delivered":
            raise ValueError("Non-delivered order cannot be returned")
        if not isinstance(item_ids, list) or not item_ids:
            raise ValueError("item_ids must be a non-empty list")
        pm = self._get_payment_method(order.user_id, payment_method_id)
        if not isinstance(pm, GiftCard) and payment_method_id != order.payment_history[0].payment_method_id:
            raise ValueError("Payment method should be the original payment method")
        all_ids = [it.item_id for it in order.items]
        for iid in item_ids:
            if item_ids.count(iid) > all_ids.count(iid):
                raise ValueError("Some item not found")
        before = order.status
        amount = 0.0
        remaining = list(order.items)
        for iid in item_ids:
            for it in remaining:
                if it.item_id == iid:
                    amount += it.price
                    remaining.remove(it)
                    break
        order.status = "return requested"
        order.return_items = sorted(item_ids)
        order.return_payment_method_id = payment_method_id
        self._effect("return_delivered_order_items",
                     {"order_id": order_id, "item_ids": list(item_ids), "payment_method_id": payment_method_id},
                     order, amount, before)
        return order.model_dump(mode="json")
