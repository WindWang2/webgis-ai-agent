"""V9 契约基石：字段级契约闸（ADR-0138 / P8，#1217 的后端半边）。

path 级快照闸（tests/quality/test_api_compatibility.py）只挡「端点消失/
参数变更」；字段级漂移（响应模型字段增删/改名/类型变化）对 TS 侧无强制
同步 —— 本闸补上：每端点 200 响应 schema 的字段签名（字段名 + 类型）
逐端点快照比对。

刷新：API_CONTRACT_FIELDS_UPDATE=1 pytest tests/unit/api_contract/test_field_contract.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))

SNAPSHOT = (
    Path(__file__).resolve().parents[2] / "quality" / "snapshots" / "api_contract_fields.json"
)

#: 字段签名深度：响应模型顶层字段 + 一层嵌套 $ref 的字段名。
#: （全递归会让快照体积爆炸；一层已覆盖「TS 接口镜像」的常见漂移面。）
MAX_REF_DEPTH = 1


def _field_signature(schema: dict, components: dict, depth: int = 0) -> dict:
    """从 OpenAPI schema 提取 (字段名 -> 类型) 签名，$ref 展开一层。"""
    if not isinstance(schema, dict):
        return {}
    ref = schema.get("$ref")
    if ref:
        name = ref.rsplit("/", 1)[-1]
        fields = {"__model__": name}
        if depth < MAX_REF_DEPTH:
            target = components.get("schemas", {}).get(name, {})
            for prop, spec in (target.get("properties") or {}).items():
                fields[prop] = _type_of(spec, components, depth + 1)
        return fields
    if schema.get("type") == "array":
        return {"__array_of__": _field_signature(schema.get("items", {}), components, depth)}
    if schema.get("type") == "object" or "properties" in schema:
        return {
            prop: _type_of(spec, components, depth)
            for prop, spec in (schema.get("properties") or {}).items()
        }
    return {"__type__": schema.get("type", "unknown")}


def _type_of(spec: dict, components: dict, depth: int) -> str:
    if not isinstance(spec, dict):
        return "unknown"
    if "$ref" in spec:
        return spec["$ref"].rsplit("/", 1)[-1]
    if spec.get("type") == "array":
        return f"array<{_type_of(spec.get('items', {}), components, depth)}>"
    if "anyOf" in spec:
        return "|".join(sorted(_type_of(s, components, depth) for s in spec["anyOf"]))
    return str(spec.get("type", "unknown"))


def _current_signatures() -> dict:
    from app.main import app

    app.openapi_schema = None
    schema = app.openapi()
    app.openapi_schema = None
    components = schema.get("components", {})
    out = {}
    for path, methods in schema["paths"].items():
        for method, op in methods.items():
            if method not in ("get", "post", "put", "delete", "patch"):
                continue
            try:
                resp_schema = op["responses"]["200"]["content"]["application/json"]["schema"]
            except (KeyError, TypeError):
                continue
            sig = _field_signature(resp_schema, components)
            if sig:
                out[f"{method.upper()} {path}"] = sig
    return dict(sorted(out.items()))


def _write_snapshot(signatures: dict) -> None:
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(
        json.dumps(signatures, ensure_ascii=False, sort_keys=True, indent=1) + "\n",
        encoding="utf-8",
    )


def test_response_field_signatures_unchanged():
    current = _current_signatures()
    assert len(current) >= 150, f"字段签名覆盖端点数异常: {len(current)}"
    if os.environ.get("API_CONTRACT_FIELDS_UPDATE") == "1":
        _write_snapshot(current)
        pytest.skip("snapshot refreshed")
    assert SNAPSHOT.exists(), (
        "字段契约快照缺失：API_CONTRACT_FIELDS_UPDATE=1 "
        "pytest tests/unit/api_contract/test_field_contract.py 生成"
    )
    committed = json.loads(SNAPSHOT.read_text(encoding="utf-8"))

    removed = sorted(set(committed) - set(current))
    added = sorted(set(current) - set(committed))
    drift = []
    for key in sorted(set(committed) & set(current)):
        if committed[key] != current[key]:
            old_fields = set(committed[key])
            new_fields = set(current[key])
            dropped = sorted(f for f in old_fields - new_fields if not f.startswith("__"))
            newly = sorted(f for f in new_fields - old_fields if not f.startswith("__"))
            type_changed = sorted(
                f for f in old_fields & new_fields if committed[key][f] != current[key][f]
            )
            drift.append(
                f"  {key}: 删除={dropped} 新增={newly} 类型变化={type_changed}"
                if (dropped or newly or type_changed)
                else f"  {key}: 签名差异 {committed[key]} -> {current[key]}"
            )

    assert not (removed or added or drift), (
        "字段级契约漂移（#1217 闸，ADR-0138）：\n"
        + (f"\n端点消失: {removed}" if removed else "")
        + (f"\n端点新增: {added}" if added else "")
        + ("\n字段漂移:\n" + "\n".join(drift[:40]) if drift else "")
        + "\n\n故意变更请：API_CONTRACT_FIELDS_UPDATE=1 刷新快照并在 PR 说明 "
        "（前端 TS 类型需同步镜像）。"
    )
