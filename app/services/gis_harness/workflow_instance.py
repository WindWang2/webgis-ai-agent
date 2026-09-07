"""WorkflowInstance —— 工作流运行态状态机（V4 / ADR-0104 Wave 1）。

现状（Phase-0 审计 02）：生产路径只有「静态 compile + finalize 快照」——
``gis_chapter["map_product"]`` 在去重门后面整体重验；没有实例身份、没有
单调状态版本、没有逐阶段证据版本；qualification/obligation 的 blocked
态在数据到位后不会重算；``rows_fingerprint`` 对参数/算法变化失明。

本模块把快照升级为可持续更新的 **WorkflowInstance 运行态投影**：

    SessionPlan.gis_chapter（唯一计划事实，_mark_progress 单写者）
          ↓ derive_workflow_instance（纯函数，O(nodes)，确定性）
    WorkflowInstanceState（有界运行态块）
          持久化于 gis_chapter["workflow_instance"]（单键，additive）

红线：

- **不是第二事实源**：行状态仍只由 SessionPlan ``_mark_progress`` 写；
  workflow_contract 的规范写者仍是 planner finalize / merge 协议。实例
  块唯一的回写是「数据到位解除阻断」方向上的科学契约重算——复用同一
  纯评估器（resolve_data_roles / evaluate_workflow_obligations），不是
  第二套科学语义；阻断增多方向只披露，等 finalize 以真实画像裁决。
- **确定性**：同输入同输出——块内无时间戳，转移记录只有 StateRevision；
  指纹一律 canonical-JSON sha256。
- **风格/科学分离**：事件携带 RECOMPUTE_DIMENSIONS 维度；style 事件只
  推进呈现态，科学阶段零触碰。
- **有界**：stages/deps/transitions/blockers 全部截断；旧读者忽略新键。

触发点与 map_product 相同（工具结果 / turn 收尾 / render observation），
但门更廉价：``gate_fingerprint = H(rows_v2 + revision + render_seq +
contract_fp)``——不变即跳过，不跑终验。
"""
from __future__ import annotations

import hashlib
import json
import os
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

from pydantic import BaseModel, Field

import logging

logger = logging.getLogger(__name__)

# ── 词汇表 ───────────────────────────────────────────────────────────────

#: gis_chapter 单键（additive；旧读者忽略）。
WORKFLOW_INSTANCE_KEY = "workflow_instance"


def _enabled() -> bool:
    """降级开关（默认开）。"""
    return os.getenv("GIS_WORKFLOW_INSTANCE", "1") not in ("0", "false", "False")


class WorkflowEventKind(str, Enum):
    """改变工作流运行态的一等事件（审计 02 §A4：会话面此前无事件概念）。"""

    DATA_ARRIVED = "data_arrived"        # capability 行绑定新 ref / 数据到位
    ARTIFACT_PRODUCED = "artifact_produced"
    ARTIFACT_STALE = "artifact_stale"    # 上游 artifact 失效（ref 死亡/过期）
    ALGORITHM_CHANGE = "algorithm_change"
    PARAMETER_CHANGE = "parameter_change"
    STYLE_MUTATION = "style_mutation"    # MapSpec 呈现突变（style-only）
    OBSERVATION = "observation"          # render observation 到达
    TOOL_FAILURE = "tool_failure"

    @property
    def dimensions(self) -> Tuple[str, ...]:
        """事件 → RECOMPUTE_DIMENSIONS 子集（workflow_schema 单一词表）。"""
        return {
            "data_arrived": ("data",),
            "artifact_produced": ("output",),
            "artifact_stale": ("data", "output"),
            "algorithm_change": ("algorithm",),
            "parameter_change": ("parameter",),
            "style_mutation": ("style",),
            "observation": ("output",),
            "tool_failure": ("output",),
        }[self.value]


class StageState(str, Enum):
    """实例阶段状态（PlanNodeStatus 的运行态投影 + stale 维度）。"""

    PENDING = "pending"
    READY = "ready"
    ACTIVE = "active"
    SATISFIED = "satisfied"
    BLOCKED = "blocked"
    STALE = "stale"          # 证据曾满足，但输入事实已漂移 → 待重算
    SKIPPED = "skipped"
    FAILED = "failed"


#: PlanNodeStatus → StageState（1:1 投影；「unavailable」→ BLOCKED）。
_NODE_TO_STAGE: Dict[str, StageState] = {
    "pending": StageState.PENDING,
    "ready": StageState.READY,
    "running": StageState.ACTIVE,
    "complete": StageState.SATISFIED,
    "unavailable": StageState.BLOCKED,
    "skipped": StageState.SKIPPED,
    "failed": StageState.FAILED,
}

#: DependencyState：边级投影。
DEPENDENCY_STATES = ("satisfied", "blocked", "stale", "unknown")

#: BlockedReason 统一前缀：把既有阻断词表映射为单一可读码
#: （不替换原码——原码作为 ``:`` 后细节保留）。
BLOCK_DATA = "BLOCKED_BY_DATA"
BLOCK_METHOD = "BLOCKED_BY_METHOD"
BLOCK_DEPENDENCY = "BLOCKED_BY_DEPENDENCY"
BLOCK_EXECUTION = "BLOCKED_BY_EXECUTION"
BLOCK_RENDER = "BLOCKED_BY_RENDER"

