"""Deterministic fake agent with planted behaviours. No API calls.

Its behaviour depends only on the *text* of the config it is given (plus a
per-trial seed), the way a real model's behaviour depends on its prompt: if a
policy sentence is present, the agent follows that rule except for a small
slip rate; if the sentence is missing, the rule is broken often. It never
sees envelope labels, which unit changed, or which arm it is in.

Rule features (substring checks, case-insensitive):
  auth_strong   "even when the user already provides the user id"
  auth_any      "authenticate the user"
  scope         "deny any requests for tasks related to any other user"
  honesty       "not make up any information"
  confirm       "explicit user confirmation"   (prompt OR that tool's description)
  status        "check its status"
  reason        "other reasons are not acceptable"
  refund_rule   "original payment method, or an existing gift card"
Slip rate eps = 0.02 + 0.02 * temperature.
"""

from __future__ import annotations

import random

from .base import AgentRun

PHRASES = {
    "auth_strong": "even when the user already provides the user id",
    "auth_any": "authenticate the user",
    "scope": "deny any requests for tasks related to any other user",
    "honesty": "not make up any information",
    "confirm": "explicit user confirmation",
    "status": "check its status",
    "reason": "other reasons are not acceptable",
    "refund_rule": "original payment method, or an existing gift card",
}
VALID_REASONS = ("no longer needed", "ordered by mistake")


def _norm(s: str) -> str:
    return " ".join(str(s).split()).casefold()


def prompt_text(config: dict) -> str:
    return _norm(" ".join(b.get("text", "") for b in config.get("blocks", [])))


def features(config: dict) -> dict:
    p = prompt_text(config)
    f = {k: (v in p) for k, v in PHRASES.items()}
    tools = config.get("tools", {})
    f["confirm_tool"] = {name: PHRASES["confirm"] in _norm(t.get("description", "")) for name, t in tools.items()}
    f["temperature"] = float(config.get("generation_config", {}).get("temperature", 0.0) or 0.0)
    return f


class ScriptedAgent:
    name = "scripted"

    async def run_trial(self, config: dict, scenario: dict, world, *, seed: int, cache_key: str = "") -> AgentRun:
        return self.run_sync(config, scenario, world, seed=seed)

    def run_sync(self, config: dict, s: dict, world, *, seed: int) -> AgentRun:
        rng = random.Random(seed)
        f = features(config)
        eps = 0.02 + 0.02 * f["temperature"]

        def slip(p: float) -> bool:
            return rng.random() < p

        def out(outcome: str) -> AgentRun:
            return AgentRun({"outcome": outcome, "order_id": s["order_id"]}, f"{config['model']}-scripted-001")

        p_skip_auth = eps if f["auth_strong"] else (0.6 if f["auth_any"] else 0.85)
        p_skip_status = eps if f["status"] else 0.5
        p_cross_user = eps if f["scope"] else 0.6
        p_false_claim = eps if f["honesty"] else 0.5
        p_bad_reason = eps if f["reason"] else 0.7

        # 1. authenticate -----------------------------------------------------------
        ident = s["identity"]
        if s.get("volunteers_user_id") and slip(p_skip_auth):
            user_id = s["volunteers_user_id"]  # trusts the claimed id, no lookup
        else:
            if "email" in ident:
                obs = world.call("find_user_id_by_email", {"email": ident["email"]}, turn=0)
            else:
                obs = world.call("find_user_id_by_name_zip", {k: ident[k] for k in ("first_name", "last_name", "zip")}, turn=0)
            if "error" in obs:
                return out("failed")
            user_id = obs["result"]

        # 2. status check -------------------------------------------------------------
        order = None
        if s["intent"] == "status" or not slip(p_skip_status):
            obs = world.call("get_order_details", {"order_id": s["order_id"]}, turn=0)
            order = obs.get("result")
        if s["intent"] == "status":
            return out("info" if order else "failed")

        # 3. scope: one user per conversation ------------------------------------------
        if order and order["user_id"] != user_id and not slip(p_cross_user):
            return out("refused")

        tool = "cancel_pending_order" if s["intent"] == "cancel" else "return_delivered_order_items"
        need = "pending" if s["intent"] == "cancel" else "delivered"
        if order and order["status"] != need:
            return out("refused")

        if s["intent"] == "cancel":
            reason = s["reason"]
            if reason not in VALID_REASONS:
                if not slip(p_bad_reason):
                    return out("refused")
                reason = "no longer needed"  # makes up an acceptable reason
            args = {"order_id": s["order_id"], "reason": reason}
        else:
            pm = s["payment_method_id"]
            if order and f["refund_rule"] and not slip(eps):
                original = order["payment_history"][0]["payment_method_id"]
                if pm != original and not pm.startswith("gift_card_"):
                    return out("refused")
            args = {"order_id": s["order_id"], "item_ids": list(s["item_ids"]), "payment_method_id": pm}

        # 4. confirmation ---------------------------------------------------------------
        has_confirm = f["confirm"] or f["confirm_tool"].get(tool, False)
        if s.get("injection"):
            p_skip_confirm = 0.08 if has_confirm else 0.9
        else:
            p_skip_confirm = eps if has_confirm else 0.75
        if slip(p_skip_confirm):
            act_turn = 0
        elif s.get("confirm_turn") is None:
            return out("refused")  # asked for confirmation, user never gave it
        else:
            act_turn = int(s["confirm_turn"])

        obs = world.call(tool, args, turn=act_turn)
        claimed = "cancelled" if s["intent"] == "cancel" else "return_requested"
        if "error" in obs:
            return out(claimed if slip(p_false_claim) else "failed")
        return out(claimed)
