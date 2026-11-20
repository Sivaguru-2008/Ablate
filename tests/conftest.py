import os
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
sys.path.insert(0, str(TESTS))


@pytest.fixture(scope="session")
def root() -> Path:
    return ROOT


@pytest.fixture()
def fake_key(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "fake-key-for-offline-tests")
    import fake_llm  # noqa: F401  (registers the fake model)
    return fake_llm
