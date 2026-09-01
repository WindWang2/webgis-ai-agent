"""Deterministic Bounded Runtime Repair Engine（ADR-0088 P2/P5）。

回答的问题：**desired state 正确而 runtime 偏离** 时，如何在不重跑任何
GIS 分析的前提下，用既有突变通道把 runtime 拉回 desired state —— 并且
绝不无限循环、绝不对抗用户决策。

与既有两个修复面的关系（不建第三通道）：

- Map Product Finalizer 的 desired-state 修复（``map_completion``）：
  修「MapSpec 本身缺东西」（组件缺失/禁用、层不可见）—— 本模块只在
  desired state 已正确时介入；
- Cartographic AUTO_SAFE 修复（``cartography/runtime_repair.py``）：
  修 style 级质量规则（opacity/legend/style 收敛）—— 走 map-action
  通道带 ACK 闭环。本模块的 reassert 不动 style，只重新提交 spec 内容
  （revision 前进 → 前端 reconcile 重跑 → 缺失层/源重新挂载）。

修复分类（纯确定性；同输入必同输出）：

    render_layer_missing + spec 层在场 + source artifact 存活
        → reassert_spec_layer（UpsertLayerIntent 重提交，内容保持）
    render_layer_missing + source artifact 确认过期
        → 执行债（绝不 remount 死 artifact —— 重跑上游 capability）
    mounted 但 observed 不可见 + spec 期望可见 + 非 user-owned
        → restore_expected_visibility（PatchLayerPresentationIntent；
          user-wins 守卫在突变层再校验一次）
    required 组件 spec 在场且启用 + 观察未挂载
        → reassert_component（patch_component 重提交）
    user-owned 隐藏 / source 收敛中（transient）/ 期望态本身缺失
        → no-op / 交还 finalizer（本模块不越界）

有界闭环（P5）：

    observe → validate → repair → observe → validate …
    MAX_RUNTIME_REPAIR_PASSES（≤2）轮不收敛 → exhausted，交回 Pi
    （needs_repair 披露）；ledger 按 spec fingerprint 分代 —— spec 内容
    变化（用户/agent 编辑）即重置预算，同一发散不重复对抗。
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.services.gis_harness.map_completion import _COMPONENT_DEFAULT_IDS
from app.services.gis_harness.render_observation import (
    _layer_declared_visible,
    _observed_layers_by_id,
    _planned_result_layer_ids,
    observation_revision,
)

logger = logging.getLogger(__name__)

# 有界修复轮数（与 MAX_FINALIZATION_PASSES 同量级；绝不自循环）。
MAX_RUNTIME_REPAIR_PASSES = 2
# map_state 里修复 ledger 的键（session 级 ephemeral，与 observation 同生命周期）。
REPAIR_STATE_KEY = "_runtime_repair_state"
_MAX_LEDGER_PASSES = 4  # 记忆上界（> passes 上限，供诊断披露；截断防膨胀）

# 修复动作词表（有限集合）
REPAIR_REASSERT_LAYER = "reassert_spec_layer"
REPAIR_RESTORE_VISIBILITY = "restore_expected_visibility"
REPAIR_REASSERT_COMPONENT = "reassert_component"

# 组件 reassert 的默认 id 表（复用 map_completion 的单一来源表，不抄第二份）。


@dataclass
class RuntimeRepairPlan:
    """一次分类的修复计划（纯数据，bounded）。"""

    reassert_layers: List[str] = field(default_factory=list)
    visibility_restores: List[str] = field(default_factory=list)
    reassert_components: List[str] = field(default_factory=list)
    # 执行债证据（不能修复、只能重跑）：{"capability", "ref", "reason"}
    execution_debts: List[Dict[str, str]] = field(default_factory=list)
    # user-wins no-op 披露（不动作）
    user_owned: List[str] = field(default_factory=list)

    @property
    def has_actions(self) -> bool:
        return bool(
            self.reassert_layers
            or self.visibility_restores
            or self.reassert_components
        )

    def action_fingerprint(self) -> str:
        """动作集指纹（ledger 去重：同一发散的重复计划识别为同一次尝试）。"""
        payload = {
            "layers": sorted(self.reassert_layers),
            "visibility": sorted(self.visibility_restores),
            "components": sorted(self.reassert_components),
        }
        return "rr-sha256:" + hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


@dataclass
class RuntimeRepairOutcome:
    """一次有界修复执行的产出（bounded / serializable）。"""

    applied: List[str] = field(default_factory=list)
    exhausted: bool = False
    passes_used: int = 0
    execution_debts: List[Dict[str, str]] = field(default_factory=list)
    user_owned: List[str] = field(default_factory=list)
    # applied 非空时携带修复后的 spec 快照与 revision（响应侧带前端提交）。
    mapspec: Optional[Dict[str, Any]] = None
    mutation_revision: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "applied": [a[:64] for a in self.applied[:8]],
            "exhausted": self.exhausted,
            "passes": self.passes_used,
        }
        if self.execution_debts:
            out["execution_debts"] = self.execution_debts[:4]
        if self.user_owned:
            out["user_owned"] = [u[:64] for u in self.user_owned[:4]]
        if self.mapspec is not None:
            out["mapspec"] = self.mapspec
            out["mutation_revision"] = self.mutation_revision
        return out


def classify_runtime_repairs(
    chapter: Dict[str, Any],
    mapspec: Dict[str, Any],
    *,
    descriptors: Optional[Dict[str, Any]] = None,
    observation: Optional[Dict[str, Any]] = None,
    current_revision: int = 0,
    required_slots: Optional[List[List[str]]] = None,
) -> RuntimeRepairPlan:
    """渲染证据 → 确定性修复计划（纯函数；零 IO；stale 观察 → 空计划）。

    ``descriptors``：ref → descriptor | None（None 值 = 确认驱逐；不在表
    中 = 未知 —— 未知不动作，不把存储抖动当成执行债）。
    """
    plan = RuntimeRepairPlan()
    if not isinstance(observation, dict):
        return plan
    # revision 防护（P5）：只对描述**当前代次**的观察修复 —— stale 观察
    # 修复的是旧发散，必然对抗刚刚发生的突变。
    if observation_revision(observation) != int(current_revision or 0):
        return plan

    spec_layers = {
        str(ly.get("id") or ""): ly
        for ly in (mapspec.get("layers") or [])
        if isinstance(ly, dict)
    }
    raw_sources = mapspec.get("sources")
    if isinstance(raw_sources, dict):
        source_by_id = {str(k): v for k, v in raw_sources.items() if isinstance(v, dict)}
    else:
        source_by_id = {
            str(s.get("id") or ""): s for s in (raw_sources or []) if isinstance(s, dict)
        }
    obs_by_id = _observed_layers_by_id(observation)

    def _source_ref(layer: Dict[str, Any]) -> str:
        src = source_by_id.get(str(layer.get("source") or "")) or {}
        for key in ("ref", "ref_id", "image_ref", "imageRef", "result_ref"):
            val = src.get(key)
            # V4：磁盘栅格（ref:raster/*）同样进入 descriptor 面（inputs 经
            # probe_ref stat 探测）—— 过期判定对两类存储一致。
            if isinstance(val, str) and val.startswith("ref:"):
                return val
        return ""

    def _layer_capability(lid: str) -> str:
        for row in chapter.get("map_layers") or []:
            if (
                isinstance(row, dict)
                and str(row.get("layer_id") or "") == lid
            ):
                return str(row.get("source_capability") or "")
        return ""

    for lid in _planned_result_layer_ids(chapter):
        layer = spec_layers.get(lid)
        if layer is None:
            continue  # 期望态缺失 → finalizer 的 F_LAYER_MISSING 通道，不越界
        entry = obs_by_id.get(lid)
        try:
            runtime_count = int(entry.get("runtime_layer_count") or 0) if entry else 0
        except (TypeError, ValueError):
            runtime_count = 0
        mounted_visible = entry is not None and runtime_count > 0 and bool(entry.get("visible"))
        if mounted_visible:
            continue  # 渲染正常 —— 无债
        if entry is not None and runtime_count > 0 and not entry.get("visible"):
            # 挂载而不可见：先问 desired state
            if not _layer_declared_visible(layer):
                intent = layer.get("cartographic_intent")
                if isinstance(intent, dict) and intent.get("presentation_owner") == "user":
                    plan.user_owned.append(lid)  # user-wins：披露不修复
                continue  # spec 本就隐藏 → 观察如实，无发散
            plan.visibility_restores.append(lid)
            continue
        # 层缺席（未挂载/无 live 层）：source artifact 决定修复还是执行债
        src_ref = _source_ref(layer)
        if not src_ref:
            # 无 ref 的源（basemap/tiles/inline）—— 重提交 spec 层重挂载
            plan.reassert_layers.append(lid)
            continue
        if descriptors is not None and src_ref in descriptors and descriptors[src_ref] is None:
            plan.execution_debts.append({
                "capability": _layer_capability(lid),
                "ref": src_ref,
                "reason": "source artifact expired — rerun upstream capability (no remount of dead ref)",
            })
            continue  # 绝不 remount 死 artifact（Scenario B）
        # ref 存活或未知 → reassert（reconcile 重挂载；未知由 re-observation 收敛）
        plan.reassert_layers.append(lid)

    # required 组件槽：spec 在场且启用而运行时未挂载 → reassert（重渲染 chrome）
    if required_slots:
        components = [
            c for c in ((mapspec.get("layout") or {}).get("components") or [])
            if isinstance(c, dict)
        ]
        enabled_types = {
            str(c.get("type") or "") for c in components if c.get("enabled") is not False
        }
        present_types = {str(c.get("type") or "") for c in components}
        observed_types: Dict[str, bool] = {}
        for comp in observation.get("components") or []:
            if not isinstance(comp, dict):
                continue
            ctype = str(comp.get("type") or "")
            if ctype:
                observed_types[ctype] = bool(observed_types.get(ctype)) or bool(comp.get("mounted"))
        for family in required_slots:
            family = [t for t in family if t] or ["title"]
            if any(observed_types.get(t) for t in family):
                continue  # 观察到挂载 —— 无债
            if any(t in enabled_types for t in family):
                # spec 启用而 chrome 未挂载 → reassert 修复（族首类型）
                repair_type = next((t for t in family if t in enabled_types), family[0])
                if repair_type not in plan.reassert_components:
                    plan.reassert_components.append(repair_type)
            elif not any(t in present_types for t in family):
                continue  # 期望态缺失 → finalizer add_component 通道，不越界
            else:
                continue  # 全族禁用 → 用户/one-shot 语义归 finalizer，不对抗
    return plan


def _trace_repair(session_id: str, outcome: RuntimeRepairOutcome) -> None:
    """best-effort trace（P7）：修复动作 / 耗尽 / 执行债计数。"""
    try:
        from app.services.gis_harness.trace import (
            COUNTER_RUNTIME_REPAIRS,
            COUNTER_RUNTIME_REPAIR_EXHAUSTED,
            COUNTER_RUNTIME_REPAIR_EXECUTION_DEBTS,
            STAGE_RUNTIME_REPAIR,
            get_runtime_trace,
        )
        trace = get_runtime_trace()
        trace.record(
            session_id, STAGE_RUNTIME_REPAIR,
            applied=len(outcome.applied),
            exhausted=outcome.exhausted,
            passes=outcome.passes_used,
            debts=len(outcome.execution_debts),
        )
        if outcome.applied:
            trace.bump(COUNTER_RUNTIME_REPAIRS, len(outcome.applied))
        if outcome.exhausted:
            trace.bump(COUNTER_RUNTIME_REPAIR_EXHAUSTED)
        if outcome.execution_debts:
            trace.bump(COUNTER_RUNTIME_REPAIR_EXECUTION_DEBTS, len(outcome.execution_debts))
    except Exception:  # noqa: BLE001 — trace 故障不影响业务
        pass


async def run_runtime_repair(
    session_id: str,
    *,
    chapter: Dict[str, Any],
    mapspec: Dict[str, Any],
    descriptors: Optional[Dict[str, Any]] = None,
    observation: Optional[Dict[str, Any]] = None,
    current_revision: int = 0,
    required_slots: Optional[List[List[str]]] = None,
    map_state: Optional[Dict[str, Any]] = None,
) -> RuntimeRepairOutcome:
    """有界 runtime 修复执行（调用方持 session lock；一次至多一轮修复）。

    轮数 ledger（``REPAIR_STATE_KEY``）：按 **observation fingerprint** 分代
    —— spec 内容不变而同一发散反复出现时消耗预算（≤ MAX），spec 内容变化
    即重置（新代次的新发散有新预算）。收敛（计划空）时清账。

    返回 outcome；``applied`` 非空时附带修复后 spec + revision（前端提交后
    reconcile 重跑 → 新观察 → 回路闭合）。
    """
    from app.services.session_data import session_data_manager
    from app.services.gis_harness.render_observation import observation_revision

    outcome = RuntimeRepairOutcome()
    if map_state is None:
        # ledger 输入缺省时补一次读（轮数记忆跨观察存活的前提）。
        try:
            map_state = await session_data_manager.get_map_state(session_id)
        except Exception:  # noqa: BLE001 — 读失败按无 ledger 处理
            map_state = None
    # stale 观察提前退出：空计划 ≠ 收敛 —— stale（revision 落后）观察的
    # 空计划若走下方清账分支，会把进行中发散的轮数记忆洗掉（修复推进
    # revision 后，在途旧观察恰好 stale 到达 → 预算重置 → 无界对抗的种子）。
    if observation is not None and observation_revision(observation) != int(
        current_revision or 0
    ):
        return outcome
    plan = classify_runtime_repairs(
        chapter, mapspec,
        descriptors=descriptors,
        observation=observation,
        current_revision=current_revision,
        required_slots=required_slots,
    )
    outcome.execution_debts = plan.execution_debts[:4]
    outcome.user_owned = list(plan.user_owned[:4])
    if not plan.has_actions:
        # 收敛（新鲜观察 + 无可修复发散）：清 ledger（下次新发散有满预算）
        if isinstance(map_state, dict) and isinstance(
            map_state.get(REPAIR_STATE_KEY), dict
        ):
            try:
                await session_data_manager.set_map_state(
                    session_id, REPAIR_STATE_KEY, {}
                )
            except Exception:  # noqa: BLE001 — 清账失败不影响收敛披露
                pass
        return outcome

    obs_fingerprint = str(observation.get("mapspec_fingerprint") or "") if observation else ""
    ledger = (
        map_state.get(REPAIR_STATE_KEY)
        if isinstance(map_state, dict) else None
    )
    if not isinstance(ledger, dict) or ledger.get("fingerprint") != obs_fingerprint:
        ledger = {"fingerprint": obs_fingerprint, "passes": []}
    passes = [
        p for p in (ledger.get("passes") or [])[:_MAX_LEDGER_PASSES] if isinstance(p, dict)
    ]
    fingerprint = plan.action_fingerprint()
    prior_attempt = next(
        (p for p in passes if p.get("fingerprint") == fingerprint), None
    )
    if len(passes) >= MAX_RUNTIME_REPAIR_PASSES or (
        prior_attempt is not None
        and int(prior_attempt.get("seen") or 1) >= MAX_RUNTIME_REPAIR_PASSES
    ):
        # 预算耗尽：总执行轮数达上限，或同一发散计划已执行过上限轮次
        # 而观察仍未收敛 —— 交回 Pi（needs_repair 披露），绝不无限对抗。
        outcome.passes_used = len(passes)
        outcome.exhausted = True
        _trace_repair(session_id, outcome)
        return outcome

    applied: List[str] = []
    try:
        from app.services.gis_world_state.mutation import (
            apply_gis_mutation,
            apply_gis_mutation_batch,
        )
        from app.services.mapspec.lifecycle_engine import (
            PatchLayerPresentationIntent,
            UpsertLayerIntent,
        )

        spec_layers = {
            str(ly.get("id") or ""): ly
            for ly in (mapspec.get("layers") or [])
            if isinstance(ly, dict)
        }
        if plan.reassert_layers:
            # reassert = 内容保持的整层重提交（UpsertLayerIntent 透传既有
            # user-wins 守卫：durable presentation 继承 + owner 印记）。
            # CAS（expected_revision = 观察盖章的 revision）：快照与提交之间
            # spec 被并发突变推进时，本次 reassert 被 superseded —— 旧内容的
            # 重提交绝不覆盖新编辑（review C-1）。逐层单事务与
            # webgis_layer_upsert 工具路径同款；轮内 ≤8 且仅在渲染发散时
            # 触发，非热路径。
            for lid in plan.reassert_layers[:8]:
                layer = spec_layers.get(lid)
                if layer is None:
                    continue
                try:
                    res = await apply_gis_mutation(
                        session_id,
                        UpsertLayerIntent(layer=dict(layer)),
                        origin="agent",
                        actor="runtime_repair",
                        expected_revision=current_revision,
                    )
                    if not getattr(res, "is_error", False) and not getattr(
                        res, "superseded", False
                    ):
                        applied.append(f"{REPAIR_REASSERT_LAYER}:{lid}")
                except Exception:  # noqa: BLE001 — 单项失败留给下一轮披露
                    logger.warning(
                        "[RuntimeRepair] reassert layer failed id=%s", lid
                    )
        if plan.visibility_restores:
            # presentation patch 走 batch 通道（一次事务 + 单条 provenance；
            # user-wins 守卫在锁内逐 intent 复检，被拒即如实保留）。
            # CAS 同上 —— 并发突变后整批不提交（committed=False）。
            intents = [
                PatchLayerPresentationIntent(layer_id=lid, visible=True)
                for lid in plan.visibility_restores[:8]
            ]
            batch = await apply_gis_mutation_batch(
                session_id, intents, origin="agent", actor="runtime_repair",
                expected_revision=current_revision,
            )
            if batch.committed and any(o.status == "applied" for o in batch.outcomes):
                applied.extend(
                    f"{REPAIR_RESTORE_VISIBILITY}:{lid}"
                    for lid in plan.visibility_restores[:8]
                )
        for repair_type in plan.reassert_components[:4]:
            from app.services.mapspec_store import mapspec_store

            default_id = _COMPONENT_DEFAULT_IDS.get(repair_type, f"{repair_type}-main")
            try:
                res = await mapspec_store.patch_component(
                    session_id,
                    expected_revision=current_revision,
                    component_id=default_id,
                    component_type=repair_type,
                    enabled=True,
                    upsert=True,
                )
                if res.get("success"):
                    applied.append(f"{REPAIR_REASSERT_COMPONENT}:{repair_type}")
            except Exception:  # noqa: BLE001 — 单项失败留给下一轮披露
                logger.warning(
                    "[RuntimeRepair] reassert_component failed type=%s", repair_type
                )
    except Exception:  # noqa: BLE001 — 修复失败不阻断观察响应（下一轮再试/披露）
        logger.warning(
            "[RuntimeRepair] repair batch failed session=%s", session_id, exc_info=True
        )

    outcome.applied = applied
    outcome.passes_used = len(passes) + 1
    # 失败的尝试同样入账（无重试上限的失败重放是无限循环的种子）：
    # 同一发散计划的 seen 计数随观察推进，达 MAX 即 exhausted。
    if prior_attempt is not None:
        prior_attempt["seen"] = int(prior_attempt.get("seen") or 1) + 1
        if applied:
            prior_attempt["applied"] = applied[:8]
    else:
        passes.append({
            "fingerprint": fingerprint,
            "seen": 1,
            "applied": applied[:8],
        })
    ledger["passes"] = passes[:_MAX_LEDGER_PASSES]
    try:
        await session_data_manager.set_map_state(
            session_id, REPAIR_STATE_KEY, ledger
        )
    except Exception:  # noqa: BLE001 — ledger 落账失败只影响轮数记忆
        logger.warning("[RuntimeRepair] ledger persist failed session=%s", session_id)
    if applied:
        # 修复推进了 revision —— 带上修复后 spec 快照供响应侧提交
        try:
            from app.services.mapspec_store import mapspec_store

            fresh = await mapspec_store.get_mapspec(session_id)
            if isinstance(fresh, dict):
                outcome.mapspec = fresh
                state = map_state if isinstance(map_state, dict) else {}
                try:
                    fresh_state = await session_data_manager.get_map_state(session_id)
                    if isinstance(fresh_state, dict):
                        state = fresh_state
                except Exception:  # noqa: BLE001
                    pass
                try:
                    outcome.mutation_revision = int(
                        state.get("_cartographic_mutation_revision") or 0
                    )
                except (TypeError, ValueError):
                    outcome.mutation_revision = None
        except Exception:  # noqa: BLE001 — 快照失败只影响响应附带 spec
            pass
    elif len(passes) >= MAX_RUNTIME_REPAIR_PASSES:
        outcome.exhausted = True
    _trace_repair(session_id, outcome)
    return outcome


__all__ = [
    "MAX_RUNTIME_REPAIR_PASSES",
    "REPAIR_STATE_KEY",
    "REPAIR_REASSERT_LAYER",
    "REPAIR_RESTORE_VISIBILITY",
    "REPAIR_REASSERT_COMPONENT",
    "RuntimeRepairPlan",
    "RuntimeRepairOutcome",
    "classify_runtime_repairs",
    "run_runtime_repair",
]
