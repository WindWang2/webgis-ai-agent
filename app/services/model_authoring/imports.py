"""模型导入转换器：项目工作流 / 会话计划 / 模板 → ModelDocument。

三个确定性转换器（同输入同 doc；零 LLM、零 I/O）：

- ``from_project_workflow``：项目工作流 graph_spec（WorkflowStepSpec dict
  列表）→ analysis/data_input/output 节点；capability 缺失的 tool-only
  step 不静默丢弃，进 ``skipped`` 披露；
- ``from_session_plan_steps``：plan 的 data_requirements + analysis_steps
  行 → 复用 ``workflow_promotion.build_recipe_steps``（import 复用，不复制
  映射逻辑）；promotion blockers 非空 → ValueError（fail-closed）；
- ``from_template``：模板实例化（deepcopy + 来源/模板标记，节点与参数
  原样保留 —— 参数是模板的一部分）。

fail-closed 纪律：转换失败抛 ValueError / typed 错误，绝不静默产出残缺
模型；未转换项一律经 ``skipped`` 诚实披露。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from app.schemas.model_authoring_schema import (
    BoundedValue,
    ModelDocument,
    ModelEdge,
    ModelNode,
    ModelNodeParams,
    ModelSource,
)

#: 节点/参数键 slug 词法（schema 同词）。
_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")

#: 与 workflow_promotion._clean_args 同纪律：会话执行态键不入可复用模型。
_SESSION_BOUND_KEYS = ("bound_ref", "status", "resolved_tool", "resolved_algorithm")

#: 节点静态参数帽（schema 同界）。
_MAX_NODE_PARAMS = 16

#: slugify 结果帽（store 层命名用）。
_SLUGIFY_MAX = 40


def slugify_name(name: str) -> str:
    """任意显示名 → 存储 slug（确定性；空 → "model"）。

    ASCII 字母/数字/下划线保留（字母小写）；其余 ASCII（空白/标点）剔除；
    非 ASCII（中文等，拼音不支持）→ "m"；首字符须为字母；长度截 40。
    """
    chars: List[str] = []
    for ch in (name or ""):
        if ch.isascii():
            if ch.isalnum() or ch == "_":
                chars.append(ch.lower())
            # 其余 ASCII：剔除
        else:
            chars.append("m")
    slug = "".join(chars)
    if slug and not re.match(r"^[a-z]", slug):
        slug = "m" + slug
    while 0 < len(slug) < 3:
        slug += "m"
    return (slug[:_SLUGIFY_MAX]) or "model"


def _field(step: Any, key: str) -> Any:
    """dict / pydantic 双形态字段读取（graph_spec 与 WorkflowStepSpec 兼容）。"""
    if isinstance(step, dict):
        return step.get(key)
    return getattr(step, key, None)


def _ensure_node_slug(candidate: str, index: int, used: set) -> str:
    """step_id → 合法 node_id：合法 slug 直用；否则确定性地重造并去重。"""
    node_id = candidate if _SLUG_RE.fullmatch(candidate or "") else (
        f"step_{index + 1}_{slugify_name(candidate or '')}")
    base = node_id
    attempt = 0
    while node_id in used:
        attempt += 1
        node_id = f"{base[:40]}_{index + 1}_{attempt}"
    used.add(node_id)
    return node_id


def _first_input_role(step: Any) -> str:
    """step 的首个输入角色（input_roles 值序）→ 数据角色。

    角色 ⊆ DATA_ROLES 原样用；非法/缺失 → 宽 subject（诚实默认：主数据集）。
    """
    roles = _field(step, "input_roles") or {}
    values = list(roles.values()) if isinstance(roles, dict) else []
    raw = str(values[0]) if values else ""
    if raw:
        try:
            from app.services.gis_harness.workflow_schema import DATA_ROLES

            if raw in DATA_ROLES:
                return raw
        except Exception:  # noqa: BLE001 — 词表不可用 → 宽 subject（fail-closed 方向）
            return "subject"
    return "subject"


def _data_node_for(
    role: str, data_by_role: Dict[str, str], nodes: List[ModelNode], used: set
) -> str:
    """按角色取/建 data_input 节点（同角色共享同一输入节点）。"""
    existing = data_by_role.get(role)
    if existing:
        return existing
    node_id = _ensure_node_slug(f"data_{role}", 0, used)
    nodes.append(ModelNode(node_id=node_id, kind="data_input", role=role))
    data_by_role[role] = node_id
    return node_id


def _scalar_params(args: Any) -> Tuple[Dict[str, BoundedValue], List[str]]:
    """args_template → 节点静态参数（只收标量；会话键剔除；越界披露）。"""
    values: Dict[str, BoundedValue] = {}
    skips: List[str] = []
    if not isinstance(args, dict):
        return values, skips
    for key, v in args.items():
        k = str(key)
        if k in _SESSION_BOUND_KEYS:
            continue
        if not _SLUG_RE.fullmatch(k):
            skips.append(f"param {k}: invalid key")
            continue
        if isinstance(v, bool) or isinstance(v, (int, float)):
            values[k] = BoundedValue(value=v)
        elif isinstance(v, str):
            if len(v) > 256:
                skips.append(f"param {k}: exceeds 256 chars")
                continue
            values[k] = BoundedValue(value=v)
        else:
            skips.append(f"param {k}: non-scalar value")
    if len(values) > _MAX_NODE_PARAMS:
        dropped = len(values) - _MAX_NODE_PARAMS
        kept = dict(list(values.items())[:_MAX_NODE_PARAMS])
        skips.append(f"params truncated to {_MAX_NODE_PARAMS} ({dropped} dropped)")
        return kept, skips
    return values, skips


def _document_from_steps(
    steps: List[Any],
    *,
    name: str,
    source_kind: str,
    source_ref: str,
    description: str = "",
) -> Tuple[ModelDocument, List[Dict[str, str]]]:
    """WorkflowStepSpec 列表 → (ModelDocument, skipped)（共享映射核心）。

    - capability 非空 → analysis 节点；缺失 → skipped 披露（tool-only）；
    - 首个输入角色 → data_input（非法角色 → 宽 subject）；有已转换依赖的
      step 数据经上游 analysis 流入（端口单生产者，管线语义）；
    - args_template → 节点 params（标量纪律，越界披露）；
    - dependencies → 边（拓扑；未转换依赖披露）；
    - 每个无出边的 analysis 汇点 → output 节点（单入边，类型干净）。
    """
    skipped: List[Dict[str, str]] = []
    nodes: List[ModelNode] = []
    edges: List[ModelEdge] = []
    data_by_role: Dict[str, str] = {}
    converted: Dict[str, str] = {}   # step_id → node_id
    used_ids: set = set()
    roles: Dict[str, str] = {}       # step_id → 首个输入角色

    # 一遍：capability step → analysis 节点（数据输入边延后统一接线）。
    for idx, step in enumerate(steps):
        step_id = str(_field(step, "step_id") or f"step_{idx + 1}")
        capability = str(_field(step, "capability") or "").strip()
        if not capability:
            skipped.append({
                "step_id": step_id,
                "reason": "no capability (tool-only step not convertible)",
            })
            continue
        node_id = _ensure_node_slug(step_id, idx, used_ids)
        values, param_skips = _scalar_params(_field(step, "args_template"))
        for reason in param_skips:
            skipped.append({"step_id": step_id, "reason": reason})
        nodes.append(ModelNode(
            node_id=node_id,
            kind="analysis",
            capability=capability,
            title=str(_field(step, "description") or "")[:120],
            params=ModelNodeParams(values=values),
        ))
        converted[step_id] = node_id
        roles[step_id] = _first_input_role(step)

    # 二遍：依赖边（允许引用任意先前转换结果；未转换依赖披露）。
    fed_by_deps: set = set()
    for idx, step in enumerate(steps):
        step_id = str(_field(step, "step_id") or f"step_{idx + 1}")
        target = converted.get(step_id)
        if target is None:
            continue
        for dep in (_field(step, "dependencies") or []):
            src = converted.get(str(dep))
            if src is None or src == target:
                skipped.append({
                    "step_id": step_id,
                    "reason": f"dependency {dep} not converted",
                })
                continue
            edges.append(ModelEdge(
                from_node=src, from_port="out", to_node=target, to_port="in"))
            fed_by_deps.add(step_id)

    # 三遍：数据输入边 —— 仅有依赖的 step 数据经上游 analysis 流入
    # （单生产者/端口，管线语义）；其余 step 直连角色 data_input。
    for step_id, role in roles.items():
        if step_id in fed_by_deps:
            continue
        data_id = _data_node_for(role, data_by_role, nodes, used_ids)
        # data_input 的输出端口名 = 角色名（contracts.node_ports 契约）。
        edges.append(ModelEdge(
            from_node=data_id, from_port=role,
            to_node=converted[step_id], to_port="in"))

    # 汇点 → output 节点（每个汇点一个 output，单入边）。
    output_ids: List[str] = []
    for idx, n in enumerate([x for x in nodes if x.kind == "analysis"]):
        if any(e.from_node == n.node_id for e in edges):
            continue
        oid = _ensure_node_slug(f"output_{n.node_id}", 100 + idx, used_ids)
        nodes.append(ModelNode(node_id=oid, kind="output"))
        edges.append(ModelEdge(
            from_node=n.node_id, from_port="out", to_node=oid, to_port="in"))
        output_ids.append(oid)

    doc = ModelDocument(
        name=name[:120],
        description=description[:2000],
        source=ModelSource(kind=source_kind, ref=source_ref[:255]),
        nodes=nodes,
        edges=edges,
        output_node_ids=output_ids,
    )
    return doc, skipped


def _steps_of(graph_spec: Any) -> List[Any]:
    """graph_spec（dict 或 WorkflowGraphSpec）→ steps 列表。"""
    steps = graph_spec.get("steps") if isinstance(graph_spec, dict) \
        else getattr(graph_spec, "steps", None)
    if not isinstance(steps, list):
        raise ValueError("graph_spec must carry a 'steps' list")
    if not steps:
        raise ValueError("graph_spec has no steps to import")
    return steps


def from_project_workflow(
    graph_spec: Dict[str, Any],
    *,
    name: str,
    source_ref: str,
) -> Tuple[ModelDocument, List[Dict[str, str]]]:
    """项目工作流 graph_spec → (ModelDocument, skipped)。确定性转换。

    ``skipped`` 元素形如 {step_id, reason}：诚实披露未转换项（tool-only
    step / 未转换依赖 / 越界参数），绝不静默丢弃。
    """
    return _document_from_steps(
        _steps_of(graph_spec),
        name=name,
        source_kind="project_workflow",
        source_ref=source_ref,
    )


def from_session_plan_steps(
    plan_steps: List[Dict[str, Any]],
    *,
    name: str,
    source_ref: str,
) -> ModelDocument:
    """SessionPlan 行（data_requirements + analysis_steps 形态）→ ModelDocument。

    复用 ``workflow_promotion.build_recipe_steps``（import 复用，不复制）；
    promotion blockers 非空 → ValueError（消息含 blockers，fail-closed ——
    调用方亦可先行自行检查）。
    """
    from app.services.gis_harness.workflow_promotion import (
        build_recipe_steps,
        promotion_blockers,
    )

    rows = [r for r in (plan_steps or []) if isinstance(r, dict)]
    chapter = {"analysis_steps": rows}
    blockers = promotion_blockers(chapter)
    if blockers:
        raise ValueError(
            "plan not promotable (blockers): " + ", ".join(blockers[:8]))
    steps = build_recipe_steps(chapter)
    if not steps:
        raise ValueError("plan has no capability rows to import")
    doc, _skipped = _document_from_steps(
        steps,
        name=name,
        source_kind="session_plan",
        source_ref=source_ref,
    )
    return doc


def from_template(template_doc: ModelDocument, *, name: str) -> ModelDocument:
    """模板实例化：deepcopy + 来源改 template + template_id 填模板 slug。

    节点/参数原样保留（参数是模板的一部分）；name 覆盖为调用方命名。
    ModelDocument 构造即校验（schema 结构闭环）。
    """
    doc = template_doc.model_copy(deep=True)
    template_slug = template_doc.template_id or slugify_name(template_doc.name)
    doc.name = name[:120]
    doc.source = ModelSource(kind="template", ref=template_slug[:255])
    doc.template_id = template_slug[:120]
    return doc


__all__ = [
    "from_project_workflow",
    "from_session_plan_steps",
    "from_template",
    "slugify_name",
]
