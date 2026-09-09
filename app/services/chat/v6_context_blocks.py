"""Contextual Context Assembly（V6 Wave 13）—— 三层上下文投影块。

设计依据 ``.agent-work/contextual-cartographic-harness-v6/07-context-state.md``
W13（三层投影，落现有块机制，不新建通道）：

1. **Node-local Context**：active workflow node（running→failed→repairing
   优先级选当前节点）→ 其 ports/params/obligations/failure/artifact refs
   的有界摘要；无 active node 时块缺席（不编造空块）。
2. **Workflow-global Context**：复用并扩展 ``format_session_plan_projection``
   投影面（DAG 阶段进度、stale 计数、blocked 原因）。
3. **Map Situation Summary**：复用现有 map_state/verdict 块（mapspec
   revision、verdict、render_status、未决 findings 数、组件异常）。

红线：

- 只读投影 —— 全部输入来自既有唯一事实源（SessionPlan store 经
  ``load_session_plan``、plan_graph / runtime_bridge 派生、mapspec store、
  map_state），不建第二 Registry、不建第二通道；LLM 不碰状态。
- 确定性 —— 同输入同输出：固定排序、无时间戳、无随机、无 LLM。
- 有界 —— 每块 hard cap（字节上限常量），超限截断并留痕（``truncated``
  标记 + 行内 ``…(truncated)``），绝不静默丢；字节度量进 trace/结果。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: 三块 hard cap（UTF-8 字节）。超限截断并留痕，绝不静默丢。
NODE_LOCAL_BLOCK_MAX_BYTES = 2048
WORKFLOW_GLOBAL_BLOCK_MAX_BYTES = 4096
MAP_SITUATION_BLOCK_MAX_BYTES = 2048

#: 行内截断标记（ASCII，只占 len 个字节，裁字节预算时预留）。
_TRUNC_SUFFIX_FMT = "…(truncated:{orig}>{cap})"

#: 各维度行内条目上限。
_MAX_LIST_ITEMS = 4
_MAX_TEXT_CHARS = 96


@dataclass(frozen=True)
class V6BlockResult:
    """一块 V6 上下文投影的组装产物（含预算度量）。"""

    name: str
    text: str
    byte_len: int
    byte_cap: int
    truncated: bool

    def metric(self) -> Dict[str, Any]:
        """进 trace/结果的字节度量（``<name>_byte_cost`` 形态）。"""
        return {
            f"{self.name}_byte_cost": self.byte_len,
            f"{self.name}_byte_cap": self.byte_cap,
            f"{self.name}_truncated": self.truncated,
        }


def _utf8_len(text: str) -> int:
    return len(text.encode("utf-8"))


def _clip(value: Any, limit: int = _MAX_TEXT_CHARS) -> str:
    """单值字符级封顶（确定性；非字符串先 str 化）。"""
    text = value if isinstance(value, str) else str(value if value is not None else "")
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _cap_text(text: str, cap: int) -> Tuple[str, bool]:
    """字节级 hard cap：超限按 UTF-8 边界截断并留痕（截断标记计入 cap）。"""
    raw = text.encode("utf-8")
    if len(raw) <= cap:
        return text, False
    marker = _TRUNC_SUFFIX_FMT.format(orig=len(raw), cap=cap)
    budget = max(0, cap - len(marker.encode("utf-8")))
    cut = raw[:budget].decode("utf-8", errors="ignore")
    return cut + marker, True


def _finish(name: str, text: str, cap: int) -> V6BlockResult:
    body, truncated = _cap_text(text, cap)
    return V6BlockResult(
        name=name, text=body,
        byte_len=_utf8_len(body), byte_cap=cap, truncated=truncated,
    )


def _field(row: Any, key: str, default: Any = "") -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _state_str(value: Any) -> str:
    """状态归一化（str Enum → .value；容忍 pydantic PlanNodeStatus）。

    注意 str Enum 的 ``isinstance(value, str)`` 为真，必须先取 ``.value``，
    否则 ``str()`` 得到 ``"PlanNodeStatus.failed"`` 而非 ``"failed"``。
    """
    inner = getattr(value, "value", None)
    if isinstance(inner, str):
        return inner
    if isinstance(value, str):
        return value
    return str(value) if value is not None else ""


# ── Active node 选择 ─────────────────────────────────────────────────────

#: 运行态 → 选择优先级的归一化（repairing > failed > running；数值大者优先）。
#: ``repairing`` = 运行态块上 ``repair_state`` 非空（修复进行中）—— 字段今日
#: 恒空（预留），自然回落到 failed/running，不虚构。
#: ``running`` 覆盖 plan_graph 的 ``running`` 与运行态块的 ``active``
#: （StageState.ACTIVE 即 PlanNodeStatus.running 的运行态投影，同一映射）。
_RUNNING_STATES = frozenset({"running", "active"})
_FAILED_STATES = frozenset({"failed"})


def _node_priority(state: str, repair_state: str) -> int:
    if repair_state:
        return 3
    s = str(state or "")
    if s in _FAILED_STATES:
        return 2
    if s in _RUNNING_STATES:
        return 1
    return 0


def select_active_node(
    plan_nodes: Sequence[Any],
    runtime_nodes: Sequence[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """按 repairing → failed → running 优先级选当前节点（无则 None）。

    输入是 W1/W2 既有投影口的产物（plan_graph 节点 + 运行态块节点），这里
    只读评估。同优先级按 node_id 字典序取首个 —— 与输入顺序无关的规范序，
    同输入必同输出。
    """
    by_cap: Dict[str, Dict[str, Any]] = {}
    for n in list(plan_nodes or []):
        cap = str(_field(n, "capability") or "")
        if not cap:
            continue
        entry = by_cap.setdefault(cap, {"node_id": "", "capability": cap})
        entry["node_id"] = entry["node_id"] or str(_field(n, "node_id") or cap)
        entry["plan_state"] = _state_str(_field(n, "status"))
        entry["plan_node"] = n
    for n in list(runtime_nodes or []):
        if not isinstance(n, dict):
            continue
        cap = str(n.get("capability") or "")
        node_id = str(n.get("node_id") or "")
        key = cap or node_id
        if not key:
            continue
        entry = by_cap.setdefault(key, {"node_id": node_id, "capability": cap})
        entry["node_id"] = entry["node_id"] or node_id
        entry["runtime_state"] = str(n.get("state") or "")
        entry["repair_state"] = str(n.get("repair_state") or "")
        entry["runtime_node"] = n
    best: Optional[Dict[str, Any]] = None
    best_rank: Tuple[int, str] = (0, "")
    for key in by_cap:
        entry = by_cap[key]
        prio = max(
            _node_priority(entry.get("plan_state", ""), ""),
            _node_priority(entry.get("runtime_state", ""), entry.get("repair_state", "")),
        )
        if prio <= 0:
            continue
        # 优先级大者胜；同级按 node_id 字典序取最小（与输入顺序无关的规范序）。
        rank = (prio, entry["node_id"] or key)
        if best is None or (rank[0] > best_rank[0]) or (
            rank[0] == best_rank[0] and rank[1] < best_rank[1]
        ):
            best, best_rank = entry, rank
    return best


# ── Node-local 块 ────────────────────────────────────────────────────────

def _project_ports(capability: str, algorithm: str) -> Tuple[List[str], List[str]]:
    """端口投影（算法层是端口类型单一事实源；失败时诚实空列表）。

    复用 ``typed_dag._analysis_ports`` 的确定性投影（同 capability +
    同算法 → 同端口），不编译整图、不碰状态。注册表不可用 → 空（调用方
    按缺席披露，不虚构）。
    """
    try:
        from app.services.gis_harness.workflow_v4.typed_dag import (
            _analysis_ports,
        )
        ins, outs = _analysis_ports(capability, algorithm)
    except Exception:  # noqa: BLE001 — 端口是增值细节，缺席即披露
        return [], []
    def _fmt(ports: Any) -> List[str]:
        out: List[str] = []
        for p in list(ports or [])[:_MAX_LIST_ITEMS]:
            name = _clip(_field(p, "name"), 24)
            atype = _clip(_field(p, "artifact_type"), 40)
            out.append(f"{name}:{atype}" if atype else name)
        return out
    return _fmt(ins), _fmt(outs)


def _matched_obligations(
    obligations: Sequence[Any], capability: str, algorithm: str,
) -> List[str]:
    """与当前节点相关的合同义务（obligation_id 列表，有界）。

    合同义务是整图级事实 —— 只收录 provenance/标识文本中出现本节点
    capability/算法名的条目；无关联 → 空（不把全局义务塞进 node-local）。
    """
    cap = (capability or "").strip()
    alg = (algorithm or "").strip()
    if not cap and not alg:
        return []
    matched: List[str] = []
    for o in list(obligations or []):
        oid = str(_field(o, "obligation_id") or "")
        kind = str(_field(o, "kind") or "")
        code = str(_field(o, "warning_code") or "")
        prov = " ".join(
            str(_field(p, "source") or "") + " " + str(_field(p, "detail") or "")
            for p in (list(_field(o, "provenance") or [])[:4])
        )
        hay = f"{oid} {kind} {code} {prov}"
        if (cap and cap in hay) or (alg and alg in hay):
            if oid and oid not in matched:
                matched.append(_clip(oid, 64))
        if len(matched) >= 3:
            break
    return matched


def merge_node_view(
    entry: Dict[str, Any],
    *,
    obligations: Sequence[Any] = (),
) -> Dict[str, Any]:
    """plan 节点 + 运行态节点 → node-local 归一视图（纯投影，不持久化）。"""
    plan_node = entry.get("plan_node")
    runtime_node = entry.get("runtime_node") if isinstance(
        entry.get("runtime_node"), dict) else {}
    capability = str(entry.get("capability") or "")
    algorithm = _clip(_field(plan_node, "resolved_algorithm"), 64) if plan_node is not None else ""
    if not algorithm and runtime_node:
        algorithm = _clip(runtime_node.get("algorithm") or "", 64)
    tool = _clip(_field(plan_node, "resolved_tool"), 64) if plan_node is not None else ""
    params: Dict[str, str] = {}
    if plan_node is not None:
        raw_params = _field(plan_node, "params", None)
        if isinstance(raw_params, dict):
            for k in sorted(raw_params)[:_MAX_LIST_ITEMS]:
                params[str(k)[:32]] = _clip(raw_params[k], 64)
    ins, outs = _project_ports(capability, algorithm)
    bound_ref = ""
    input_refs: List[str] = []
    if plan_node is not None:
        bound_ref = str(_field(plan_node, "bound_ref") or _field(plan_node, "output_ref") or "")
        for r in list(_field(plan_node, "input_refs") or [])[:_MAX_LIST_ITEMS]:
            if r and r not in input_refs:
                input_refs.append(_clip(r, 64))
    if runtime_node:
        bound_ref = bound_ref or str(runtime_node.get("bound_ref") or "")
        for r in list(runtime_node.get("inputs") or [])[:_MAX_LIST_ITEMS]:
            r = _clip(r, 64)
            if r and r not in input_refs:
                input_refs.append(r)
    failure = ""
    if runtime_node:
        failure = str(
            runtime_node.get("failure_class")
            or runtime_node.get("stale_reason")
            or runtime_node.get("evidence")
            or ""
        )
    if plan_node is not None and not failure:
        blocked = list(_field(plan_node, "blocked_by") or [])[:2]
        notes = list(_field(plan_node, "notes") or [])[:2]
        failure = ",".join(_clip(x, 48) for x in blocked + notes if x)
    state = str(entry.get("runtime_state") or entry.get("plan_state") or "")
    repair = str(entry.get("repair_state") or "")
    if repair:
        state = f"{state}+repairing:{_clip(repair, 32)}" if state else f"repairing:{_clip(repair, 32)}"
    return {
        "node_id": str(entry.get("node_id") or capability),
        "capability": capability,
        "state": state,
        "tool": tool,
        "algorithm": algorithm,
        "ports_in": ins,
        "ports_out": outs,
        "params": params,
        "obligations": _matched_obligations(obligations, capability, algorithm),
        "failure": _clip(failure, _MAX_TEXT_CHARS),
        "bound_ref": _clip(bound_ref, 64),
        "input_refs": input_refs,
    }


def active_node_view(chapter: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """章节 → active 节点归一视图（无 active node 返回 None）。

    装配路径与 Pi 路径共用（同一选择 + 同一合并，非第二通道）。W1/W2
    既有投影口（plan_graph 节点 + 运行态块节点）只读评估；图构建失败 →
    None（诚实缺席）。
    """
    if not isinstance(chapter, dict) or not chapter:
        return None
    try:
        from app.services.gis_harness.plan_graph import build_plan_graph
        from app.services.gis_harness.runtime_bridge import WORKFLOW_RUNTIME_KEY

        graph = build_plan_graph(chapter)
        runtime = chapter.get(WORKFLOW_RUNTIME_KEY)
        runtime_nodes = (
            list(runtime.get("nodes") or [])
            if isinstance(runtime, dict) else []
        )
        active = select_active_node(graph.nodes, runtime_nodes)
        if active is None:
            return None
        contract = chapter.get("workflow_contract")
        obligations = (
            list(contract.get("obligations") or [])
            if isinstance(contract, dict) else []
        )
        return merge_node_view(active, obligations=obligations)
    except Exception:  # noqa: BLE001 — 节点视图缺席即块缺席
        return None


def build_node_local_block(view: Optional[Dict[str, Any]]) -> Optional[V6BlockResult]:
    """归一视图 → ``[Node-local Context]`` 有界块；无 active node 返回 None。"""
    if not view:
        return None
    ports_in = ",".join(view.get("ports_in") or []) or "none"
    ports_out = ",".join(view.get("ports_out") or []) or "none"
    params = view.get("params") or {}
    params_s = ",".join(f"{k}={v}" for k, v in params.items()) or "none"
    obligations = ",".join(view.get("obligations") or []) or "none"
    bound = view.get("bound_ref") or "none"
    inputs = ",".join(view.get("input_refs") or []) or "none"
    lines = [
        f"[Node-local Context] node={_clip(view.get('node_id'), 64)} "
        f"capability={_clip(view.get('capability'), 64)} "
        f"state={_clip(view.get('state'), 64)}",
        f"ports: in({ports_in}) -> out({ports_out})",
        f"params: {params_s}",
        f"method: tool={view.get('tool') or 'none'} algorithm={view.get('algorithm') or 'none'}",
        f"obligations: {obligations}",
        f"failure: {view.get('failure') or 'none'}",
        f"artifact_refs: bound={bound} inputs={inputs}",
    ]
    return _finish("node_local", "\n".join(lines), NODE_LOCAL_BLOCK_MAX_BYTES)


# ── Workflow-global 块 ───────────────────────────────────────────────────

def format_plan_progress_line(chapter: Optional[Dict[str, Any]]) -> str:
    """``[GIS Plan Progress]`` 单行投影（DAG 阶段进度、stale 计数、blocked 原因）。

    ``format_session_plan_projection`` 的 additive 扩展行：首行契约不变，
    无 DAG 的章节返回空串（零漂移）。全部取数自既有投影口（plan_graph、
    runtime_bridge stale 口、workflow_contract 阻断词），纯派生。
    """
    if not isinstance(chapter, dict):
        return ""
    if not chapter.get("data_requirements") and not chapter.get("analysis_steps"):
        return ""
    try:
        from app.services.gis_harness.plan_graph import build_plan_graph

        graph = build_plan_graph(chapter)
    except Exception:  # noqa: BLE001 — 进度行是增值信号，绝不阻断投影
        return ""
    counts: Dict[str, int] = {}
    for n in graph.nodes:
        counts[n.status.value] = counts.get(n.status.value, 0) + 1
    total = len(graph.nodes)
    stale: List[str] = []
    try:
        from app.services.gis_harness.completion.unified_findings import (
            stale_runtime_nodes,
        )
        from app.services.gis_harness.runtime_bridge import WORKFLOW_RUNTIME_KEY

        stale = stale_runtime_nodes(chapter.get(WORKFLOW_RUNTIME_KEY))
    except Exception:  # noqa: BLE001 — stale 计数缺席即按 0 披露
        stale = []
    blocked: List[str] = []
    for n in graph.nodes:
        for b in list(n.blocked_by or [])[:2]:
            reason = f"{n.capability}<-{_clip(b, 40)}"
            if reason not in blocked:
                blocked.append(reason)
            if len(blocked) >= 3:
                break
        if len(blocked) >= 3:
            break
    if len(blocked) < 3:
        contract = chapter.get("workflow_contract")
        if isinstance(contract, dict):
            for b in list(contract.get("data_blockers") or [])[:2]:
                reason = f"data:{_clip(b, 40)}"
                if reason not in blocked:
                    blocked.append(reason)
                if len(blocked) >= 3:
                    break
        if isinstance(contract, dict) and len(blocked) < 3:
            for b in list(contract.get("method_blockers") or [])[:2]:
                reason = f"method:{_clip(b, 40)}"
                if reason not in blocked:
                    blocked.append(reason)
                if len(blocked) >= 3:
                    break
    parts = [
        f"[GIS Plan Progress] total={total}",
        f"ready={counts.get('ready', 0)}",
        f"running={counts.get('running', 0)}",
        f"complete={counts.get('complete', 0)}",
        f"failed={counts.get('failed', 0)}",
        f"unavailable={counts.get('unavailable', 0)}",
        f"stale={len(stale)}",
    ]
    if stale:
        parts.append("stale_nodes=" + ",".join(_clip(s, 32) for s in stale[:3]))
    parts.append("blocked=" + (",".join(blocked) if blocked else "none"))
    line = " ".join(parts)
    return line if _utf8_len(line) <= 480 else _cap_text(line, 480)[0]


def build_workflow_global_block(
    plan: Any,
    mapspec: Optional[Dict[str, Any]] = None,
) -> Optional[V6BlockResult]:
    """SessionPlan → ``[Workflow-global Context]`` 有界块；无计划返回 None。

    复用 ``format_session_plan_projection`` 投影面（含 W13 扩展的进度行），
    整体再进 hard cap（超限截断留痕）。
    """
    if plan is None or getattr(plan, "gis_chapter", None) is None:
        return None
    from app.services.session_plan import format_session_plan_projection

    base = format_session_plan_projection(plan, mapspec)
    text = "[Workflow-global Context]\n" + base
    return _finish("workflow_global", text, WORKFLOW_GLOBAL_BLOCK_MAX_BYTES)


# ── Map Situation 块 ─────────────────────────────────────────────────────

def _verdict_token_for(status: str) -> str:
    """三态 verdict token（与 ``verdict_summary._verdict_token`` 同规则的投影复述。

    ``partial`` = 证据不完整 → not_evaluated（无证据 ≠ 修正压力）；只有真实
    失败态才映射 fail。权威实现见 ``app/lib/cartography/verdict_summary.py``。
    """
    if status in ("passed", "passed_with_warnings"):
        return "pass"
    if status in ("not_evaluated", "partial"):
        return "not_evaluated"
    return "fail"


def build_map_situation_block(
    *,
    map_state: Optional[Dict[str, Any]] = None,
    mapspec: Optional[Dict[str, Any]] = None,
    review: Optional[Dict[str, Any]] = None,
    current_fingerprint: Optional[str] = None,
    chapter: Optional[Dict[str, Any]] = None,
    render_diagnostics: Optional[List[Dict[str, Any]]] = None,
) -> V6BlockResult:
    """``[Map Situation]`` 有界块（复用现有 map_state/verdict 块取数口）。

    - mapspec revision：``map_state[_cartographic_mutation_revision]``（store
      自身的取数口；缺席时回落 mapspec 内整数字段；皆无 → none）。
    - verdict：``should_inject_verdict`` 同守卫（跨代/不可验证不注入 → none）。
    - render_status：章节 ``map_product.render_status``（缺席 → unknown）。
    - 未决 findings 数：``collect_unified_findings`` 统一投影（result 缺席时
      只计运行态块 stale/failed 节点 + 渲染诊断；没有完成引擎第二通道）。
    - 组件异常：scope=component 的 findings（码:实体，有界）。
    """
    state = map_state if isinstance(map_state, dict) else {}
    spec = mapspec if isinstance(mapspec, dict) else {}
    chap = chapter if isinstance(chapter, dict) else {}
    rev: Any = state.get("_cartographic_mutation_revision")
    if not isinstance(rev, int):
        for key in ("revision", "mutation_revision"):
            cand = spec.get(key)
            if isinstance(cand, int):
                rev = cand
                break
    rev_s = str(rev) if isinstance(rev, int) else "none"
    verdict = "none"
    if isinstance(review, dict):
        try:
            from app.lib.cartography.verdict_summary import should_inject_verdict

            if should_inject_verdict(review, current_fingerprint):
                cart = review.get("cartography")
                status = str(cart.get("status") or "not_evaluated") if isinstance(cart, dict) else "not_evaluated"
                verdict = _verdict_token_for(status)
        except Exception:  # noqa: BLE001 — verdict 缺席即 none，不阻断块
            verdict = "none"
    render_status = "unknown"
    product = chap.get("map_product")
    if isinstance(product, dict) and product.get("render_status"):
        render_status = _clip(product.get("render_status"), 32)
    findings: List[Any] = []
    try:
        from app.services.gis_harness.completion.unified_findings import (
            collect_unified_findings,
        )
        from app.services.gis_harness.runtime_bridge import WORKFLOW_RUNTIME_KEY

        findings = collect_unified_findings(
            result=None,
            runtime_block=chap.get(WORKFLOW_RUNTIME_KEY),
            render_diagnostics=render_diagnostics,
        )
    except Exception:  # noqa: BLE001 — findings 缺席即 0，不阻断块
        findings = []
    blocking = sum(
        1 for f in findings
        if getattr(f, "blocks_completion", False) or getattr(f, "severity", "") == "error"
    )
    anomalies = [
        f"{_clip(getattr(f, 'code', ''), 48)}:{_clip(getattr(f, 'affected_entity', ''), 48)}"
        for f in findings
        if getattr(f, "scope", "") == "component"
    ][:3]
    lines = [
        f"[Map Situation] mapspec_rev={rev_s} verdict={verdict} render={render_status}",
        f"findings: pending={len(findings)} blocking={blocking}",
        f"components: {','.join(anomalies) if anomalies else 'none'}",
    ]
    return _finish("map_situation", "\n".join(lines), MAP_SITUATION_BLOCK_MAX_BYTES)


def blocks_metric(results: Sequence[V6BlockResult]) -> Dict[str, Any]:
    """三块字节度量（进 trace/budget_report，供测试断言）。"""
    metric: Dict[str, Any] = {}
    total = 0
    truncated: List[str] = []
    for r in results:
        metric.update(r.metric())
        total += r.byte_len
        if r.truncated:
            truncated.append(r.name)
    metric["total_byte_cost"] = total
    metric["truncated"] = sorted(truncated)
    return metric


__all__ = [
    "NODE_LOCAL_BLOCK_MAX_BYTES",
    "WORKFLOW_GLOBAL_BLOCK_MAX_BYTES",
    "MAP_SITUATION_BLOCK_MAX_BYTES",
    "V6BlockResult",
    "select_active_node",
    "merge_node_view",
    "active_node_view",
    "build_node_local_block",
    "format_plan_progress_line",
    "build_workflow_global_block",
    "build_map_situation_block",
    "blocks_metric",
]
