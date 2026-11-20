"""Step 3: differ + preflight lint."""

import copy

from ablate import differ, preflight
from ablate.scenarios import load_scenarios
from ablate.verdict import INCOMPATIBLE, verdict


def cfgs(root):
    return differ.load_config(root / "configs" / "base.yaml"), differ.load_config(root / "configs" / "cand.yaml")


def gemini25(cfg):
    """Lint tests pin a Gemini 2.5 model: thinking_level is a Gemini 3 setting."""
    cfg = copy.deepcopy(cfg)
    cfg["model"] = "gemini-2.5-flash-lite"
    return cfg


def scen(root):
    return load_scenarios(root / "scenarios" / "retail.yaml")


def test_units_and_demo_arms(root):
    base, cand = cfgs(root)
    changed = [u.id for u in differ.changed_units(base, cand)]
    assert changed == ["block.confirm", "block.role", "gen.temperature"]
    pf = preflight.build(base, cand, scen(root))
    assert pf.plan.revertible == ("block.confirm", "block.role", "gen.temperature")
    assert len(pf.plan.arms) == 9 and pf.plan.n_trials == 1080
    cm = pf.configs["cand-block.confirm"]
    assert next(b for b in cm["blocks"] if b["id"] == "confirm")["text"] == next(
        b for b in base["blocks"] if b["id"] == "confirm")["text"]
    assert cm["generation_config"]["temperature"] == 0.3
    bp = pf.configs["base+gen.temperature"]
    assert bp["generation_config"]["temperature"] == 0.3
    assert [b["id"] for b in bp["blocks"]] == [b["id"] for b in base["blocks"]]


def test_thinking_level_rule_fires_incompatible(root):
    base, _ = cfgs(root)
    base = gemini25(base)
    cand = copy.deepcopy(base)
    cand["generation_config"]["thinking_level"] = "low"  # Gemini 2.5 model
    issues = preflight.lint(cand)
    assert any(i.rule == "thinking-level-unsupported" and i.severity == "INCOMPATIBLE" for i in issues)
    pf = preflight.build(base, cand, scen(root))
    assert pf.plan.release_status == INCOMPATIBLE
    v = verdict(pf.plan, [])
    assert v.status == INCOMPATIBLE


def test_unit_whose_arm_is_rejected_is_incompatible(root):
    base = gemini25(cfgs(root)[0])
    cand = copy.deepcopy(base)
    cand["model"] = "gemini-3-pro-preview"
    cand["generation_config"]["thinking_level"] = "low"
    pf = preflight.build(base, cand, scen(root))
    st = {u.id: u.status for u in pf.plan.units}
    assert pf.plan.release_status == "OK"
    # base+gen.thinking_level = Gemini 2.5 + thinking_level -> rejected
    assert st["gen.thinking_level"] == "INCOMPATIBLE"
    # cand-model = Gemini 2.5 + thinking_level -> rejected
    assert st["model"] == "INCOMPATIBLE"


def test_other_lint_rules(root):
    base, _ = cfgs(root)
    for gen, rule in [({"thinking_budget": 0}, "thinking-budget-pro-off"), ({"temperature": 3}, "temperature-range"),
                      ({"bogus": 1}, "gen-unknown-key")]:
        c = copy.deepcopy(base)
        c["model"] = "gemini-2.5-pro"
        c["generation_config"].update(gen)
        assert any(i.rule == rule and i.severity == "INCOMPATIBLE" for i in preflight.lint(c)), rule
    c = copy.deepcopy(base)
    c["model"] = "gemini-3-pro-preview"
    c["generation_config"].update(thinking_level="high", thinking_budget=128)
    assert any(i.rule == "thinking-both" for i in preflight.lint(c))
    assert not [i for i in preflight.lint(base) if i.severity == "INCOMPATIBLE"]


def test_non_closed_arms_are_not_revertible(root):
    base, _ = cfgs(root)
    cand = copy.deepcopy(base)
    blk = next(b for b in cand["blocks"] if b["id"] == "cancel")
    blk["text"] += " Use the new 'reason' wording described in the tool."
    blk["requires"] = ["tool.cancel_pending_order"]
    cand["tools"]["cancel_pending_order"]["description"] += " Reasons are case-sensitive."
    cand["generation_config"]["temperature"] = 0.1
    cand = differ.normalize(cand)
    pf = preflight.build(base, cand, scen(root))
    st = {u.id: (u.status, u.reason) for u in pf.plan.units}
    assert st["block.cancel"][0] == "NOT_REVERTIBLE"
    assert st["tool.cancel_pending_order"][0] == "NOT_REVERTIBLE"
    assert st["gen.temperature"][0] == "REVERTIBLE"
    assert "requires tool.cancel_pending_order" in st["block.cancel"][1]
    assert pf.plan.revertible == ("gen.temperature",)
    assert len(pf.plan.arms) == 5


def test_budget_cap(root):
    base, cand = cfgs(root)
    pf = preflight.build(base, cand, scen(root), agent="adk", budget_usd=0.5)
    assert pf.est_cost_usd > 0.5
    assert preflight.over_budget(pf, 0.5)
    assert pf.floor == 7
    assert any("floor" in w for w in pf.warnings)


def test_plan_hash_changes_with_design_constants(root):
    base, cand = cfgs(root)
    a = preflight.build(base, cand, scen(root)).plan
    b = preflight.build(base, cand, scen(root), k=6).plan
    assert a.plan_hash != b.plan_hash
    assert a.plan_hash == preflight.build(base, cand, scen(root)).plan.plan_hash


def test_cli_preflight_prints_incompatible_without_banned_words(root, tmp_path, capsys):
    import yaml

    from ablate import wording
    from ablate.cli import main
    cand = gemini25(differ.load_config(root / "configs" / "base.yaml"))
    cand["generation_config"]["thinking_level"] = "low"
    p = tmp_path / "cand.yaml"
    p.write_text(yaml.safe_dump(cand), encoding="utf-8")
    code = main(["preflight", "--cand", str(p), "--no-mde"])
    out = capsys.readouterr().out
    assert code == 5 and "INCOMPATIBLE" in out
    assert wording.violations(out) == []
