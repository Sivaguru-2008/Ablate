"""Step 4: deterministic world + code-only contracts."""

import ast
from pathlib import Path

import pytest

from ablate.oracle import contracts
from ablate.scenarios import load_scenarios
from ablate.world.retail.tools import RetailWorld


@pytest.fixture(scope="module")
def scenarios(root):
    return {s["id"]: s for s in load_scenarios(root / "scenarios" / "retail.yaml")}


def test_seed_db_is_small(root):
    w = RetailWorld()
    assert 25 <= len(w.db.users) <= 30 and len(w.db.orders) == 60


def test_reset_and_state_hash_are_deterministic():
    a, b = RetailWorld(1), RetailWorld(2)
    assert a.state_hash() == b.state_hash()
    a.call("cancel_pending_order", {"order_id": "#W5065081", "reason": "no longer needed"})
    assert a.state_hash() != b.state_hash()
    a.reset(1)
    assert a.state_hash() == b.state_hash() and a.effect_log == []


def test_unknown_tool_and_bad_args_fail_closed():
    w = RetailWorld()
    h = w.state_hash()
    assert "error" in w.call("issue_refund", {"order_id": "#W5065081"})
    assert "error" in w.call("cancel_pending_order", {"order_id": "#W5065081"})
    assert w.state_hash() == h and w.effect_log == []
    assert w.call_log[0]["blocked"] is True


def test_effect_log_has_inr_amounts():
    w = RetailWorld()
    w.call("cancel_pending_order", {"order_id": "#W5065081", "reason": "no longer needed"})
    e = w.effect_log[0]
    assert set(e) >= {"tool", "args", "amount_inr", "order_id", "before_status", "after_status"}
    assert e["before_status"] == "pending" and e["after_status"] == "cancelled" and e["amount_inr"] > 0


def test_every_scenario_expectation_is_reachable(scenarios):
    assert len(scenarios) == 24
    for s in scenarios.values():
        contracts.expected_state(s)


def run_calls(scenario, calls, outcome):
    w = RetailWorld()
    init = w.snapshot()
    for tool, args, turn in calls:
        w.call(tool, args, turn=turn)
    return contracts.evaluate(scenario, initial=init, final=w.snapshot(), final_hash=w.state_hash(),
                              call_log=w.call_log, effect_log=w.effect_log,
                              structured_output={"outcome": outcome, "order_id": scenario["order_id"]})


def test_contracts(scenarios):
    s = scenarios["s01_cancel_email"]
    auth = ("find_user_id_by_email", {"email": s["identity"]["email"]}, 0)
    look = ("get_order_details", {"order_id": s["order_id"]}, 0)
    act = lambda t: ("cancel_pending_order", {"order_id": s["order_id"], "reason": s["reason"]}, t)  # noqa: E731
    assert run_calls(s, [auth, look, act(1)], "cancelled") == []
    kinds = {f.contract for f in run_calls(s, [auth, look, act(0)], "cancelled")}
    assert kinds == {"missing_precondition"}
    kinds = {f.contract for f in run_calls(s, [look, act(1)], "cancelled")}
    assert "missing_precondition" in kinds
    kinds = {f.contract for f in run_calls(s, [auth, look], "cancelled")}
    assert kinds == {"wrong_state_change", "false_success_claim"}
    o = scenarios["s20_adv_other_user"]
    found = run_calls(o, [("find_user_id_by_email", {"email": o["identity"]["email"]}, 0),
                          ("get_order_details", {"order_id": o["order_id"]}, 0),
                          ("cancel_pending_order", {"order_id": o["order_id"], "reason": "no longer needed"}, 1)], "cancelled")
    assert "forbidden_action" in {f.contract for f in found}
    assert contracts.amount_at_risk(found) > 0


def test_no_llm_in_oracle_or_verdict_path(root):
    banned = ("google", "genai", "adk", "openai", "anthropic", "litellm", "requests", "httpx")
    for rel in ("ablate/oracle/contracts.py", "ablate/verdict.py", "ablate/stats/tests.py", "ablate/stats/intervals.py",
                "ablate/plan.py", "ablate/world/retail/tools.py"):
        tree = ast.parse(Path(root / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] + [getattr(node, "module", None) or ""]
                assert not any(b in n for n in names for b in banned), (rel, names)


def test_verdict_engine_has_no_io(root):
    src = (root / "ablate" / "verdict.py").read_text(encoding="utf-8")
    for token in ("open(", "Path(", "print(", "time.", "random.", "os."):
        assert token not in src, token
