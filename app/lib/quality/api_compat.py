"""API Compatibility Suite（ADR-0104 Wave 16）——HTTP 契约快照与兼容分类。

契约面：FastAPI OpenAPI schema（120 paths 量级）。规则：

- **breaking**：path/method 移除、required 参数/请求体字段新增、参数/字段
  移除、类型变化、enum 收缩、schema required 列表扩张 —— 任何客户端可能
  因此失败的变化，未经说明直接红；
- **additive**：新 path/method、新可选参数、新响应字段、enum 扩张 ——
  允许，但进报告。

快照：``tests/quality/snapshots/openapi.json``。刷新 =
``API_SNAPSHOT_UPDATE=1 pytest tests/quality/test_api_compatibility.py``
（故意破坏必须伴随 PR 说明；测试失败信息即引导）。

已知边界：OpenAPI 不覆盖 WebSocket 消息契约与 SSE 事件 shape（由各自
的对抗/回归测试与 trace 闸保护）；工具 schema 指纹由 registry 自带
（ToolRegistry.schema_fingerprint），此处不重复。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass(frozen=True)
class CompatChange:
    kind: str
    breaking: bool
    subject: str
    detail: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "kind": self.kind,
            "breaking": "1" if self.breaking else "0",
            "subject": self.subject,
            "detail": self.detail,
        }


def snapshot_openapi() -> Dict[str, Any]:
    from app.main import app

    return app.openapi()


# ── diff 规则 ────────────────────────────────────────────────────────────


def _param_required(op: Dict[str, Any], name: str, where: str) -> bool:
    for p in op.get("parameters", []) or []:
        if p.get("name") == name and p.get("in") == where:
            return bool(p.get("required"))
    return False


def diff_operation_params(
    changes: List[CompatChange], path: str, method: str,
    old_op: Dict[str, Any], new_op: Dict[str, Any],
) -> None:
    def _index(op: Dict[str, Any]) -> Dict[tuple, Dict[str, Any]]:
        return {
            (p.get("name"), p.get("in")): p
            for p in (op.get("parameters") or [])
        }

    old_params, new_params = _index(old_op), _index(new_op)
    for key, p in old_params.items():
        if key not in new_params:
            changes.append(CompatChange(
                "param_removed", True, f"{method.upper()} {path}",
                f"parameter {key} removed"))
            continue
        np = new_params[key]
        if not p.get("required") and np.get("required"):
            changes.append(CompatChange(
                "param_became_required", True, f"{method.upper()} {path}",
                f"parameter {key} became required"))
        old_schema = p.get("schema") or {}
        new_schema = np.get("schema") or {}
        if old_schema.get("type") and new_schema.get("type") \
                and old_schema["type"] != new_schema["type"]:
            changes.append(CompatChange(
                "param_type_changed", True, f"{method.upper()} {path}",
                f"parameter {key} type {old_schema['type']} → {new_schema['type']}"))
        old_enum, new_enum = old_schema.get("enum"), new_schema.get("enum")
        if old_enum and new_enum:
            shrunk = set(old_enum) - set(new_enum)
            if shrunk:
                changes.append(CompatChange(
                    "param_enum_shrunk", True, f"{method.upper()} {path}",
                    f"parameter {key} enum lost {sorted(shrunk)}"))
        elif not old_enum and new_enum:
            # 自由值参数被收紧为受限枚举 —— 既有合法取值可能被拒（breaking）
            changes.append(CompatChange(
                "param_enum_added", True, f"{method.upper()} {path}",
                f"parameter {key} gained enum constraint {sorted(new_enum)[:8]}"))

        # media type 变化（同一参数位换了 content 形态 —— OpenAPI 参数无
        # content 键；requestBody 由 diff_request_body 处理）
    for key in new_params:
        if key not in old_params:
            required = bool(new_params[key].get("required"))
            changes.append(CompatChange(
                "param_added", required, f"{method.upper()} {path}",
                f"parameter {key} added (required={required})"))


def diff_operation_responses(
    changes: List[CompatChange], path: str, method: str,
    old_op: Dict[str, Any], new_op: Dict[str, Any],
) -> None:
    old_resp = old_op.get("responses") or {}
    new_resp = new_op.get("responses") or {}
    for code in old_resp:
        if code not in new_resp:
            changes.append(CompatChange(
                "response_removed", True, f"{method.upper()} {path}",
                f"response {code} removed"))
            continue
        old_content = old_resp[code].get("content") or {}
        new_content = new_resp[code].get("content") or {}
        for media in old_content:
            if media not in new_content:
                changes.append(CompatChange(
                    "response_media_removed", True, f"{method.upper()} {path}",
                    f"response {code} media {media} removed"))
                continue
            old_schema = old_content[media].get("schema")
            new_schema = new_content[media].get("schema")
            if old_schema is not None and new_schema is not None \
                    and old_schema != new_schema:
                changes.append(CompatChange(
                    "response_schema_changed", True, f"{method.upper()} {path}",
                    f"response {code} {media} schema changed: {old_schema} → {new_schema}"))


def diff_request_body(
    changes: List[CompatChange], path: str, method: str,
    old_op: Dict[str, Any], new_op: Dict[str, Any],
) -> None:
    old_body = old_op.get("requestBody")
    new_body = new_op.get("requestBody")
    if old_body is not None and new_body is None:
        changes.append(CompatChange(
            "request_body_removed", True, f"{method.upper()} {path}",
            "requestBody removed"))
        return
    if old_body is None or new_body is None:
        return
    if not old_body.get("required") and new_body.get("required"):
        changes.append(CompatChange(
            "request_body_became_required", True, f"{method.upper()} {path}",
            "requestBody became required"))
    old_content = old_body.get("content") or {}
    new_content = new_body.get("content") or {}
    for media in old_content:
        if media not in new_content:
            changes.append(CompatChange(
                "request_body_media_removed", True, f"{method.upper()} {path}",
                f"requestBody media {media} removed"))
            continue
        old_ref = old_content[media].get("schema", {})
        new_ref = new_content[media].get("schema", {})
        if old_ref and new_ref and old_ref != new_ref:
            changes.append(CompatChange(
                "request_body_schema_changed", True, f"{method.upper()} {path}",
                f"requestBody {media} schema {old_ref} → {new_ref}"))


def diff_schema_component(
    changes: List[CompatChange], name: str,
    old: Dict[str, Any], new: Dict[str, Any],
) -> None:
    old_props = old.get("properties") or {}
    new_props = new.get("properties") or {}
    for prop in old_props:
        if prop not in new_props:
            changes.append(CompatChange(
                "schema_field_removed", True, f"schema {name}",
                f"property {prop} removed"))
            continue
        old_pschema = old_props[prop] or {}
        new_pschema = new_props[prop] or {}
        # 字段级类型/枚举变化（R1 review：响应字段 string→integer 是教科书式
        # breaking —— 组件内容变化必须与参数同级对待）
        old_t = old_pschema.get("type")
        new_t = new_pschema.get("type")
        if old_t and new_t and old_t != new_t:
            changes.append(CompatChange(
                "schema_field_type_changed", True, f"schema {name}",
                f"property {prop} type {old_t} → {new_t}"))
        old_penum, new_penum = old_pschema.get("enum"), new_pschema.get("enum")
        if old_penum and new_penum:
            shrunk = set(old_penum) - set(new_penum)
            if shrunk:
                changes.append(CompatChange(
                    "schema_field_enum_shrunk", True, f"schema {name}",
                    f"property {prop} enum lost {sorted(shrunk)}"))
        elif not old_penum and new_penum:
            changes.append(CompatChange(
                "schema_field_enum_added", True, f"schema {name}",
                f"property {prop} gained enum constraint {sorted(new_penum)[:8]}"))
        old_pref = old_pschema.get("$ref") or (old_pschema.get("allOf") or [{}])[0].get("$ref")
        new_pref = new_pschema.get("$ref") or (new_pschema.get("allOf") or [{}])[0].get("$ref")
        if old_pref and new_pref and old_pref != new_pref:
            changes.append(CompatChange(
                "schema_field_ref_changed", True, f"schema {name}",
                f"property {prop} ref {old_pref} → {new_pref}"))
    old_required = set(old.get("required") or [])
    new_required = set(new.get("required") or [])
    grew = sorted(new_required - old_required)
    if grew:
        changes.append(CompatChange(
            "schema_required_grew", True, f"schema {name}",
            f"required now also includes {grew}"))


def diff_openapi(old: Dict[str, Any], new: Dict[str, Any]) -> List[CompatChange]:
    changes: List[CompatChange] = []
    old_paths = old.get("paths") or {}
    new_paths = new.get("paths") or {}
    for path in sorted(set(old_paths) - set(new_paths)):
        changes.append(CompatChange(
            "path_removed", True, path, "path removed"))
    for path in sorted(set(new_paths) - set(old_paths)):
        changes.append(CompatChange(
            "path_added", False, path, "path added"))
    for path in sorted(set(old_paths) & set(new_paths)):
        old_ops = {
            m: op for m, op in old_paths[path].items()
            if m in ("get", "post", "put", "patch", "delete")
        }
        new_ops = {
            m: op for m, op in new_paths[path].items()
            if m in ("get", "post", "put", "patch", "delete")
        }
        for method in sorted(set(old_ops) - set(new_ops)):
            changes.append(CompatChange(
                "method_removed", True, path, f"{method.upper()} removed"))
        for method in sorted(set(new_ops) - set(old_ops)):
            changes.append(CompatChange(
                "method_added", False, path, f"{method.upper()} added"))
        for method in sorted(set(old_ops) & set(new_ops)):
            old_op, new_op = old_ops[method], new_ops[method]
            diff_operation_params(changes, path, method, old_op, new_op)
            diff_request_body(changes, path, method, old_op, new_op)
            diff_operation_responses(changes, path, method, old_op, new_op)

    old_comp = (old.get("components") or {}).get("schemas") or {}
    new_comp = (new.get("components") or {}).get("schemas") or {}
    for name in sorted(set(old_comp) - set(new_comp)):
        changes.append(CompatChange(
            "schema_removed", True, name, "schema component removed"))
    for name in sorted(set(old_comp) & set(new_comp)):
        if old_comp[name] != new_comp[name]:
            diff_schema_component(changes, name, old_comp[name], new_comp[name])
    return changes