#: 有界预算。
MAX_STAGES = 24
MAX_DEPENDENCIES = 32
MAX_TRANSITIONS = 32
MAX_BLOCKERS = 8
MAX_REASON = 96


# ── 指纹 ─────────────────────────────────────────────────────────────────

def canonical_fingerprint(payload: Any) -> str:
    """canonical-JSON sha256（与 workflow_schema/recipe 指纹同规，截 32）。"""
    try:
        canonical = json.dumps(
            payload, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), default=str,
        )
    except Exception:  # noqa: BLE001 — 指纹失败退化为 repr（诚实退化）
        canonical = repr(payload)
    return hashlib.sha256(canonical.encode("utf-8"), usedforsecurity=False).hexdigest()[:32]


def _row_params_hash(row: Dict[str, Any]) -> str:
    """行参数的内容哈希（rows_fingerprint V2：参数编辑必须可见）。"""
    params = row.get("params")
    if not params:
        return ""
    return canonical_fingerprint(params)[:12]


def row_signature(row: Any) -> str:
    """单行签名：capability:status:bound_ref:algorithm:params_hash。

    V2 修复审计 02 §A4 的洞——旧签名（capability:status:bound_ref）对
    parameter/algorithm 编辑失明，参数-only 编辑后陈旧 verdict 被去重门
    永久保护。同输入同签名（确定性契约由测试钉住）。
    """
    if not isinstance(row, dict):
        return ""
    return ":".join((
        str(row.get("capability") or ""),
        str(row.get("status") or ""),
        str(row.get("bound_ref") or ""),
        str(row.get("resolved_algorithm") or ""),
        _row_params_hash(row),
    ))


def rows_fingerprint(chapter: Dict[str, Any]) -> str:
    """行状态指纹 V2（completion pipeline 去重门共用本实现——单一计算源）。"""
    parts: List[str] = []
    for row in list(chapter.get("data_requirements") or []) + list(
        chapter.get("analysis_steps") or []
    ):
        sig = row_signature(row)
        if sig:
            parts.append(sig)
    return "|".join(sorted(parts))


# ── 模型 ─────────────────────────────────────────────────────────────────

class StageEvidence(BaseModel):
    """阶段证据版本：staleness = 指纹失配，从不猜测。"""

    fingerprint: str = ""            # 该阶段输入事实（行签名）的内容指纹
    source_rows_fingerprint: str = ""  # 派生时的整章行指纹（[:32]）
    status: str = "current"          # current | stale | unknown

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "fingerprint": self.fingerprint[:32],
            "source_rows_fingerprint": self.source_rows_fingerprint[:32],
            "status": self.status,
        }


class InstanceStage(BaseModel):
    """一个 capability 节点的运行态（PlanGraph 节点投影 + 证据版本）。"""

    capability: str
    kind: str = "analysis"                    # requirement | analysis
    state: str = StageState.PENDING.value
    blocked_reasons: List[str] = Field(default_factory=list)
    resolved_algorithm: str = ""
    bound_ref: str = ""
    evidence: StageEvidence = Field(default_factory=StageEvidence)

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "capability": self.capability[:64],
            "kind": self.kind,
            "state": self.state,
            "blocked_reasons": [r[:MAX_REASON] for r in self.blocked_reasons[:4]],
            "resolved_algorithm": self.resolved_algorithm[:64],
            "bound_ref": self.bound_ref[:64],
            "evidence": self.evidence.to_bounded_dict(),
        }


class InstanceDependency(BaseModel):
    """一条依赖边（consumer ← producer）的运行态。"""

    consumer: str
    producer: str
    state: str = "unknown"                    # satisfied | blocked | stale | unknown

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "consumer": self.consumer[:64],
            "producer": self.producer[:64],
            "state": self.state,
        }


class StateTransition(BaseModel):
    """一次状态转移记录（有界环形；无时间戳——StateRevision 即序）。"""

    revision: int
    event: str = ""                            # WorkflowEventKind 值 / auto
    capability: str = ""                       # 空 = 实例级转移
    from_state: str = ""
    to_state: str = ""
    reason_code: str = ""

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "revision": self.revision,
            "event": self.event[:32],
            "capability": self.capability[:64],
            "from_state": self.from_state[:16],
            "to_state": self.to_state[:16],
            "reason_code": self.reason_code[:MAX_REASON],
        }


class ScienceRecheck(BaseModel):
    """科学契约重算裁决（V4 核心行为：blocked 态随数据到位自动重评）。"""

    evaluated: bool = False
    recomputed_fingerprint: str = ""           # 重算 contract 核心的指纹
    divergent: bool = False                    # 与存储 contract 不一致
    direction: str = ""                        # unblocked | worsened | changed | equal
    data_blockers: List[str] = Field(default_factory=list)
    method_blockers: List[str] = Field(default_factory=list)
    disclosure: str = ""

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "evaluated": self.evaluated,
            "recomputed_fingerprint": self.recomputed_fingerprint[:32],
            "divergent": self.divergent,
            "direction": self.direction[:16],
            "data_blockers": [b[:64] for b in self.data_blockers[:MAX_BLOCKERS]],
            "method_blockers": [b[:64] for b in self.method_blockers[:MAX_BLOCKERS]],
            "disclosure": self.disclosure[:200],
        }


