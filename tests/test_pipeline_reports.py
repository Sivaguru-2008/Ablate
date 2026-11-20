"""Steps 2 and 6 on the scripted agent (no API key): culprit CONFIRMED, decoys
not named, null abstains; PR comment BLOCK with rupees; generated pytest red
on cand, green on base; fidelity replays exactly."""

import copy
import subprocess
import sys

import pytest

from ablate import differ, wording
from ablate.envelopes import DECOYS
from ablate.pipeline import run_check


@pytest.fixture(scope="module")
def demo(root, tmp_path_factory):
    d = tmp_path_factory.mktemp("demo")
    out = run_check(differ.load_config(root / "configs" / "base.yaml"), differ.load_config(root / "configs" / "cand.yaml"),
                    root / "scenarios" / "retail.yaml", d / "run", cache_dir=d / "cache")
    return out


def test_planted_culprit_confirmed_decoys_not_named(demo):
    v = demo.verdict
    st = {u.unit: u.status for u in v.units}
    assert v.decision == "BLOCK"
    assert st["block.confirm"] == "CONFIRMED"
    assert v.named == ["block.confirm"]
    assert sum(r.api_calls for r in demo.results) == 0


def test_null_release_abstains(root, tmp_path):
    base = differ.load_config(root / "configs" / "base.yaml")
    cand = copy.deepcopy(base)
    for d in ("block.role", "gen.temperature", "tool.get_order_details"):
        DECOYS[d](cand)
    out = run_check(base, differ.normalize(cand), root / "scenarios" / "retail.yaml", tmp_path / "run",
                    cache_dir=tmp_path / "cache")
    assert out.verdict.named == []
    assert out.verdict.decision in ("APPROVE", "NEEDS MORE EVIDENCE")
    assert out.verdict.status == "NOT_REPRODUCED"


def test_pr_comment_block_with_rupees(demo):
    md = demo.pr_markdown
    assert "BLOCK" in md and "₹" in md and "per 1,000 conversations" in md and "under this scenario mix" in md
    assert "scripted" in md.lower() and "constructed fault" in md.lower()
    assert wording.violations(md, pr=True) == []
    html = (demo.run_dir / "report.html").read_text(encoding="utf-8")
    assert "scripted" in html.lower() and "constructed fault" in html.lower()
    assert wording.violations(html, pr=True) == []


def test_wording_guard():
    for bad in ("proven", "a proof", "the first", "only this", "certified", "root cause", "LIKELY"):
        assert wording.violations(bad, pr=True), bad
    assert wording.violations("firstly fine? onlyness", pr=True) == []


def test_fidelity_replays_exactly(demo, capsys):
    from ablate.cli import main
    from ablate.fidelity import check_results
    rep = check_results(demo.results[:200])
    assert rep["ok"] and rep["checked"] == 200
    rc = main(["fidelity", "--run", str(demo.run_dir), "--arm", "base"])
    out = capsys.readouterr().out
    assert rc == 0 and "replays recorded runs exactly (" in out and "matches production" not in out.lower()


def test_generated_pytest_red_on_cand_green_on_base(demo, tmp_path):
    from ablate.report.pytest_gen import generate
    out = tmp_path / "gen"
    cand = generate(demo.run_dir, "cand", out)
    base = generate(demo.run_dir, "base", out)
    run = lambda p: subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(p)],  # noqa: E731
                                   capture_output=True, text=True, cwd=out)
    rc, rb = run(cand), run(base)
    assert rc.returncode != 0, rc.stdout[-2000:]
    assert rb.returncode == 0, rb.stdout[-2000:]


def test_frozen_plan_cannot_be_overwritten(demo):
    from ablate.plan import freeze, load
    plan = load(demo.run_dir)
    assert freeze(plan, demo.run_dir) == plan.plan_hash
    changed = plan.__class__(**{**plan.__dict__, "k": 7})
    with pytest.raises(RuntimeError):
        freeze(changed, demo.run_dir)
