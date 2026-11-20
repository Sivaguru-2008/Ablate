"""Cloud Run Job entrypoint: run the release gate once and upload the report
to Cloud Storage.

    python -m ablate.cloud_job

Configuration is by environment variable (Cloud Run Job `--set-env-vars`):

  ABLATE_BUCKET     required. "my-bucket", "gs://my-bucket/prefix", or
                    "file:///some/dir" (local testing, no Cloud Storage)
  ABLATE_AGENT      scripted (default) | adk
  ABLATE_MODEL      optional model override for both configs (adk agent)
  ABLATE_K          trials per scenario per arm (default: design constant 5)
  ABLATE_RPM        max model requests per minute (adk agent)
  ABLATE_BASE / ABLATE_CAND / ABLATE_SCENARIOS
                    paths inside the image (default: the bundled demo files)
  GOOGLE_API_KEY    for the adk agent; mount it from Secret Manager

The run directory and cache live under the container's temp space, which is
discarded when the task ends: Cloud Storage is the only output. The job exits
0 whatever the decision (BLOCK is a result, not a crash), so Cloud Run does not
retry a finished gate; the decision is in verdict.json and the job log.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import PROJECT_DIR, differ

UPLOADS = ("pr_comment.md", "report.html", "verdict.json", "plan.json", "preflight.json", "results.jsonl")
CONTENT_TYPES = {".md": "text/markdown; charset=utf-8", ".html": "text/html; charset=utf-8",
                 ".json": "application/json", ".jsonl": "application/x-ndjson"}


def parse_destination(dest: str) -> tuple[str, str, str]:
    """-> (scheme, bucket_or_dir, prefix). Accepts bucket, gs://bucket/prefix, file:///dir."""
    if dest.startswith("file://"):
        path = unquote(urlparse(dest).path)
        if os.name == "nt" and path.startswith("/") and len(path) > 2 and path[2] == ":":
            path = path[1:]  # file:///D:/dir -> D:/dir
        return "file", path, ""
    if dest.startswith("gs://"):
        rest = dest[len("gs://"):]
    else:
        rest = dest
    bucket, _, prefix = rest.partition("/")
    if not bucket:
        raise ValueError(f"no bucket in ABLATE_BUCKET={dest!r}")
    return "gs", bucket, prefix.strip("/")


def upload(run_dir: Path, dest: str, run_id: str) -> list[str]:
    scheme, root, prefix = parse_destination(dest)
    base = "/".join(p for p in (prefix, "ablate-runs", run_id) if p)
    files = [run_dir / name for name in UPLOADS if (run_dir / name).exists()]
    urls = []
    if scheme == "file":
        out = Path(root) / base
        out.mkdir(parents=True, exist_ok=True)
        for f in files:
            (out / f.name).write_bytes(f.read_bytes())
            urls.append(str(out / f.name))
        (Path(root) / "/".join(p for p in (prefix, "ablate-runs") if p) / "LATEST").write_text(run_id, encoding="utf-8")
        return urls
    from google.cloud import storage  # optional extra: `uv sync --extra cloud`

    client = storage.Client()
    bucket = client.bucket(root)
    for f in files:
        blob = bucket.blob(f"{base}/{f.name}")
        blob.upload_from_filename(str(f), content_type=CONTENT_TYPES.get(f.suffix, "application/octet-stream"))
        urls.append(f"gs://{root}/{base}/{f.name}")
    latest = "/".join(p for p in (prefix, "ablate-runs", "LATEST") if p)
    bucket.blob(latest).upload_from_string(run_id, content_type="text/plain")
    return urls


CACHED_INPUTS = ("plan.json", "results.jsonl", "preflight.json", "scenarios.yaml")


def fetch_cached_run(src: str, run_dir: Path) -> None:
    """Copy a recorded run (plan.json + results.jsonl) from gs://, file://, or local path into run_dir."""
    run_dir.mkdir(parents=True, exist_ok=True)
    if src.startswith("gs://"):
        _, bucket_name, prefix = parse_destination(src)
        from google.cloud import storage

        client = storage.Client()
        bucket = client.bucket(bucket_name)
        for name in CACHED_INPUTS:
            blob_path = f"{prefix}/{name}" if prefix else name
            blob = bucket.blob(blob_path)
            if blob.exists():
                blob.download_to_filename(str(run_dir / name))
            elif name in ("plan.json", "results.jsonl"):
                raise FileNotFoundError(f"missing gs://{bucket_name}/{blob_path}")
        return
    if src.startswith("file://"):
        _, root, _ = parse_destination(src)
        src_dir = Path(root)
    else:
        src_dir = Path(src)
    for name in CACHED_INPUTS:
        f = src_dir / name
        if f.exists():
            (run_dir / name).write_bytes(f.read_bytes())
        elif name in ("plan.json", "results.jsonl"):
            raise FileNotFoundError(f"missing {f}")


def main(env: dict | None = None) -> int:
    from .pipeline import finish, run_check
    from .plan import load
    from .results import read_jsonl
    from .verdict import verdict

    env = dict(os.environ if env is None else env)
    dest = env.get("ABLATE_BUCKET")
    if not dest:
        print("ABLATE_BUCKET is not set", file=sys.stderr)
        return 2
    work = Path(env.get("ABLATE_WORKDIR") or tempfile.mkdtemp(prefix="ablate-job-"))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = work / "run"

    cached_src = env.get("ABLATE_CACHED_RUN")
    if cached_src:
        fetch_cached_run(cached_src, run_dir)
        plan = load(run_dir)
        results = read_jsonl(run_dir / "results.jsonl")
        out = finish(run_dir, plan, verdict(plan, results), results)
        api_calls_made = 0
    else:
        agent = env.get("ABLATE_AGENT", "scripted")
        base = differ.load_config(env.get("ABLATE_BASE", PROJECT_DIR / "configs" / "base.yaml"))
        cand = differ.load_config(env.get("ABLATE_CAND", PROJECT_DIR / "configs" / "cand.yaml"))
        if env.get("ABLATE_MODEL"):
            base["model"] = cand["model"] = env["ABLATE_MODEL"]
        scenarios = Path(env.get("ABLATE_SCENARIOS", PROJECT_DIR / "scenarios" / "retail.yaml"))
        k = int(env["ABLATE_K"]) if env.get("ABLATE_K") else None
        rpm = int(env["ABLATE_RPM"]) if env.get("ABLATE_RPM") else None
        out = run_check(base, cand, scenarios, run_dir, agent=agent, k=k, rpm=rpm, concurrency=4,
                        cache_dir=work / "cache")
        api_calls_made = sum(r.api_calls for r in out.results)

    run_id = f"{stamp}-{out.verdict.plan_hash[:12]}"
    urls = upload(run_dir, dest, run_id)
    summary = {"run_id": run_id, "decision": out.verdict.decision, "status": out.verdict.status,
               "named": out.verdict.named, "trials": len(out.results), "api_calls": api_calls_made,
               "cached_run": bool(cached_src), "uploaded": urls}
    print(json.dumps(summary, indent=2))  # one structured line block for Cloud Logging
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