class WorkflowInstanceState(BaseModel):
    """工作流实例运行态块（gis_chapter[WORKFLOW_INSTANCE_KEY] 的 schema）。"""

    instance_id: str                            # session:envelope:plan
    plan_id: str = ""
    state_revision: int = 1                     # 单调递增（内容变化 +1）
    state_fingerprint: str = ""                 # 派生状态的 canonical 指纹
    gate_fingerprint: str = ""                  # 去重门键（rows+rev+seq+contract）
    rows_fingerprint: str = ""
    checked_revision: int = 0                   # MapSpec mutation revision
    render_observation_seq: int = 0
    stages: List[InstanceStage] = Field(default_factory=list)
    dependencies: List[InstanceDependency] = Field(default_factory=list)
    science: ScienceRecheck = Field(default_factory=ScienceRecheck)
    # 完成维的有界投影（COMPLETION_DIMENSIONS 子集：ok/pending/blocked/
    # degraded/unknown）。
    dimensions: Dict[str, str] = Field(default_factory=dict)
    transitions: List[StateTransition] = Field(default_factory=list)
    last_event: str = ""

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "schema": "workflow_instance.v1",
            "instance_id": self.instance_id[:128],
            "plan_id": self.plan_id[:64],
            "state_revision": self.state_revision,
            "state_fingerprint": self.state_fingerprint,
            "gate_fingerprint": self.gate_fingerprint,
            "rows_fingerprint": self.rows_fingerprint[:2048],
            "checked_revision": self.checked_revision,
            "render_observation_seq": self.render_observation_seq,
            "stages": [s.to_bounded_dict() for s in self.stages[:MAX_STAGES]],
            "dependencies": [
                d.to_bounded_dict() for d in self.dependencies[:MAX_DEPENDENCIES]
            ],
            "science": self.science.to_bounded_dict(),
            "dimensions": dict(list(self.dimensions.items())[:8]),
            "transitions": [t.to_bounded_dict() for t in self.transitions[:MAX_TRANSITIONS]],
            "last_event": self.last_event[:32],
        }


# ── 科学契约重算（纯函数；解除方向由服务回写）────────────────────────────

def _contract_core(contract: Dict[str, Any]) -> Dict[str, Any]:
    """contract 的语义核心（重算可比对面；忽略 provenance 键）。"""
    return {
        "roles": contract.get("roles") or [],
        "obligations": contract.get("obligations") or [],
        "method_blockers": contract.get("method_blockers") or [],
        "data_blockers": contract.get("data_blockers") or [],
    }


