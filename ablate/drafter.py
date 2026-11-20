"""(Should tier) Gemini drafts one tool's world function from recorded traces.

The draft is never trusted: it is swapped into the world via `overrides` and
every recorded trial that calls the tool is replayed (fidelity.py). The draft
is accepted only if every observation and every final state hash matches
exactly. Gemini is used to *write code*, never to judge a trial.

The generated code runs with a reduced builtins table and an AST screen
(no imports, no dunder attribute access). That is a guard against accidents,
not a security sandbox; review drafts before reusing them.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

from .fidelity import check_results
from .world.retail.tools import TOOL_SPECS

SAFE_BUILTINS = {n: __builtins__[n] if isinstance(__builtins__, dict) else getattr(__builtins__, n)
                 for n in ("len", "range", "dict", "list", "str", "int", "float", "bool", "sorted", "round", "min", "max",
                           "sum", "any", "all", "enumerate", "zip", "isinstance", "ValueError", "KeyError", "TypeError")}

SCHEMA_HINT = """world.db is a pydantic RetailDB with fields:
  products: dict[str, Product]   users: dict[str, User]   orders: dict[str, Order]
Order fields: order_id, user_id, address, items[list[OrderItem]], status, fulfillments, payment_history,
  cancel_reason, exchange_items, exchange_new_items, exchange_payment_method_id, exchange_price_difference,
  return_items, return_payment_method_id
Every model has .model_dump(mode="json"). Raise ValueError("...") for errors; the world turns it into
{"error": message}."""


def _examples(results, tool: str, limit: int = 12) -> list[dict]:
    out, seen = [], set()
    for r in results:
        for c in r.trace:
            if c["tool"] != tool:
                continue
            k = json.dumps(c["args"], sort_keys=True)
            if k in seen:
                continue
            seen.add(k)
            out.append({"args": c["args"], "observation": c["observation"]})
            if len(out) >= limit:
                return out
    return out


def build_prompt(tool: str, examples: list[dict]) -> str:
    params = ", ".join(f"{p}: {t}" for p, t, _ in TOOL_SPECS[tool]["params"])
    return (
        f"Write a Python function `def {tool}(world, {params}):` for a deterministic retail simulator.\n"
        f"{SCHEMA_HINT}\n\nThe function's return value becomes {{\"result\": value}}. "
        f"Reproduce these recorded calls exactly (args -> observation):\n"
        + "\n".join(json.dumps(e, sort_keys=True)[:4000] for e in examples)
        + "\n\nReturn JSON {\"code\": \"<python source of the function>\"}. No imports."
    )


class UnsafeDraft(ValueError):
    pass


def compile_draft(code: str, tool: str):
    tree = ast.parse(code)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal)):
            raise UnsafeDraft("imports / global state are not allowed")
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise UnsafeDraft("dunder attribute access is not allowed")
        if isinstance(node, ast.Name) and node.id in ("eval", "exec", "open", "compile", "__import__", "globals", "locals"):
            raise UnsafeDraft(f"use of {node.id} is not allowed")
    ns: dict = {"__builtins__": SAFE_BUILTINS}
    exec(compile(tree, f"<draft:{tool}>", "exec"), ns)  # noqa: S102 - screened above
    fn = ns.get(tool)
    if not callable(fn):
        raise UnsafeDraft(f"draft does not define {tool}()")
    return fn


def request_draft_with_meta(tool: str, examples: list[dict], model: str) -> tuple[str, dict]:
    import random
    import time

    from google import genai
    from google.genai import errors, types

    from .agents.base import MissingCredentials, QuotaExhausted
    from .agents.recorder_plugin import have_credentials

    if not have_credentials():
        raise MissingCredentials("no GOOGLE_API_KEY / GEMINI_API_KEY set for live draft")
    client = genai.Client()  # keep a reference: an unreferenced client closes before the request
    config = types.GenerateContentConfig(
        temperature=0.0, response_mime_type="application/json",
        response_schema={"type": "OBJECT", "properties": {"code": {"type": "STRING"}}, "required": ["code"]})
    for attempt in range(6):
        try:
            resp = client.models.generate_content(model=model, contents=build_prompt(tool, examples), config=config)
            code = json.loads(resp.text)["code"]
            raw = resp.model_dump(mode="json", exclude_none=True) if hasattr(resp, "model_dump") else {"text": resp.text}
            meta = {
                "model_requested": model,
                "model_version": getattr(resp, "model_version", None),
                "raw_response": raw,
            }
            return code, meta
        except errors.APIError as e:
            text = str(e)
            if e.code == 429 and ("PerDay" in text or "per day" in text.lower() or "RequestsPerDay" in text):
                raise QuotaExhausted(f"daily quota exhausted: {text[:300]}") from None
            if e.code not in (429, 500, 503) or attempt == 5:
                if e.code == 429:
                    raise QuotaExhausted(f"rate limit / quota exhausted: {text[:300]}") from None
                raise
            time.sleep(min(60.0, 2.0 * 2 ** attempt) + random.uniform(0, 1))  # busy model: back off and retry
    raise RuntimeError("unreachable")


def request_draft(tool: str, examples: list[dict], model: str) -> str:
    code, _ = request_draft_with_meta(tool, examples, model)
    return code


def draft_and_gate(results, *, tool: str = "get_order_details", model: str = "gemini-3.8-flash",
                   out_dir: Path | None = None, code: str | None = None, meta: dict | None = None) -> dict:
    if tool not in TOOL_SPECS:
        raise ValueError(f"unknown tool {tool}")
    examples = _examples(results, tool)
    if not examples:
        raise ValueError(f"no recorded calls to {tool} in this run")
    if code is None:
        code, meta = request_draft_with_meta(tool, examples, model)
    path = None
    raw_path = None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{tool}.py"
        path.write_text(code, encoding="utf-8")
        if meta is not None:
            raw_path = out_dir / f"{tool}.raw.json"
            raw_path.write_text(json.dumps({**meta, "code": code}, indent=2, sort_keys=True), encoding="utf-8")
    model_version = meta.get("model_version") if meta else None
    try:
        fn = compile_draft(code, tool)
    except (SyntaxError, UnsafeDraft) as e:
        return {"accepted": False, "path": str(path), "raw_path": str(raw_path) if raw_path else None,
                "model_version": model_version, "gate": {"checked": 0, "passed": 0, "failures": [str(e)]}}
    gate = check_results(results, overrides={tool: fn}, tools={tool})
    return {"accepted": gate["ok"], "path": str(path), "raw_path": str(raw_path) if raw_path else None,
            "model_version": model_version, "gate": gate}
