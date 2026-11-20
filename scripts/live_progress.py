"""Progress of a running `ablate check`: finished trials (trial cache) and model calls made so far."""
import json
import sys
from pathlib import Path

from ablate.runner.cache import DiskCache

run = Path(sys.argv[1] if len(sys.argv) > 1 else "runs/live")
cache = Path(sys.argv[2] if len(sys.argv) > 2 else ".ablate_cache")
plan = json.loads((run / "plan.json").read_text(encoding="utf-8"))
keys = {DiskCache.trial_key(s, a["name"], t, plan["plan_hash"]) for a in plan["arms"] for s in plan["scenarios"] for t in range(plan["k"])}
done = sum((cache / "trials" / k[:2] / f"{k}.json").exists() for k in keys)
calls = sum(1 for _ in (cache / "calls").rglob("*.json"))
print(f"{done}/{len(keys)} trials finished · {calls} model calls cached")
