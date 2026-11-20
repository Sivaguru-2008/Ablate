"""`ablate console`: a small stdlib HTTP server for the web console. Binds to localhost only.

Routes
  GET  /                                  the single-page console (static/index.html)
  GET  /api/assurance                     envelope commitments, blind verdicts, opened envelopes, score
  POST /api/envelopes/<id>/reveal         open one envelope (recompute its hash, score it)
  POST /api/envelopes/reveal-all          `ablate envelopes reveal` (writes reveal.json)
  GET  /api/releases                      finished `ablate check` runs under the runs directory
  GET  /files/runs/<run>/<file>           report.html, pr_comment.md, verdict.json, ... of a run
  GET  /files/envelopes/hashes.json       the public commitments
  GET  /files/envelopes/<id>/<file>       blind-run report of one envelope

POSTs require the header `X-Ablate: 1`, so a cross-site form cannot trigger them.
"""

from __future__ import annotations

import json
import re
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .. import envelopes
from . import data

STATIC = Path(__file__).resolve().parent / "static"
NAME = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
CTYPES = {".html": "text/html; charset=utf-8", ".md": "text/markdown; charset=utf-8",
          ".json": "application/json; charset=utf-8"}


class Console:
    def __init__(self, env_dir: Path, sealed_dir: Path, runs_dir: Path):
        self.env_dir, self.sealed_dir, self.runs_dir = Path(env_dir), Path(sealed_dir), Path(runs_dir)
        self.revealed: set[str] = set()
        self.lock = threading.Lock()

    def handler(self):
        console = self

        class H(BaseHTTPRequestHandler):
            server_version = "ablate-console"

            def log_message(self, fmt, *args):  # quiet by default
                pass

            def _send(self, status, body: bytes, ctype: str):
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)

            def _json(self, obj, status=HTTPStatus.OK):
                self._send(status, json.dumps(obj).encode("utf-8"), CTYPES[".json"])

            def _err(self, status, msg):
                self._json({"error": msg}, status)

            def _file(self, p: Path):
                if not p.is_file():
                    return self._err(HTTPStatus.NOT_FOUND, "not found")
                self._send(HTTPStatus.OK, p.read_bytes(), CTYPES.get(p.suffix, "application/octet-stream"))

            def do_GET(self):
                path = self.path.split("?", 1)[0]
                parts = [p for p in path.split("/") if p]
                if path in ("/", "/index.html"):
                    return self._file(STATIC / "index.html")
                if path == "/api/assurance":
                    with console.lock:
                        return self._json(data.assurance(console.env_dir, console.sealed_dir, console.revealed))
                if path == "/api/releases":
                    return self._json({"runs_dir": str(console.runs_dir), "releases": data.releases(console.runs_dir)})
                if parts[:2] == ["files", "runs"] and len(parts) == 4 and NAME.match(parts[2]) \
                        and parts[3] in data.RUN_FILES:
                    return self._file(console.runs_dir / parts[2] / parts[3])
                if parts == ["files", "envelopes", "hashes.json"]:
                    return self._file(console.env_dir / "hashes.json")
                if parts[:2] == ["files", "envelopes"] and len(parts) == 4 and NAME.match(parts[2]) \
                        and parts[3] in data.RUN_FILES:
                    return self._file(console.env_dir / "results" / parts[2] / parts[3])
                return self._err(HTTPStatus.NOT_FOUND, "not found")

            def do_POST(self):
                if self.headers.get("X-Ablate") != "1":
                    return self._err(HTTPStatus.FORBIDDEN, "missing X-Ablate header")
                parts = [p for p in self.path.split("?", 1)[0].split("/") if p]
                if not (console.sealed_dir / "labels.json").exists():
                    return self._err(HTTPStatus.CONFLICT, f"no sealed labels in {console.sealed_dir}")
                with console.lock:
                    try:
                        if parts == ["api", "envelopes", "reveal-all"]:
                            out = envelopes.reveal(console.env_dir, console.sealed_dir)
                            return self._json({"hashes_ok": out["hashes_ok"], "correct": out["correct"],
                                               "total": out["total"]})
                        if len(parts) == 4 and parts[:2] == ["api", "envelopes"] and parts[3] == "reveal" \
                                and NAME.match(parts[2]):
                            row = envelopes.reveal_one(console.env_dir, console.sealed_dir, parts[2])
                            console.revealed.add(parts[2])
                            return self._json(row)
                    except KeyError:
                        return self._err(HTTPStatus.NOT_FOUND, "no such envelope")
                    except FileNotFoundError as e:
                        return self._err(HTTPStatus.CONFLICT, str(e))
                return self._err(HTTPStatus.NOT_FOUND, "not found")

        return H


def make_server(console: Console, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), console.handler())
