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


def request_draft(tool: str, examples: list[dict], model: str) -> str:
    from google import genai
    from google.genai import types

    resp = genai.Client().models.generate_content(
        model=model, contents=build_prompt(tool, examples),
        config=types.GenerateContentConfig(
            temperature=0.0, response_mime_type="application/json",
            response_schema={"type": "OBJECT", "properties": {"code": {"type": "STRING"}}, "required": ["code"]}),
    )
    return json.loads(resp.text)["code"]


def draft_and_gate(results, *, tool: str = "get_order_details", model: str = "gemini-2.5-flash",
                   out_dir: Path | None = None, code: str | None = None) -> dict:
    if tool not in TOOL_SPECS:
        raise ValueError(f"unknown tool {tool}")
    examples = _examples(results, tool)
    if not examples:
        raise ValueError(f"no recorded calls to {tool} in this run")
    if code is None:
        code = request_draft(tool, examples, model)
    path = None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{tool}.py"
        path.write_text(code, encoding="utf-8")
    try:
        fn = compile_draft(code, tool)
    except (SyntaxError, UnsafeDraft) as e:
        return {"accepted": False, "path": str(path), "gate": {"checked": 0, "passed": 0, "failures": [str(e)]}}
    gate = check_results(results, overrides={tool: fn}, tools={tool})
    return {"accepted": gate["ok"], "path": str(path), "gate": gate}