def derive_unblock_contract(chapter: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """数据到位后的**最小安全**契约解除（纯函数，不写任何状态）。

    review BLOCKER 修正（Round-1 #1/#2）：本函数**不再**重跑完整评估器
    —— 无画像事实时 precondition 走 deferred-PASS / unknown，重算系统性
    偏宽松，count 比较会把「事实缺席」误判成「阻断解除」，把
    BLOCKED_BY_* 洗成 READY。改为**单调解除**：

    - 绑定规则与 planner finalize 完全同源（R1-A3/R2-9）：只认
      ``data_requirements`` 行、status ∈ {available, done} 且带
      bound_ref；同一 capability_hint 服务多角色时声明序首个胜出；
    - 只解除**有新绑定证据**的角色：基线 roles 中非 bound、而本次
      绑定规则下已 bound 的角色（缺新证据 ⇒ None，零重写）；
    - 契约只改两处：被解除角色的 role status/bound_ref，与
      ``data_blockers`` 中对应的角色名；
    - ``method_blockers`` / ``obligations`` / ``warnings`` **逐字保留**
      基线 —— 方法阻断依赖画像事实，无事实绝不放松（review #1）。

    返回 None = 无 recipe/无画像/无新证据/异常（调用方保持现契约）。
    """
    contract = chapter.get("workflow_contract")
    recipe_id = str(chapter.get("recipe_id") or "")
    if not isinstance(contract, dict) or not recipe_id:
        return None
    try:
        from app.services.gis_harness.recipes import get_recipe_registry

        recipe = get_recipe_registry().get(recipe_id)
        wf_profile = getattr(recipe, "workflow", None) if recipe is not None else None
        if wf_profile is None:
            return None
        bound_refs = _derive_bound_refs(chapter, wf_profile)
        if not bound_refs:
            return None
        baseline_roles = [
            r for r in contract.get("roles") or [] if isinstance(r, dict)
        ]
        baseline_bound = {
            str(r.get("role"))
            for r in baseline_roles if str(r.get("status") or "") == "bound"
        }
        newly_bound = {
            role for role in bound_refs if role not in baseline_bound
        }
        if not newly_bound:
            return None
        new_contract = dict(contract)
        new_roles: List[Dict[str, Any]] = []
        for r in baseline_roles:
            role_name = str(r.get("role") or "")
            if role_name in newly_bound:
                updated = dict(r)
                updated["status"] = "bound"
                updated["bound_ref"] = str(bound_refs[role_name])[:64]
                new_roles.append(updated)
            else:
                new_roles.append(dict(r))
        # 声明了角色但基线 roles 表缺席（旧契约形状）→ 追加 bound 条目。
        known = {str(r.get("role") or "") for r in new_roles}
        for role in sorted(newly_bound - known):
            new_roles.append({
                "role": role, "required": True, "acquisition": "local",
                "status": "bound", "bound_ref": str(bound_refs[role])[:64],
                "source_capability": "", "missing_policy": "block",
                "reason_code": "", "disclosure": "",
            })
        new_contract["roles"] = new_roles[:16]
        baseline_data_blockers = [
            str(b) for b in contract.get("data_blockers") or []
        ]
        new_contract["data_blockers"] = [
            b for b in baseline_data_blockers if b not in newly_bound
        ]
        # method_blockers / obligations / warnings 逐字保留（见 docstring）。
        new_contract["recheck"] = {
            "source": "data_arrival",
            "unblocked_roles": sorted(newly_bound)[:8],
            "fingerprint": canonical_fingerprint({
                "roles": new_contract["roles"],
                "data_blockers": new_contract["data_blockers"],
                "method_blockers": new_contract.get("method_blockers") or [],
            }),
        }
        return new_contract
    except Exception:  # noqa: BLE001 — 重算是增值路径，失败保持现契约
        return None


def _derive_bound_refs(chapter: Dict[str, Any], profile: Any) -> Dict[str, str]:
    """角色 → bound_ref（与 planner finalize 同规则：R1-A3 + R2-9）。

    只认 ``data_requirements`` 行（step 行是执行投影不是数据在场证据）；
    status ∈ {available, done} 且带 bound_ref；同一 capability_hint 服务
    多角色时按声明序首个胜出。
    """
    role_by_cap: Dict[str, str] = {}
    for req in getattr(profile, "data_roles", []) or []:
        hint = getattr(req, "capability_hint", "")
        if hint and hint not in role_by_cap:
            role_by_cap[hint] = req.role
    bound: Dict[str, str] = {}
    for row in chapter.get("data_requirements") or []:
        if not isinstance(row, dict):
            continue
        cap = str(row.get("capability") or "")
        role_name = role_by_cap.get(cap)
        if (role_name and str(row.get("status") or "") in ("available", "done")
                and row.get("bound_ref")):
            bound[role_name] = str(row["bound_ref"])
    return bound


# ── 纯派生 ───────────────────────────────────────────────────────────────

def _stage_evidence(node_signature: str, rows_fp: str, *, stale: bool) -> StageEvidence:
    return StageEvidence(
        fingerprint=canonical_fingerprint(node_signature),
        source_rows_fingerprint=rows_fp[:32],
        status="stale" if stale else "current",
    )


def _dependency_state(
    dep_node: Any,
    consumer_state: StageState,
    dep_stale: bool,
) -> str:
    """边级状态：上游满足即 satisfied；上游证据漂移 → stale（待重算）；
    上游 blocked/failed → blocked。"""
    if dep_node is None:
        return "unknown"
    dep_stage = _NODE_TO_STAGE.get(
        getattr(getattr(dep_node, "status", "pending"), "value",
                getattr(dep_node, "status", "pending")),
        StageState.PENDING)
    if dep_stale:
        return "stale"
    if dep_stage in (StageState.SATISFIED, StageState.SKIPPED):
        return "satisfied"
    if dep_stage in (StageState.BLOCKED, StageState.FAILED):
        return "blocked"
    if consumer_state in (StageState.BLOCKED, StageState.STALE):
        return "blocked"
    return "unknown"


def derive_workflow_instance(
    chapter: Dict[str, Any],
    *,
    instance_id: str,
    mapspec_revision: int,
    render_seq: int,
    stored: Optional[Dict[str, Any]] = None,
    event: str = "auto",
    recomputed_contract: Optional[Dict[str, Any]] = None,
    science_baseline: Optional[Dict[str, Any]] = None,
) -> WorkflowInstanceState:
    """从章节纯派生实例态（确定性、O(nodes)、零 I/O）。

    ``stored`` 是已持久化的旧块（dict 或 None）。提供时：
    - 证据失配（行签名变化）的**已满足**阶段标记 STALE，并沿 reverse
      依赖闭包只污染下游（「只 invalidate 受影响 downstream」）；
    - StateRevision 在状态内容变化时 +1（内容不变则保持——同输入同输出）；
    - 转移记录环形合并（≤MAX_TRANSITIONS）。

    ``recomputed_contract``（可选）是 ``recompute_workflow_contract`` 的
    输出：提供时科学维比较重算 vs ``science_baseline``（缺省 = 章节当前
    contract）。``science_baseline`` 是比较基准——服务回写 contract 后的
    重派生必须仍对**写前**契约裁决（否则方向被洗成 equal）。
    """
    from app.services.gis_harness.plan_graph import build_plan_graph

    graph = build_plan_graph(chapter, evaluate=True)
    rows_fp = rows_fingerprint(chapter)

    stored_stages: Dict[str, Dict[str, Any]] = {}
    if isinstance(stored, dict):
        for s in stored.get("stages") or []:
            if isinstance(s, dict) and s.get("capability"):
                stored_stages[str(s["capability"])] = s

    # 证据失配检测：行签名 vs 旧证据指纹。**只对已满足（satisfied）的
    # 旧阶段判 stale**——pending/ready 行的签名变化是正常推进（完成/
    # 绑定），不是证据漂移；satisfied 行被重绑（换 ref / 换算法 / 换参）
    # 才是「上游事实变了 → 下游要重算」。旧块无证据键（首代）→ 全 current。
    stale_caps: Set[str] = set()
    node_sig_by_cap: Dict[str, str] = {}
    for node in graph.nodes:
        sig = _node_row_signature(chapter, node.capability)
        node_sig_by_cap[node.capability] = sig
        old = stored_stages.get(node.capability)
        if old is None:
            continue
        if str(old.get("state") or "") != StageState.SATISFIED.value:
            continue
        old_fp = str((old.get("evidence") or {}).get("fingerprint") or "")
        if old_fp and old_fp != canonical_fingerprint(sig):
            stale_caps.add(node.capability)

    # reverse 依赖闭包：证据漂移向下游传播 STALE（不触碰无关分支）。
    stale_caps |= _downstream_closure(graph, stale_caps)

    stages: List[InstanceStage] = []
    state_by_cap: Dict[str, StageState] = {}
    for node in graph.nodes[:MAX_STAGES]:
        # PlanNodeStatus 是 (str, Enum) 混入枚举：str() 是 "Class.MEMBER"
        # 而非值 —— 必须经 .value 归一（否则所有节点都落 PENDING 缺省）。
        stage_state = _NODE_TO_STAGE.get(
            getattr(node.status, "value", node.status), StageState.PENDING)
        if node.capability in stale_caps and stage_state == StageState.SATISFIED:
            stage_state = StageState.STALE
        blocked: List[str] = []
        if stage_state == StageState.BLOCKED:
            for dep in node.blocked_by[:4]:
                blocked.append(f"{BLOCK_DEPENDENCY}:{dep[:48]}")
        stages.append(InstanceStage(
            capability=node.capability,
            kind=node.kind,
            state=stage_state.value,
            blocked_reasons=blocked,
            resolved_algorithm=node.resolved_algorithm[:64],
            bound_ref=node.bound_ref[:64],
            evidence=_stage_evidence(
                node_sig_by_cap[node.capability], rows_fp,
                stale=node.capability in stale_caps,
            ),
        ))
        state_by_cap[node.capability] = stage_state

    deps: List[InstanceDependency] = []
    for node in graph.nodes:
        for producer in node.depends_on:
            if len(deps) >= MAX_DEPENDENCIES:
                break
            deps.append(InstanceDependency(
                consumer=node.capability,
                producer=producer,
                state=_dependency_state(
                    graph.node(producer),
                    state_by_cap.get(node.capability, StageState.PENDING),
                    producer in stale_caps,
                ),
            ))

    science = _derive_science(
        science_baseline if isinstance(science_baseline, dict) else chapter,
        recomputed_contract,
    )
    dimensions = _derive_dimensions(chapter, state_by_cap, science)

    new_state = WorkflowInstanceState(
        instance_id=instance_id,
        plan_id=str(chapter.get("plan_id") or ""),
        state_revision=1,
        state_fingerprint="",
        gate_fingerprint="",
        rows_fingerprint=rows_fp[:2048],
        checked_revision=int(mapspec_revision or 0),
        render_observation_seq=int(render_seq or 0),
        stages=stages,
        dependencies=deps,
        science=science,
        dimensions=dimensions,
        transitions=[],
        last_event=str(event)[:32],
    )
    prev_revision = 0
    prev_fp = ""
    if isinstance(stored, dict):
        try:
            prev_revision = int(stored.get("state_revision") or 0)
        except (TypeError, ValueError):
            prev_revision = 0
        prev_fp = str(stored.get("state_fingerprint") or "")
    new_state.state_fingerprint = _content_fingerprint(new_state)
    if prev_fp == new_state.state_fingerprint:
        new_state.state_revision = max(prev_revision, 1)
    else:
        new_state.state_revision = prev_revision + 1
        new_state.transitions = _merge_transitions(
            stored, new_state, prev_revision + 1, event)
    new_state.gate_fingerprint = gate_fingerprint(
        rows_fp, mapspec_revision, render_seq,
        str(chapter.get("workflow_contract") or ""),
    )
    return new_state


def _node_row_signature(chapter: Dict[str, Any], capability: str) -> str:
    for row in list(chapter.get("data_requirements") or []) + list(
        chapter.get("analysis_steps") or []
    ):
        if isinstance(row, dict) and row.get("capability") == capability:
            return row_signature(row)
    return capability


def _downstream_closure(graph: Any, seeds: Set[str]) -> Set[str]:
    """reverse-dep 闭包：seeds 的全部（传递）下游 capability。"""
    if not seeds:
        return set()
    reverse: Dict[str, List[str]] = {}
    for node in graph.nodes:
        for dep in node.depends_on:
            reverse.setdefault(dep, []).append(node.capability)
    out: Set[str] = set()
    stack = list(seeds)
    while stack:
        cur = stack.pop()
        for child in reverse.get(cur, []):
            if child not in out:
                out.add(child)
                stack.append(child)
    return out


def _derive_science(
    chapter: Dict[str, Any],
    recomputed: Optional[Dict[str, Any]],
) -> ScienceRecheck:
    """科学维裁决：解除候选 vs 基准 contract（有解除候选时）。

    ``chapter`` 可以是整章 dict（读 ``workflow_contract`` 键）或直接是
    基准 contract dict（服务回写后的重派生传写前契约）。

    review Round-1 #1/#2 修正：``recomputed`` 是 ``derive_unblock_contract``
    的输出 —— **单调解除**（只动 roles + data_blockers，method/obligations
    逐字保留），因此方向裁决用**集合恒等**而非数量：

    - data_blockers 严格子集（至少解除一个）⇒ ``unblocked``（服务回写）；
    - 其余（无新证据 / 相等）⇒ ``equal``；
    - 恶化方向不存在于本函数（重算不构造新阻断）—— 上游失效走
      artifact-stale 事件 + STALE 阶段披露，不由契约重算虚构；
    - 无候选 ⇒ evaluated=False（不虚构评估）。
    """
    recheck = ScienceRecheck()
    contract = chapter.get("workflow_contract") if isinstance(chapter, dict) else None
    if contract is None and isinstance(chapter, dict) and "roles" in chapter:
        contract = chapter  # 传入的已是基准 contract 本体
    if not isinstance(contract, dict) or not isinstance(recomputed, dict):
        return recheck
    recheck.evaluated = True
    recheck.data_blockers = [str(b) for b in (recomputed.get("data_blockers") or [])][:MAX_BLOCKERS]
    recheck.method_blockers = [
        str(b) for b in (recomputed.get("method_blockers") or [])
    ][:MAX_BLOCKERS]
    recheck.recomputed_fingerprint = canonical_fingerprint(_contract_core(recomputed))
    baseline_data = {str(b) for b in (contract.get("data_blockers") or [])}
    recomputed_data = set(recheck.data_blockers)
    method_same = sorted(recheck.method_blockers) == sorted(
        str(b) for b in (contract.get("method_blockers") or []))
    if recomputed_data < baseline_data and method_same:
        recheck.direction = "unblocked"
        recheck.divergent = True
        unblocked = sorted(baseline_data - recomputed_data)
        recheck.disclosure = (
            "数据到位后科学契约解除：角色 "
            f"{','.join(unblocked[:4])} 已有绑定证据（method/obligations 未放松）"
        )
    else:
        recheck.direction = "equal"
    return recheck


def _derive_dimensions(
    chapter: Dict[str, Any],
    state_by_cap: Dict[str, StageState],
    science: ScienceRecheck,
) -> Dict[str, str]:
    """完成维的实例级有界投影（contracts.py 七维的运行态对应面，诚实保守）。"""
    dims: Dict[str, str] = {}
    req_rows = [r for r in chapter.get("data_requirements") or [] if isinstance(r, dict)]
    required_unresolved = [
        r for r in req_rows
        if not r.get("optional") and r.get("status") in ("pending", "unavailable", "failed")
    ]
    dims["data"] = (
        "blocked" if science.data_blockers
        else "pending" if required_unresolved
        else "ok" if req_rows else "unknown"
    )
    node_states = list(state_by_cap.values())
    if not node_states:
        dims["analysis"] = "unknown"
    elif any(s == StageState.BLOCKED for s in node_states):
        dims["analysis"] = "blocked"
    elif all(s in (StageState.SATISFIED, StageState.SKIPPED) for s in node_states):
        dims["analysis"] = "ok"
    elif any(s in (StageState.STALE, StageState.FAILED) for s in node_states):
        dims["analysis"] = "degraded"
    else:
        dims["analysis"] = "pending"
    if science.method_blockers:
        dims["science"] = "blocked"
    elif any(s == StageState.STALE for s in node_states):
        dims["science"] = "degraded"
    elif science.evaluated:
        dims["science"] = "ok"
    else:
        dims["science"] = "unknown"
    map_product = chapter.get("map_product")
    if isinstance(map_product, dict):
        status = str(map_product.get("status") or "")
        dims["cartography"] = (
            "ok" if status == "complete"
            else "blocked" if status == "failed"
            else "pending"
        )
        render_status = str(map_product.get("render_status") or "")
        dims["observed_map"] = (
            "ok" if render_status == "verified"
            else "degraded" if render_status in ("issues", "stale")
            else "pending" if render_status == "unknown"
            else "unknown"
        )
    else:
        dims["cartography"] = "pending"
        dims["observed_map"] = "unknown"
    # 披露维：有方法论警告（披露已落地）→ ok；未评估 → unknown。
    # 不确定性正证据在 W7 落地前保持诚实 unknown。
    dims["methodology_disclosure"] = (
        "ok" if chapter.get("methodology_warnings") or science.evaluated else "unknown"
    )
    dims["uncertainty_disclosure"] = "unknown"
    return {k: v for k, v in dims.items() if v}


def _content_fingerprint(state: WorkflowInstanceState) -> str:
    """状态内容指纹（不含 revision/transitions/last_event——同内容同指纹）。"""
    return canonical_fingerprint({
        "stages": [s.to_bounded_dict() for s in state.stages],
        "dependencies": [d.to_bounded_dict() for d in state.dependencies],
        "science": state.science.to_bounded_dict(),
        "dimensions": state.dimensions,
        "rows_fingerprint": state.rows_fingerprint[:2048],
        "checked_revision": state.checked_revision,
        "render_observation_seq": state.render_observation_seq,
    })


def _merge_transitions(
    stored: Optional[Dict[str, Any]],
    new_state: WorkflowInstanceState,
    revision: int,
    event: str,
) -> List[StateTransition]:
    """转移记录：新块 vs 旧块的阶段态差 + 科学契约差（环形 ≤MAX）。"""
    old_by_cap: Dict[str, str] = {}
    if isinstance(stored, dict):
        for s in stored.get("stages") or []:
            if isinstance(s, dict) and s.get("capability"):
                old_by_cap[str(s["capability"])] = str(s.get("state") or "")
    records: List[StateTransition] = []
    for stage in new_state.stages:
        old = old_by_cap.get(stage.capability)
        if old is not None and old != stage.state:
            records.append(StateTransition(
                revision=revision,
                event=event,
                capability=stage.capability,
                from_state=old,
                to_state=stage.state,
                reason_code=stage.blocked_reasons[0][:MAX_REASON] if stage.blocked_reasons else "",
            ))
    if new_state.science.divergent:
        records.append(StateTransition(
            revision=revision,
            event=event,
            capability="",
            from_state="science",
            to_state=new_state.science.direction,
            reason_code="WORKFLOW_CONTRACT_DIVERGENT",
        ))
    return records[:MAX_TRANSITIONS]


def gate_fingerprint(
    rows_fp: str,
    mapspec_revision: int,
    render_seq: int,
    contract: Any,
) -> str:
    """去重门键：rows(V2) + MapSpec revision + render 代次 + contract 指纹。

    contract 参与门：科学重算回写 contract 后必须再派生一次（转移记录
    记录该事件），否则重算不可观测。
    """
    contract_fp = ""
    if isinstance(contract, dict):
        contract_fp = canonical_fingerprint(_contract_core(contract))
    return canonical_fingerprint({
        "rows": str(rows_fp)[:2048],
        "revision": int(mapspec_revision or 0),
        "render_seq": int(render_seq or 0),
        "contract": contract_fp,
    })


# ── 有界投影（LLM 面）────────────────────────────────────────────────────

def format_instance_line(chapter: Optional[Dict[str, Any]]) -> str:
    """[GIS Instance] 单行投影（SessionPlan projection 的 additive 行）。"""
    if not isinstance(chapter, dict):
        return ""
    stored = chapter.get(WORKFLOW_INSTANCE_KEY)
    if not isinstance(stored, dict) or not stored.get("instance_id"):
        return ""
    lines = [f"[GIS Instance] rev={stored.get('state_revision')}"]
    dims = stored.get("dimensions") or {}
    if dims:
        lines.append(",".join(f"{k}={v}" for k, v in list(dims.items())[:5]))
    sci = stored.get("science")
    if isinstance(sci, dict):
        blockers = list(sci.get("data_blockers") or []) + list(sci.get("method_blockers") or [])
        if blockers:
            lines.append("blocked:" + ",".join(str(b) for b in blockers[:3]))
        if sci.get("divergent") and sci.get("direction") == "worsened":
            lines.append("science:awaiting_finalize")
    stages = stored.get("stages") or []
    stale = [
        s.get("capability") for s in stages
        if isinstance(s, dict) and s.get("state") == "stale"
    ]
    if stale:
        lines.append("stale:" + ",".join(str(c)[:32] for c in stale[:3]))
    ready = [
        s.get("capability") for s in stages
        if isinstance(s, dict) and s.get("state") == "ready"
    ]
    if ready:
        lines.append("ready:" + ",".join(str(c)[:32] for c in ready[:3]))
    return " ".join(lines)[:480]


# ── 服务入口（异步、锁内持久化；map_product persist 模式的轻量版）────────

async def maybe_update_workflow_instance(
    session_id: str,
    *,
    reason: str = "tool_result",
    event: str = "auto",
    force: bool = False,
) -> Optional[Dict[str, Any]]:
    """触发点入口：廉价门 + 纯派生 + 锁内持久化（幂等、有界、可关停）。

    返回新块 dict（跳过/禁用时 None）。绝不触碰行状态与 map_product 块；
    科学重算的解除方向按 merge 协议回写 ``workflow_contract``（同一评估
    器，非第二事实源）；失败只记日志，下一触发点重试。
    """
    if not session_id or not _enabled():
        return None
    from app.services.session_plan import goal_key, load_session_plan, save_session_plan
    from app.services.session_data import session_data_manager

    plan = await load_session_plan(session_id)
    if plan is None or not isinstance(plan.gis_chapter, dict):
        return None
    chapter = plan.gis_chapter
    if not chapter.get("plan_id"):
        return None
    try:
        map_state = await session_data_manager.get_map_state(session_id)
    except Exception:  # noqa: BLE001 — 门输入读失败按 0 处理
        map_state = None
    try:
        revision = int((map_state or {}).get("_cartographic_mutation_revision") or 0)
    except (TypeError, ValueError):
        revision = 0
    from app.services.gis_harness.render_observation import (
        load_render_observation,
        observation_sequence,
    )
    render_seq = observation_sequence(await load_render_observation(session_id, map_state))

    stored = chapter.get(WORKFLOW_INSTANCE_KEY)
    rows_fp = rows_fingerprint(chapter)
    gate = gate_fingerprint(rows_fp, revision, render_seq, chapter.get("workflow_contract"))
    if (
        not force
        and isinstance(stored, dict)
        and str(stored.get("gate_fingerprint") or "") == gate
        and str(stored.get("instance_id") or "")
    ):
        return None

    instance_id = f"{session_id}:{plan.envelope_id}:{chapter.get('plan_id')}"
    # review Round-1 #1：解除候选是**单调解除**（derive_unblock_contract）——
    # 只认 data_requirements 绑定证据、只动 roles/data_blockers，方法阻断
    # 与 obligations 逐字保留。无新绑定证据 ⇒ None ⇒ 零回写。
    recomputed = derive_unblock_contract(chapter)
    new_state = derive_workflow_instance(
        chapter,
        instance_id=instance_id,
        mapspec_revision=revision,
        render_seq=render_seq,
        stored=stored if isinstance(stored, dict) else None,
        event=event,
        recomputed_contract=recomputed,
    )
    # 解除方向才回写 contract（derive_unblock_contract 的输出自带 provenance
    # ——服务兜底补齐写事件键；重算函数保持纯）。
    pending_contract: Optional[Dict[str, Any]] = None
    if new_state.science.direction == "unblocked" and isinstance(recomputed, dict):
        pending_contract = dict(recomputed)
        pending_contract.setdefault(
            "recheck",
            {"source": "data_arrival", "unblocked_roles": [], "fingerprint": ""},
        )
        event = "science_recheck"

    validated_goal = goal_key(chapter, plan.user_goal)
    validated_rows = rows_fp
    # review Round-1 #10：锁内对比用的写前契约指纹（契约缺席 = 空串 = 不守卫）。
    baseline_contract = chapter.get("workflow_contract")
    validated_contract_fp = (
        canonical_fingerprint(baseline_contract)
        if isinstance(baseline_contract, dict) else ""
    )
    try:
        from app.services.distributed_lock import session_lock_registry
        async with session_lock_registry.lock(session_id, fail_on_degraded=True) as lock:
            fresh = await load_session_plan(session_id)
            if fresh is None or not isinstance(fresh.gis_chapter, dict) or lock.lost:
                return None
            if goal_key(fresh.gis_chapter, fresh.user_goal) != validated_goal:
                return None
            if rows_fingerprint(fresh.gis_chapter)[:2048] != validated_rows[:2048]:
                return None
            # review R3 minor：实例块自身漂移守卫 —— 等锁窗口内另一触发点
            # 已写过实例块 ⇒ 本次派生基于旧 stored，写回会覆盖其转移记录/
            # revision（有界观测日志的 lost update）。放弃，让下一触发点
            # 基于新块重新派生（有新事件时门键必然已变）。
            fresh_stored = fresh.gis_chapter.get(WORKFLOW_INSTANCE_KEY)
            if (
                isinstance(stored, dict)
                and isinstance(fresh_stored, dict)
                and str(fresh_stored.get("state_fingerprint") or "")
                != str(stored.get("state_fingerprint") or "")
            ):
                return None
            # review Round-1 #10：锁内契约漂移守卫 —— 等锁窗口内契约被
            # 并发写手（finalize）改过 ⇒ 本次的解除候选基于旧契约，放弃
            # （下一触发点基于新契约重新裁决）。
            if validated_contract_fp and pending_contract is not None:
                fresh_fp = canonical_fingerprint(
                    fresh.gis_chapter.get("workflow_contract")
                ) if isinstance(
                    fresh.gis_chapter.get("workflow_contract"), dict
                ) else ""
                if fresh_fp != validated_contract_fp:
                    return None
            if pending_contract is not None:
                pre_write_contract = fresh.gis_chapter.get("workflow_contract")
                fresh.gis_chapter["workflow_contract"] = pending_contract
                # contract 变了 → 门键随之前进（重派生一次，O(nodes)）；
                # 科学裁决仍对写前契约（否则方向被洗成 equal）。
                new_state = derive_workflow_instance(
                    fresh.gis_chapter,
                    instance_id=instance_id,
                    mapspec_revision=revision,
                    render_seq=render_seq,
                    stored=stored if isinstance(stored, dict) else None,
                    event=event,
                    recomputed_contract=recomputed,
                    science_baseline=(
                        pre_write_contract if isinstance(pre_write_contract, dict) else None
                    ),
                )
            block = new_state.to_bounded_dict()
            fresh.gis_chapter[WORKFLOW_INSTANCE_KEY] = block
            await save_session_plan(fresh)
            return block
    except Exception:  # noqa: BLE001 — 披露失败不阻断 turn
        # review R3 minor：与 docstring 一致，失败必须留痕（观测面静默
        # 失败 = 不可诊断）。
        logger.warning(
            "[WorkflowInstance] persist failed session=%s (retry on next trigger)",
            session_id, exc_info=True,
        )
        return None


__all__ = [
    "WORKFLOW_INSTANCE_KEY",
    "WorkflowEventKind",
    "StageState",
    "InstanceStage",
    "InstanceDependency",
    "StateTransition",
    "ScienceRecheck",
    "WorkflowInstanceState",
    "row_signature",
    "rows_fingerprint",
    "canonical_fingerprint",
    "derive_unblock_contract",
    "derive_workflow_instance",
    "gate_fingerprint",
    "format_instance_line",
    "maybe_update_workflow_instance",
    "BLOCK_DATA",
    "BLOCK_METHOD",
    "BLOCK_DEPENDENCY",
    "BLOCK_EXECUTION",
    "BLOCK_RENDER",
]
