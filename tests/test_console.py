"""Web console: API over a sealed + blind-run envelope set, per-envelope reveal, wording guard."""

import json
import threading
import urllib.error
import urllib.request

import pytest

from ablate import envelopes, wording
from ablate.console import data
from ablate.console.server import STATIC, Console, make_server


@pytest.fixture(scope="module")
def site(tmp_path_factory, root):
    d = tmp_path_factory.mktemp("console")
    env, sealed = d / "envelopes", d / ".sealed"
    envelopes.seal(env, sealed, count=4)
    envelopes.run(env, root / "scenarios" / "retail.yaml")
    srv = make_server(Console(env, sealed, root / "runs"), port=0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", env, sealed
    srv.shutdown()
    srv.server_close()


def get(url):
    with urllib.request.urlopen(url) as r:
        return r.status, r.read()


def post(url, header=True):
    req = urllib.request.Request(url, method="POST", headers={"X-Ablate": "1"} if header else {})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def test_page_wording_is_clean():
    text = (STATIC / "index.html").read_text(encoding="utf-8")
    assert wording.violations(text) == []


def test_assurance_is_blind_until_opened(site):
    base, env, _ = site
    _, body = get(base + "/api/assurance")
    a = json.loads(body)
    assert a["available"] and len(a["rows"]) == 4 and all(r["ran"] for r in a["rows"])
    assert not any(r["revealed"] for r in a["rows"])
    assert all("kind" not in r and "expected_decision" not in r for r in a["rows"])
    assert a["score"]["scored"] == 0


def test_reveal_one_then_score(site):
    base, env, _ = site
    with pytest.raises(urllib.error.HTTPError) as e:
        post(base + "/api/envelopes/E01/reveal", header=False)
    assert e.value.code == 403
    row = post(base + "/api/envelopes/E01/reveal")
    assert row["hash_ok"] and row["correct"]
    a = json.loads(get(base + "/api/assurance")[1])
    opened = [r for r in a["rows"] if r["revealed"]]
    assert [r["release"] for r in opened] == ["E01"]
    assert a["score"]["scored"] == 1 and a["score"]["false_attribution"] == 0
    assert not (env / "reveal.json").exists()


def test_reveal_all_and_files(site):
    base, env, _ = site
    out = post(base + "/api/envelopes/reveal-all")
    assert out["hashes_ok"] and out["correct"] == out["total"] == 4
    a = json.loads(get(base + "/api/assurance")[1])
    assert a["fully_revealed"] and all(r["revealed"] for r in a["rows"])
    s = a["score"]
    assert s["correct_attribution"] + s["correct_abstention"] == 4
    assert get(base + "/files/envelopes/hashes.json")[0] == 200
    assert get(base + "/files/envelopes/E01/report.html")[0] == 200
    with pytest.raises(urllib.error.HTTPError):
        get(base + "/files/envelopes/..%2F.sealed/labels.json")


def test_upper_bound():
    assert data.upper_bound(0, 5) == pytest.approx(1 - 0.05 ** (1 / 5), abs=1e-6)
    assert data.upper_bound(0, 0) is None
    assert 0.0 < data.upper_bound(1, 13) < 1.0
