"""Stretch goal: the Cloud Run Job entrypoint, tested against a local
destination (file://) so no Cloud Storage access is needed."""

import json
from pathlib import Path

import pytest

from ablate import cloud_job


def test_parse_destination():
    assert cloud_job.parse_destination("my-bucket") == ("gs", "my-bucket", "")
    assert cloud_job.parse_destination("gs://b/team/gate/") == ("gs", "b", "team/gate")
    scheme, path, _ = cloud_job.parse_destination("file:///tmp/out")
    assert scheme == "file" and path.endswith("tmp/out")
    with pytest.raises(ValueError):
        cloud_job.parse_destination("gs://")


def test_missing_bucket_is_an_error():
    assert cloud_job.main({}) == 2


def test_job_runs_gate_and_uploads_report(tmp_path, capsys):
    out = tmp_path / "bucket"
    code = cloud_job.main({"ABLATE_BUCKET": out.resolve().as_uri(), "ABLATE_WORKDIR": str(tmp_path / "work")})
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["decision"] == "BLOCK" and summary["named"] == ["block.confirm"]
    run_id = (out / "ablate-runs" / "LATEST").read_text(encoding="utf-8")
    assert run_id == summary["run_id"]
    run = out / "ablate-runs" / run_id
    assert sorted(p.name for p in run.iterdir()) == sorted(cloud_job.UPLOADS)
    assert "BLOCK" in (run / "pr_comment.md").read_text(encoding="utf-8")


def test_container_files_are_present(root: Path):
    docker = (root / "Dockerfile").read_text(encoding="utf-8")
    assert "ablate.cloud_job" in docker and "--extra cloud" in docker
    ignore = (root / ".gcloudignore").read_text(encoding="utf-8")
    for p in (".venv/", ".sealed/", ".env", "runs/"):
        assert p in ignore, p
