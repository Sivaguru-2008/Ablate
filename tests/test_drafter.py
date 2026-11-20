"""Step 8 offline: the fidelity gate accepts a faithful draft of
get_order_details and rejects wrong or unsafe ones. (Gemini writes the draft
in the live path; here the code is supplied directly.)"""

import asyncio

import pytest

from ablate import differ
from ablate.agents.scripted_agent import ScriptedAgent
from ablate.drafter import UnsafeDraft, compile_draft, draft_and_gate
from ablate.plan import ArmSpec, Plan
from ablate.runner.cache import DiskCache
from ablate.runner.scheduler import run_plan
from ablate.scenarios import load_scenarios

GOOD = '''
def get_order_details(world, order_id):
    if order_id not in world.db.orders:
        raise ValueError("Order not found")
    return world.db.orders[order_id].model_dump(mode="json")
'''
WRONG = '''
def get_order_details(world, order_id):
    o = world.db.orders[order_id].model_dump(mode="json")
    o["status"] = "pending"
    return o
'''
UNSAFE = '''
import os
def get_order_details(world, order_id):
    return {}
'''


@pytest.fixture(scope="module")
def results(root, tmp_path_factory):
    cfg = differ.load_config(root / "configs" / "base.yaml")
    scen = load_scenarios(root / "scenarios" / "retail.yaml")
    plan = Plan(scenarios=tuple(s["id"] for s in scen), units=(), arms=(ArmSpec("base", "base", None, "b"),), k=1)
    d = tmp_path_factory.mktemp("draft")
    return asyncio.run(run_plan(plan, {"base": cfg}, {s["id"]: s for s in scen}, ScriptedAgent(), DiskCache(d)))


def test_faithful_draft_accepted(results, tmp_path):
    out = draft_and_gate(results, code=GOOD, out_dir=tmp_path)
    assert out["accepted"] and out["gate"]["checked"] > 10


def test_wrong_draft_rejected(results):
    assert not draft_and_gate(results, code=WRONG)["accepted"]


def test_unsafe_draft_rejected(results):
    with pytest.raises(UnsafeDraft):
        compile_draft(UNSAFE, "get_order_details")
    assert not draft_and_gate(results, code=UNSAFE)["accepted"]


def test_draft_saves_raw_response_and_model_version(results, tmp_path):
    import json
    meta = {"model_requested": "gemini-3.8-flash", "model_version": "gemini-3.8-flash-001", "raw_response": {"text": "{}"}}
    out = draft_and_gate(results, code=GOOD, meta=meta, out_dir=tmp_path)
    assert out["accepted"] and out["model_version"] == "gemini-3.8-flash-001"
    raw_file = tmp_path / "get_order_details.raw.json"
    assert raw_file.exists() and out["raw_path"] == str(raw_file)
    saved = json.loads(raw_file.read_text(encoding="utf-8"))
    assert saved["model_version"] == "gemini-3.8-flash-001" and "raw_response" in saved
