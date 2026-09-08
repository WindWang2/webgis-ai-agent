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
    # 字节一致闸（R1 review MAJOR-2）：additive 变化也必须显式刷新快照 ——
    # 否则「新增 path 后再删除」对旧快照静默diff干净，漂移被掩埋。
    committed = SNAPSHOT.read_text(encoding="utf-8")
    current_bytes = json.dumps(current, ensure_ascii=False, sort_keys=True, indent=1) + "\n"
    if committed != current_bytes:
        old = json.loads(committed)
        breaking = [c for c in diff_openapi(old, current) if c.breaking]
        assert False, (
            "OpenAPI 快照过期（additive 变化也需显式刷新）。BREAKING 变化数："
            f"{len(breaking)}（如属故意，API_SNAPSHOT_UPDATE=1 刷新并在 PR 说明）"
            + "\n".join(f"- [{c.kind}] {c.subject}: {c.detail}" for c in breaking[:20])
        )


def test_snapshot_generation_deterministic():
    """R1 review MINOR-1：FastAPI 缓存 openapi schema —— 必须显式失效后
    重算，否则恒等断言是框架缓存造成的空洞。"""
    from app.main import app

    a = json.dumps(snapshot_openapi(), ensure_ascii=False, sort_keys=True)
    app.openapi_schema = None
    try:
        b = json.dumps(snapshot_openapi(), ensure_ascii=False, sort_keys=True)
    finally:
        app.openapi_schema = None
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


# ── R1 review 补强：分类器新增规则自测 ────────────────────────────────────


def test_component_field_type_change_is_breaking():
    old = {"paths": {}, "components": {"schemas": {
        "Item": {"type": "object", "properties": {"count": {"type": "string"}}}}}}
    new = {"paths": {}, "components": {"schemas": {
        "Item": {"type": "object", "properties": {"count": {"type": "integer"}}}}}}
    changes = diff_openapi(old, new)
    kinds = {c.kind for c in changes if c.breaking}
    assert "schema_field_type_changed" in kinds, changes


def test_component_field_enum_addition_is_breaking():
    old = {"paths": {}, "components": {"schemas": {
        "Item": {"type": "object", "properties": {"kind": {"type": "string"}}}}}}
    new = {"paths": {}, "components": {"schemas": {
        "Item": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": ["a", "b"]}}}}}}
    changes = diff_openapi(old, new)
    kinds = {c.kind for c in changes if c.breaking}
    assert "schema_field_enum_added" in kinds


def test_param_enum_addition_is_breaking():
    old = {"paths": {"/a": {"get": _op(parameters=[
        {"name": "q", "in": "query", "required": False, "schema": {"type": "string"}}])}}}
    new = {"paths": {"/a": {"get": _op(parameters=[
        {"name": "q", "in": "query", "required": False,
         "schema": {"type": "string", "enum": ["a"]}}])}}}
    changes = diff_openapi(old, new)
    assert any(c.kind == "param_enum_added" and c.breaking for c in changes)


def test_response_media_removal_is_breaking():
    old = {"paths": {"/a": {"get": _op(responses={"200": {
        "description": "ok", "content": {"application/json": {"schema": {"type": "object"}}}}})}}}
    new = {"paths": {"/a": {"get": _op(responses={"200": {
        "description": "ok", "content": {"text/plain": {"schema": {"type": "string"}}}}})}}}
    changes = diff_openapi(old, new)
    kinds = {c.kind for c in changes if c.breaking}
    assert "response_media_removed" in kinds
