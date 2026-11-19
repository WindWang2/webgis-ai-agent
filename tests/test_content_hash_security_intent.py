"""Preserve content IDs while allowing SHA1 on non-security/FIPS code paths.

Exercise each constructor expression in the four audited content-ID modules.
Existing domain tests cover how their results are used; this test proves the
explicit hash intent and unchanged bytes without importing heavyweight services.
"""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
PATHS = (
    "app/services/data_import/discovery.py",
    "app/services/gis_harness/goal_repair_execution/bindings.py",
    "app/services/gis_harness/goal_repair_execution/service.py",
    "app/services/storymap/brief_compiler.py",
)


def _calls():
    for path in PATHS:
        tree = ast.parse((ROOT / path).read_text())
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "hashlib" and node.func.attr == "sha1"):
                yield pytest.param(path, node, id=f"{path}:{node.lineno}")


@pytest.mark.parametrize("path,node", list(_calls()))
def test_content_hash_constructor_preserves_digest_without_security_use(path, node):
    environment = {
        "hashlib": hashlib, "str": str,
        "key_dir": "a:b", "base": "roads", "raw": "goal:道路",
        "blob": "evidence:道路" if path.endswith("service.py") else b"plan-v1",
        "ref": "ref:stats-example", "field_path": "table.rows.0.count",
        "step": SimpleNamespace(stage_id="analysis", ts="2026-11-18"),
        "key": "count", "ch": SimpleNamespace(id="chapter-introduction"),
        "fact": SimpleNamespace(fact_id="fact-count"),
        "w": SimpleNamespace(id="widget-count", kind="chart"),
    }
    expression = compile(ast.Expression(node), path, "eval")
    expected = eval(expression, {"__builtins__": {}}, environment).hexdigest()

    def non_security_sha1(data, **kwargs):
        assert kwargs.get("usedforsecurity") is False, "content ID must declare non-security use"
        return hashlib.sha1(data, **kwargs)

    environment["hashlib"] = SimpleNamespace(sha1=non_security_sha1)
    actual = eval(expression, {"__builtins__": {}}, environment).hexdigest()
    assert actual == expected
