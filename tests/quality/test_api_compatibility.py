"""API Compatibility 红线（ADR-0104 Wave 16）。

- 后端 OpenAPI 快照（tests/quality/snapshots/openapi.json）；
- 当前 schema vs 快照：breaking 变化直接红（additive 允许但计数）；
- 分类器自测：每条规则必须真的能判别（拆掉恒绿风险）；
- 刷新：API_SNAPSHOT_UPDATE=1 pytest tests/quality/test_api_compatibility.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

SNAPSHOT = Path(__file__).resolve().parent / "snapshots" / "openapi.json"

from app.lib.quality.api_compat import (  # noqa: E402
    diff_openapi,
    snapshot_openapi,
)


def _write_snapshot() -> None:
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(
        json.dumps(snapshot_openapi(), ensure_ascii=False, sort_keys=True, indent=1) + "\n",
        encoding="utf-8")


def test_snapshot_matches_and_no_breaking_changes():
    current = snapshot_openapi()
    if os.environ.get("API_SNAPSHOT_UPDATE") == "1":
        _write_snapshot()
        pytest.skip("snapshot refreshed")
    assert SNAPSHOT.exists(), (
        "快照缺失：API_SNAPSHOT_UPDATE=1 pytest tests/quality/test_api_compatibility.py 生成")
    old = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    changes = diff_openapi(old, current)
    breaking = [c for c in changes if c.breaking]
    assert not breaking, (
        "检测到未声明的 BREAKING API 变化（如属故意，请 "
        "API_SNAPSHOT_UPDATE=1 刷新快照并在 PR 中说明）：\n"
        + "\n".join(f"- [{c.kind}] {c.subject}: {c.detail}" for c in breaking[:20])
    )


def test_snapshot_generation_deterministic():
    a = json.dumps(snapshot_openapi(), ensure_ascii=False, sort_keys=True)
    b = json.dumps(snapshot_openapi(), ensure_ascii=False, sort_keys=True)
    assert a == b


# ── 分类器自测（规则判别力）───────────────────────────────────────────────


def _op(**kw):
    base = {"responses": {"200": {"description": "ok"}}}
    base.update(kw)
    return base


def test_path_removal_is_breaking():
    old = {"paths": {"/api/v1/x": {"get": _op()}}}
    new = {"paths": {}}
    changes = diff_openapi(old, new)
    assert any(c.kind == "path_removed" and c.breaking for c in changes)


def test_optional_param_addition_is_additive():
    old = {"paths": {"/a": {"get": _op(parameters=[])}}}
    new = {"paths": {"/a": {"get": _op(parameters=[
        {"name": "zoom", "in": "query", "required": False, "schema": {"type": "integer"}}],
    )}}}
    changes = diff_openapi(old, new)
    added = [c for c in changes if c.kind == "param_added"]
    assert added and not added[0].breaking


def test_required_param_addition_is_breaking():
    old = {"paths": {"/a": {"get": _op(parameters=[])}}}
    new = {"paths": {"/a": {"get": _op(parameters=[
        {"name": "zoom", "in": "query", "required": True, "schema": {"type": "integer"}}],
    )}}}
    changes = diff_openapi(old, new)
    added = [c for c in changes if c.kind == "param_added"]
    assert added and added[0].breaking


def test_param_became_required_is_breaking():
    old = {"paths": {"/a": {"get": _op(parameters=[
        {"name": "q", "in": "query", "required": False, "schema": {"type": "string"}}])}}}
    new = {"paths": {"/a": {"get": _op(parameters=[
        {"name": "q", "in": "query", "required": True, "schema": {"type": "string"}}])}}}
    changes = diff_openapi(old, new)
    assert any(c.kind == "param_became_required" and c.breaking for c in changes)


def test_schema_field_removal_is_breaking():
    old = {"paths": {}, "components": {"schemas": {
        "Item": {"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "string"}}}}}}
    new = {"paths": {}, "components": {"schemas": {
        "Item": {"type": "object", "properties": {"a": {"type": "string"}}}}}}
    changes = diff_openapi(old, new)
    assert any(c.kind == "schema_field_removed" and c.breaking for c in changes)


def test_type_change_and_enum_shrink_are_breaking():
    old = {"paths": {"/a": {"get": _op(parameters=[
        {"name": "q", "in": "query", "required": False,
         "schema": {"type": "string", "enum": ["a", "b"]}}])}}}
    new = {"paths": {"/a": {"get": _op(parameters=[
        {"name": "q", "in": "query", "required": False,
         "schema": {"type": "integer", "enum": ["a"]}}])}}}
    changes = diff_openapi(old, new)
    kinds = {c.kind for c in changes if c.breaking}
    assert "param_type_changed" in kinds
    assert "param_enum_shrunk" in kinds
