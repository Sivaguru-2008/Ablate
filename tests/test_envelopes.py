"""Steps 2 and 7: seal writes hashes.json; the blind run never reads .sealed;
reveal recomputes every hash and matches hashes.json; tampering is caught."""

import builtins
import json
import pathlib

import pytest

from ablate import envelopes


@pytest.fixture(scope="module")
def sealed(tmp_path_factory):
    d = tmp_path_factory.mktemp("env")
    meta = envelopes.seal(d / "envelopes", d / ".sealed", count=4)
    return d, meta


def test_seal_writes_hashes_only_in_public_dir(sealed):
    d, meta = sealed
    h = json.loads((d / "envelopes" / "hashes.json").read_text(encoding="utf-8"))
    assert len(h["envelopes"]) == 4 and all(len(x) == 64 for x in h["envelopes"].values())
    public = "".join(p.read_text(encoding="utf-8") for p in (d / "envelopes").rglob("*") if p.is_file())
    labels = json.loads((d / ".sealed" / "labels.json").read_text(encoding="utf-8"))
    for v in labels.values():
        assert v["salt"] not in public
        assert v["label"]["kind"] not in json.loads((d / "envelopes" / "hashes.json").read_text())["envelopes"]


def test_blind_run_then_reveal(sealed, root, monkeypatch):
    d, _ = sealed
    real_open, real_read = builtins.open, pathlib.Path.read_text

    def guard(path):
        if ".sealed" in str(path):
            raise AssertionError(f"runner touched sealed storage: {path}")

    monkeypatch.setattr(builtins, "open", lambda f, *a, **k: (guard(f), real_open(f, *a, **k))[1])
    monkeypatch.setattr(pathlib.Path, "read_text", lambda self, *a, **k: (guard(self), real_read(self, *a, **k))[1])
    idx = envelopes.run(d / "envelopes", root / "scenarios" / "retail.yaml")
    monkeypatch.undo()
    assert len(idx["releases"]) == 4
    out = envelopes.reveal(d / "envelopes", d / ".sealed")
    assert out["hashes_ok"] and out["ordered"]
    assert out["correct"] == 4, out["rows"]


def test_tampered_label_is_detected(sealed):
    d, _ = sealed
    p = d / ".sealed" / "labels.json"
    labels = json.loads(p.read_text(encoding="utf-8"))
    rid = sorted(labels)[0]
    labels[rid]["label"]["expected_decision"] = "APPROVE" if labels[rid]["label"]["expected_decision"] != "APPROVE" else "BLOCK"
    p.write_text(json.dumps(labels), encoding="utf-8")
    out = envelopes.reveal(d / "envelopes", d / ".sealed")
    assert not out["hashes_ok"]
    assert not next(r for r in out["rows"] if r["release"] == rid)["hash_ok"]


def test_runner_refuses_sealed_paths(tmp_path):
    from ablate.runner.cache import DiskCache
    with pytest.raises(PermissionError):
        DiskCache(tmp_path / ".sealed" / "cache")


def test_seal_is_once(sealed):
    d, _ = sealed
    with pytest.raises(FileExistsError):
        envelopes.seal(d / "envelopes", d / ".sealed2", count=2)
