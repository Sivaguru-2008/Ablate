"""Disk cache. Two levels:

* trials: keyed by (scenario, arm, trial, plan_hash) -> finished TrialResult.
* calls:  keyed by (trial key, call index, request hash) -> one model
  response. A trial killed half-way replays its finished model calls from
  here, so a resumed run never re-spends quota.

Writes are atomic (temp file + os.replace), so a kill cannot leave a torn
entry. The cache refuses to live under a `.sealed` directory (rule 5).
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path


def _h(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def assert_not_sealed(path: Path) -> None:
    if ".sealed" in Path(path).resolve().parts:
        raise PermissionError(f"refusing to touch sealed envelope storage: {path}")


class DiskCache:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        assert_not_sealed(self.root)
        (self.root / "trials").mkdir(parents=True, exist_ok=True)
        (self.root / "calls").mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    # -- keys -------------------------------------------------------------
    @staticmethod
    def trial_key(scenario: str, arm: str, trial: int, plan_hash: str) -> str:
        return _h("trial", scenario, arm, str(trial), plan_hash)

    @staticmethod
    def call_key(trial_key: str, index: int, request_digest: str) -> str:
        return _h("call", trial_key, str(index), request_digest)

    # -- io ---------------------------------------------------------------
    def _path(self, kind: str, key: str) -> Path:
        return self.root / kind / key[:2] / f"{key}.json"

    def _get(self, kind: str, key: str):
        p = self._path(kind, key)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    def _put(self, kind: str, key: str, value) -> None:
        p = self._path(kind, key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
        os.replace(tmp, p)

    def get_trial(self, key: str):
        return self._get("trials", key)

    def put_trial(self, key: str, value: dict) -> None:
        self._put("trials", key, value)

    def get_call(self, key: str):
        v = self._get("calls", key)
        with self._lock:
            if v is None:
                self.misses += 1
            else:
                self.hits += 1
        return v

    def put_call(self, key: str, value: dict) -> None:
        self._put("calls", key, value)
