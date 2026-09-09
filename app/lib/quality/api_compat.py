"""API Compatibility Suite（ADR-0104 Wave 16 / Quality V2 Wave W6）。

契约面 1 —— HTTP：FastAPI OpenAPI schema（131 paths 量级）。规则：

- **breaking**：path/method 移除、required 参数/请求体字段新增、参数/字段
  移除、类型变化、enum 收缩、schema required 列表扩张 —— 任何客户端可能
  因此失败的变化，未经说明直接红；
- **additive**：新 path/method、新可选参数、新响应字段、enum 扩张 ——
  允许，但进报告。

快照：``tests/quality/snapshots/openapi.json``。刷新 =
``API_SNAPSHOT_UPDATE=1 pytest tests/quality/test_api_compatibility.py``
（故意破坏必须伴随 PR 说明；测试失败信息即引导）。

契约面 2 —— Realtime（Quality V2，补原 15-17 行声明的盲区）：

- **WebSocket**：入站感知事件词表（PERCEPTION_HANDLERS）+ 每事件必需字段
  （AST 派生 handler 的 data.get 守卫）+ 出站信封 {event, data} + 出站
  事件词表（AST 扫描 broadcast_ws_event 字面量）+ auth 语义锚；
- **SSE**：事件词表（AST 扫描 sse_event 首参字面量）+ wire 格式
  （event:/id:/data:）+ resume 语义锚。

快照：``tests/quality/snapshots/realtime-contract.json``。刷新 =
``REALTIME_SNAPSHOT_UPDATE=1 pytest tests/quality/test_realtime_contract.py``。

已知边界（诚实披露）：WS/SSE 的**字段级类型**契约不由本快照覆盖
（信封 data 是自由 JSON）；工具 schema 指纹由 registry 自带
（ToolRegistry.schema_fingerprint），此处不重复。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
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
    changes: List[CompatChange],
    path: str,
    method: str,
    old_op: Dict[str, Any],
    new_op: Dict[str, Any],
) -> None:
    def _index(op: Dict[str, Any]) -> Dict[tuple, Dict[str, Any]]:
        return {(p.get("name"), p.get("in")): p for p in (op.get("parameters") or [])}

    old_params, new_params = _index(old_op), _index(new_op)
    for key, p in old_params.items():
        if key not in new_params:
            changes.append(
                CompatChange(
                    "param_removed",
                    True,
                    f"{method.upper()} {path}",
                    f"parameter {key} removed",
                )
            )
            continue
        np = new_params[key]
        if not p.get("required") and np.get("required"):
            changes.append(
                CompatChange(
                    "param_became_required",
                    True,
                    f"{method.upper()} {path}",
                    f"parameter {key} became required",
                )
            )
        old_schema = p.get("schema") or {}
        new_schema = np.get("schema") or {}
        if (
            old_schema.get("type")
            and new_schema.get("type")
            and old_schema["type"] != new_schema["type"]
        ):
            changes.append(
                CompatChange(
                    "param_type_changed",
                    True,
                    f"{method.upper()} {path}",
                    f"parameter {key} type {old_schema['type']} → {new_schema['type']}",
                )
            )
        old_enum, new_enum = old_schema.get("enum"), new_schema.get("enum")
        if old_enum and new_enum:
            shrunk = set(old_enum) - set(new_enum)
            if shrunk:
                changes.append(
                    CompatChange(
                        "param_enum_shrunk",
                        True,
                        f"{method.upper()} {path}",
                        f"parameter {key} enum lost {sorted(shrunk)}",
                    )
                )
        elif not old_enum and new_enum:
            # 自由值参数被收紧为受限枚举 —— 既有合法取值可能被拒（breaking）
            changes.append(
                CompatChange(
                    "param_enum_added",
                    True,
                    f"{method.upper()} {path}",
                    f"parameter {key} gained enum constraint {sorted(new_enum)[:8]}",
                )
            )

        # media type 变化（同一参数位换了 content 形态 —— OpenAPI 参数无
        # content 键；requestBody 由 diff_request_body 处理）
    for key in new_params:
        if key not in old_params:
            required = bool(new_params[key].get("required"))
            changes.append(
                CompatChange(
                    "param_added",
                    required,
                    f"{method.upper()} {path}",
                    f"parameter {key} added (required={required})",
                )
            )


def diff_operation_responses(
    changes: List[CompatChange],
    path: str,
    method: str,
    old_op: Dict[str, Any],
    new_op: Dict[str, Any],
) -> None:
    old_resp = old_op.get("responses") or {}
    new_resp = new_op.get("responses") or {}
    for code in old_resp:
        if code not in new_resp:
            changes.append(
                CompatChange(
                    "response_removed",
                    True,
                    f"{method.upper()} {path}",
                    f"response {code} removed",
                )
            )
            continue
        old_content = old_resp[code].get("content") or {}
        new_content = new_resp[code].get("content") or {}
        for media in old_content:
            if media not in new_content:
                changes.append(
                    CompatChange(
                        "response_media_removed",
                        True,
                        f"{method.upper()} {path}",
                        f"response {code} media {media} removed",
                    )
                )
                continue
            old_schema = old_content[media].get("schema")
            new_schema = new_content[media].get("schema")
            if (
                old_schema is not None
                and new_schema is not None
                and old_schema != new_schema
            ):
                changes.append(
                    CompatChange(
                        "response_schema_changed",
                        True,
                        f"{method.upper()} {path}",
                        f"response {code} {media} schema changed: {old_schema} → {new_schema}",
                    )
                )


def diff_request_body(
    changes: List[CompatChange],
    path: str,
    method: str,
    old_op: Dict[str, Any],
    new_op: Dict[str, Any],
) -> None:
    old_body = old_op.get("requestBody")
    new_body = new_op.get("requestBody")
    if old_body is not None and new_body is None:
        changes.append(
            CompatChange(
                "request_body_removed",
                True,
                f"{method.upper()} {path}",
                "requestBody removed",
            )
        )
        return
    if old_body is None or new_body is None:
        return
    if not old_body.get("required") and new_body.get("required"):
        changes.append(
            CompatChange(
                "request_body_became_required",
                True,
                f"{method.upper()} {path}",
                "requestBody became required",
            )
        )
    old_content = old_body.get("content") or {}
    new_content = new_body.get("content") or {}
    for media in old_content:
        if media not in new_content:
            changes.append(
                CompatChange(
                    "request_body_media_removed",
                    True,
                    f"{method.upper()} {path}",
                    f"requestBody media {media} removed",
                )
            )
            continue
        old_ref = old_content[media].get("schema", {})
        new_ref = new_content[media].get("schema", {})
        if old_ref and new_ref and old_ref != new_ref:
            changes.append(
                CompatChange(
                    "request_body_schema_changed",
                    True,
                    f"{method.upper()} {path}",
                    f"requestBody {media} schema {old_ref} → {new_ref}",
                )
            )


def diff_schema_component(
    changes: List[CompatChange],
    name: str,
    old: Dict[str, Any],
    new: Dict[str, Any],
) -> None:
    old_props = old.get("properties") or {}
    new_props = new.get("properties") or {}
    for prop in old_props:
        if prop not in new_props:
            changes.append(
                CompatChange(
                    "schema_field_removed",
                    True,
                    f"schema {name}",
                    f"property {prop} removed",
                )
            )
            continue
        old_pschema = old_props[prop] or {}
        new_pschema = new_props[prop] or {}
        # 字段级类型/枚举变化（R1 review：响应字段 string→integer 是教科书式
        # breaking —— 组件内容变化必须与参数同级对待）
        old_t = old_pschema.get("type")
        new_t = new_pschema.get("type")
        if old_t and new_t and old_t != new_t:
            changes.append(
                CompatChange(
                    "schema_field_type_changed",
                    True,
                    f"schema {name}",
                    f"property {prop} type {old_t} → {new_t}",
                )
            )
        old_penum, new_penum = old_pschema.get("enum"), new_pschema.get("enum")
        if old_penum and new_penum:
            shrunk = set(old_penum) - set(new_penum)
            if shrunk:
                changes.append(
                    CompatChange(
                        "schema_field_enum_shrunk",
                        True,
                        f"schema {name}",
                        f"property {prop} enum lost {sorted(shrunk)}",
                    )
                )
        elif not old_penum and new_penum:
            changes.append(
                CompatChange(
                    "schema_field_enum_added",
                    True,
                    f"schema {name}",
                    f"property {prop} gained enum constraint {sorted(new_penum)[:8]}",
                )
            )
        old_pref = old_pschema.get("$ref") or (old_pschema.get("allOf") or [{}])[0].get(
            "$ref"
        )
        new_pref = new_pschema.get("$ref") or (new_pschema.get("allOf") or [{}])[0].get(
            "$ref"
        )
        if old_pref and new_pref and old_pref != new_pref:
            changes.append(
                CompatChange(
                    "schema_field_ref_changed",
                    True,
                    f"schema {name}",
                    f"property {prop} ref {old_pref} → {new_pref}",
                )
            )
    old_required = set(old.get("required") or [])
    new_required = set(new.get("required") or [])
    grew = sorted(new_required - old_required)
    if grew:
        changes.append(
            CompatChange(
                "schema_required_grew",
                True,
                f"schema {name}",
                f"required now also includes {grew}",
            )
        )


def diff_openapi(old: Dict[str, Any], new: Dict[str, Any]) -> List[CompatChange]:
    changes: List[CompatChange] = []
    old_paths = old.get("paths") or {}
    new_paths = new.get("paths") or {}
    for path in sorted(set(old_paths) - set(new_paths)):
        changes.append(CompatChange("path_removed", True, path, "path removed"))
    for path in sorted(set(new_paths) - set(old_paths)):
        changes.append(CompatChange("path_added", False, path, "path added"))
    for path in sorted(set(old_paths) & set(new_paths)):
        old_ops = {
            m: op
            for m, op in old_paths[path].items()
            if m in ("get", "post", "put", "patch", "delete")
        }
        new_ops = {
            m: op
            for m, op in new_paths[path].items()
            if m in ("get", "post", "put", "patch", "delete")
        }
        for method in sorted(set(old_ops) - set(new_ops)):
            changes.append(
                CompatChange("method_removed", True, path, f"{method.upper()} removed")
            )
        for method in sorted(set(new_ops) - set(old_ops)):
            changes.append(
                CompatChange("method_added", False, path, f"{method.upper()} added")
            )
        for method in sorted(set(old_ops) & set(new_ops)):
            old_op, new_op = old_ops[method], new_ops[method]
            diff_operation_params(changes, path, method, old_op, new_op)
            diff_request_body(changes, path, method, old_op, new_op)
            diff_operation_responses(changes, path, method, old_op, new_op)

    old_comp = (old.get("components") or {}).get("schemas") or {}
    new_comp = (new.get("components") or {}).get("schemas") or {}
    for name in sorted(set(old_comp) - set(new_comp)):
        changes.append(
            CompatChange("schema_removed", True, name, "schema component removed")
        )
    for name in sorted(set(old_comp) & set(new_comp)):
        if old_comp[name] != new_comp[name]:
            diff_schema_component(changes, name, old_comp[name], new_comp[name])
    return changes


# ── Realtime 契约（Quality V2 W6）：WS + SSE ─────────────────────────────

_REALTIME_BOUND_SNIPPETS_CACHE: Dict[str, Dict[str, Any]] = {}


def _read(app_relative: str) -> str:
    root = Path(__file__).resolve().parents[3]
    return (root / app_relative).read_text(encoding="utf-8", errors="replace")


def _names_outside_calls(test: ast.AST) -> set:
    """if 测试中未被 Call 包裹的 Name 集合（守卫操作数判定）。"""
    names: set = set()

    def _walk(node: ast.AST, inside_call: bool) -> None:
        if isinstance(node, ast.Name) and not inside_call:
            names.add(node.id)
        for child in ast.iter_child_nodes(node):
            _walk(child, inside_call or isinstance(child, ast.Call))

    _walk(test, False)
    return names


def _ws_inbound_handlers() -> Dict[str, List[str]]:
    """{event: [必需字段（升序）]}，AST 派生自 ws_service.py。

    必需字段判定（与 handler 现有守卫一一对应）：函数体内出现
    ``x = data.get("k")`` 且 ``k`` 出现在某个 ``if`` 测试中 → required。
    这是静态下界（诚实方向：只可能把可选判成可选）。
    """
    from app.services.ws_service import PERCEPTION_HANDLERS

    tree = ast.parse(_read("app/services/ws_service.py"))
    get_names: Dict[str, set] = {}  # 函数名 -> data.get 字面量集合
    if_names: Dict[str, set] = {}  # 函数名 -> if 测试中出现的名称
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        gets: set = set()
        ifs: set = set()
        for sub in ast.walk(node):
            if (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and sub.func.attr == "get"
                and isinstance(sub.func.value, ast.Name)
                and sub.func.value.id == "data"
                and sub.args
                and isinstance(sub.args[0], ast.Constant)
                and isinstance(sub.args[0].value, str)
            ):
                gets.add(sub.args[0].value)
            elif isinstance(sub, ast.If) and sub.test is not None:
                # 守卫判定：名称必须是 if 测试的直接操作数（裸名 / is None /
                # not x）；嵌套在 isinstance(...) 等 Call 内的引用不算必需
                # 守卫（如 viewport_change 对 center 的后验类型检查）。
                ifs |= _names_outside_calls(sub.test)
        get_names[node.name] = gets
        if_names[node.name] = ifs
    out: Dict[str, List[str]] = {}
    for event, handler in PERCEPTION_HANDLERS.items():
        fname = getattr(handler, "__name__", "")
        required = sorted(get_names.get(fname, set()) & if_names.get(fname, set()))
        out[event] = required
    return out


def _matches_call(node: ast.Call, names: tuple) -> bool:
    """func 是 ``name``（裸导入调用）或 ``<x>.name``（方法调用）之一。"""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id in names
    if isinstance(func, ast.Attribute):
        return func.attr in names
    return False


def _scan_literal_first_args(
    func_names: tuple,
    arg_index: int,
    roots: tuple = ("app",),
) -> List[str]:
    """AST 扫描 app/**/*.py 中 ``{func_names}(..., "literal", ...)`` 调用。"""
    root = Path(__file__).resolve().parents[3]
    names: set = set()
    for pattern_dir in roots:
        base = root / pattern_dir
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except (OSError, SyntaxError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if not _matches_call(node, func_names):
                    continue
                if (
                    len(node.args) > arg_index
                    and isinstance(node.args[arg_index], ast.Constant)
                    and isinstance(node.args[arg_index].value, str)
                ):
                    names.add(node.args[arg_index].value)
    return sorted(names)


def _scan_ws_outbound_events() -> List[str]:
    """出站事件词表：任何 *broadcast 形态调用的第 2 参字面量。

    覆盖 broadcast_ws_event 直调与 *_fire_broadcast 包装（事件名在包装
    调用点以字面量出现的场景）；变量中转诚实漏报。
    """
    import re

    root = Path(__file__).resolve().parents[3]
    hits: set = set()
    for path in sorted((root / "app").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(
                node.func, ast.Attribute
            ):
                continue
            if not re.fullmatch(r"[a-z_]*broadcast", node.func.attr):
                continue
            if (
                len(node.args) > 1
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
            ):
                hits.add(node.args[1].value)
    return sorted(hits)


def _ws_auth_anchors() -> Dict[str, bool]:
    """WS 认证语义锚（存在性布尔，源自 ws.py 源码静态检查）。"""
    src = _read("app/api/routes/ws.py")
    return {
        "subprotocol_preferred": "Sec-WebSocket-Protocol" in src,
        "token_verified": "verify_token(" in src,
        "token_version_checked": "token_version" in src,
        "session_ownership_fail_closed": "ownership check" in src,
        "per_ip_rate_limited": "rate" in src.lower(),
    }


def snapshot_realtime_contract() -> Dict[str, Any]:
    """WS/SSE 契约快照（确定性：排序、无时间戳、无行号）。"""
    from app.services.ws_service import PERCEPTION_HANDLERS

    inbound = {}
    for event, required in _ws_inbound_handlers().items():
        handler_name = getattr(PERCEPTION_HANDLERS[event], "__name__", "")
        inbound[event] = {
            "required_fields": required,
            "handler": f"app.services.ws_service.{handler_name}",
        }
    return {
        "artifact": "realtime-contract",
        "contract_version": 1,
        "websocket": {
            "endpoint": "/api/v1/ws/{session_id}",
            "auth": _ws_auth_anchors(),
            "inbound_envelope": {"event": "str", "data": "object"},
            "outbound_envelope": {"event": "str", "data": "any"},
            "inbound_events": inbound,
            "outbound_events": _scan_ws_outbound_events(),
        },
        "sse": {
            "endpoint": "/api/v1/chat/stream",
            "wire_format": [
                "event: <type>",
                "id: <per-turn monotonic int>",
                "data: <json>",
            ],
            "primitives": [
                "sse_event",
                "sse_event_id",
                "sse_event_type",
                "sse_event_id_scope",
            ],
            "events": _scan_literal_first_args(("sse_event",), 0),
            "resume": {
                "last_event_id_replay": True,
                "keepalive_comments": True,
            },
        },
    }


def diff_realtime_contract(
    old: Dict[str, Any],
    new: Dict[str, Any],
) -> List[CompatChange]:
    """Realtime 兼容分类：词表收缩/必需字段扩张/语义锚降级 = breaking。"""
    changes: List[CompatChange] = []
    old_ws = old.get("websocket") or {}
    new_ws = new.get("websocket") or {}
    old_sse = old.get("sse") or {}
    new_sse = new.get("sse") or {}

    old_in = old_ws.get("inbound_events") or {}
    new_in = new_ws.get("inbound_events") or {}
    for event in sorted(set(old_in) - set(new_in)):
        changes.append(
            CompatChange(
                "ws_inbound_event_removed", True, f"ws.inbound.{event}", "event removed"
            )
        )
    for event in sorted(set(new_in) - set(old_in)):
        changes.append(
            CompatChange(
                "ws_inbound_event_added", False, f"ws.inbound.{event}", "event added"
            )
        )
    for event in sorted(set(old_in) & set(new_in)):
        old_req = set((old_in[event] or {}).get("required_fields") or [])
        new_req = set((new_in[event] or {}).get("required_fields") or [])
        grew = sorted(new_req - old_req)
        if grew:
            changes.append(
                CompatChange(
                    "ws_required_field_added",
                    True,
                    f"ws.inbound.{event}",
                    f"now also requires {grew}",
                )
            )

    old_out = set(old_ws.get("outbound_events") or [])
    new_out = set(new_ws.get("outbound_events") or [])
    for event in sorted(old_out - new_out):
        changes.append(
            CompatChange(
                "ws_outbound_event_removed",
                True,
                f"ws.outbound.{event}",
                "event removed",
            )
        )
    for event in sorted(new_out - old_out):
        changes.append(
            CompatChange(
                "ws_outbound_event_added", False, f"ws.outbound.{event}", "event added"
            )
        )

    old_auth = old_ws.get("auth") or {}
    new_auth = new_ws.get("auth") or {}
    for anchor in sorted(set(old_auth) | set(new_auth)):
        if old_auth.get(anchor) and not new_auth.get(anchor):
            changes.append(
                CompatChange(
                    "ws_auth_anchor_dropped",
                    True,
                    f"ws.auth.{anchor}",
                    "semantic anchor lost",
                )
            )

    old_ev = set(old_sse.get("events") or [])
    new_ev = set(new_sse.get("events") or [])
    for event in sorted(old_ev - new_ev):
        changes.append(
            CompatChange("sse_event_removed", True, f"sse.{event}", "event removed")
        )
    for event in sorted(new_ev - old_ev):
        changes.append(
            CompatChange("sse_event_added", False, f"sse.{event}", "event added")
        )

    old_prim = set(old_sse.get("primitives") or [])
    new_prim = set(new_sse.get("primitives") or [])
    for prim in sorted(old_prim - new_prim):
        changes.append(
            CompatChange(
                "sse_primitive_removed", True, f"sse.{prim}", "primitive removed"
            )
        )
    for prim in sorted(new_prim - old_prim):
        changes.append(
            CompatChange(
                "sse_primitive_added", False, f"sse.{prim}", "primitive added"
            )
        )

    # R2 review 漏类补齐：resume 语义锚 / 信封与 wire 形状
    old_resume = old_sse.get("resume") or {}
    new_resume = new_sse.get("resume") or {}
    for anchor in sorted(set(old_resume) | set(new_resume)):
        if old_resume.get(anchor) and not new_resume.get(anchor):
            changes.append(
                CompatChange(
                    "sse_resume_anchor_dropped",
                    True,
                    f"sse.resume.{anchor}",
                    "resume semantic anchor lost",
                )
            )
        elif anchor not in old_resume and anchor in new_resume:
            changes.append(
                CompatChange(
                    "sse_resume_anchor_added",
                    False,
                    f"sse.resume.{anchor}",
                    "resume anchor added",
                )
            )

    for keys, label in (
        (("websocket", "inbound_envelope"), "ws.inbound"),
        (("websocket", "outbound_envelope"), "ws.outbound"),
        (("sse", "wire_format"), "sse.wire"),
    ):
        old_shape: Any = old
        new_shape: Any = new
        for key in keys:
            old_shape = (old_shape or {}).get(key)
            new_shape = (new_shape or {}).get(key)
        if old_shape is not None and new_shape is not None \
                and old_shape != new_shape:
            changes.append(
                CompatChange(
                    f"{label}_envelope_changed",
                    True,
                    label,
                    f"envelope/wire shape changed: {old_shape} → {new_shape}",
                )
            )
    return changes
