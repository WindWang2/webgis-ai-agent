"""情境化多方案候选工具面（G05，薄包装层）。

真正的事务/隔离/裁决全部在 ``scenario_harness``（baseline/candidates/
staleness/adopt）与既有 ADR-0193 分支机器内完成 —— 工具调用方只能**指名**
candidate id / 组 / seq / 锁字段，任何叙事性描述不参与判定。采纳语义：

- ``webgis_scenario_inspect``：只读（候选/基准/锁定/指代解析）；
- ``webgis_scenario_propose``：在隔离分支上开候选（零父会话突变）；
- ``webgis_scenario_execute``：对候选分支施加干预（仍零父会话突变）；
- ``webgis_scenario_compare``：结构化对比 + 基准漂移影响分级（只读）;
- ``webgis_scenario_adopt``：选择性采纳 → 父会话单事务（CAS + user-wins +
  锁检查 + 幂等回执）；origin="agent" 时与用户路径同链，user-wins 守卫
  仍然生效，不因 Agent 提议绕过用户权限；
- ``webgis_scenario_abandon``：删除/归档候选（分支级回收）；
- ``webgis_scenario_lock`` / ``webgis_scenario_unlock``：用户显式锁定面
  （unlock 仅 user origin —— Agent 无权解用户锁）。
"""

from __future__ import annotations

import logging
from typing import List, Optional

from pydantic import BaseModel, Field

from app.tools.registry import ToolExecutionPolicy, ToolRegistry

logger = logging.getLogger(__name__)


def _err(code: str, message: str) -> dict:
    return {"ok": False, "error": code, "message": message[:200]}


class ScenarioInspectArgs(BaseModel):
    reference: str = Field(
        default="",
        max_length=80,
        description=(
            "候选指代：candidate_id / 标题全等 / current / previous / 已采纳。"
            "空 = 列出全部候选 + 共享基准 + 活跃锁定。"
        ),
    )


class ScenarioProposeArgs(BaseModel):
    title: str = Field(
        default="", max_length=80, description="候选标题（如「B 方案：分级色带」）。"
    )
    hypothesis: str = Field(
        default="", max_length=200, description="该候选要验证的假设（可空）。"
    )
    intents: List[dict] = Field(
        default_factory=list,
        max_length=24,
        description=(
            "干预序列（MapSpec mutation intent 字段展开，kind 必须在白名单内："
            "PatchLayerStyleIntent/UpsertLayerIntent/PatchComponentIntent/…）。"
            "可空 —— 先开候选后用 webgis_scenario_execute 补干预。"
        ),
    )


class ScenarioExecuteArgs(BaseModel):
    candidate_id: str = Field(min_length=1, max_length=64, description="候选 id。")
    intents: List[dict] = Field(
        default_factory=list,
        max_length=24,
        description="追加干预（空 = 重放候选已声明的全部未应用 delta）。",
    )


class ScenarioCompareArgs(BaseModel):
    candidate_ids: List[str] = Field(
        default_factory=list,
        max_length=5,
        description="待比较候选 id（空 = 全部活跃候选）。",
    )


class ScenarioAdoptArgs(BaseModel):
    candidate_id: str = Field(min_length=1, max_length=64, description="候选 id。")
    mode: str = Field(default="all", description="all | groups | seqs。")
    groups: List[str] = Field(
        default_factory=list,
        max_length=5,
        description="mode=groups 时的语义分组（analysis/style/layout/component/data）。",
    )
    seqs: List[int] = Field(
        default_factory=list, max_length=24, description="mode=seqs 时的干预序号。"
    )
    expected_revision: Optional[int] = Field(
        default=None,
        description="乐观并发令牌（缺省 = 服务端取当前权威 revision）。",
    )


class ScenarioAbandonArgs(BaseModel):
    candidate_id: str = Field(min_length=1, max_length=64, description="候选 id。")
    hard_delete: bool = Field(
        default=True, description="True=连分支一起回收；False=仅归档。"
    )
    reason: str = Field(default="", max_length=160, description="放弃原因（审计）。")


class ScenarioLockArgs(BaseModel):
    kind: str = Field(description="layer_field | component_field | analysis_param。")
    target_id: str = Field(
        default="",
        max_length=120,
        description="目标 layer/component id（空 = 任务级全局）。",
    )
    field_paths: List[str] = Field(
        default_factory=list,
        max_length=16,
        description="锁定字段路径（如 paint.opacity / measure_unit）。",
    )
    observed_value: str = Field(
        default="", max_length=200, description="锁定时观察值摘要（漂移可见性）。"
    )
    reason: str = Field(default="", max_length=160, description="用户原因。")


class ScenarioUnlockArgs(BaseModel):
    lock_id: str = Field(min_length=1, max_length=80, description="要释放的锁 id。")
    reason: str = Field(default="", max_length=160, description="释放原因。")


def _intents_from_dicts(raw: List[dict]) -> list:
    """dict 描述 → dataclass 意图（白名单内；错误抛 ValueError 由工具转结构化）。"""
    from dataclasses import fields as dc_fields, is_dataclass

    from app.services.scenario_harness.intents_codec import SUPPORTED_KINDS
    from app.services.mapspec import intents as _intents

    out = []
    for item in raw or []:
        if not isinstance(item, dict) or not item.get("kind"):
            raise ValueError("intent dict 需要 kind 字段")
        kind = str(item["kind"])
        if kind not in SUPPORTED_KINDS:
            raise ValueError(f"kind {kind} 不在候选干预白名单内")
        cls = getattr(_intents, kind)
        if not is_dataclass(cls):
            raise ValueError(f"kind {kind} 非法")
        valid = {f.name for f in dc_fields(cls)}
        payload = {k: v for k, v in item.items() if k != "kind"}
        unknown = set(payload) - valid
        if unknown:
            raise ValueError(f"{kind} 未知字段: {sorted(unknown)}")
        out.append(cls(**payload))
    return out


def register_scenario_harness_tools(registry: ToolRegistry):
    """注册 G05 情境化多方案候选工具面。"""

    @registry.tool(
        name="webgis_scenario_inspect",
        capabilities=["gis_scenario_harness"],
        tier=2,
        domains=["meta"],
        description=(
            "只读检视当前任务的候选方案面：候选清单（状态/分组/失效原因）、共享基准"
            "（MapSpec revision/指纹/数据版本锚）、活跃用户锁定；支持语义指代"
            "（current / previous / 已采纳 / 精确 id / 标题全等）。\n"
            "**纪律**：解析「上一个方案」「之前那个候选」必须先经本工具拿到稳定"
            "candidate_id，不得凭对话记忆猜测 id。"
        ),
        args_model=ScenarioInspectArgs,
        execution_policy=ToolExecutionPolicy.INLINE,
        side_effect="read_only",
        network=False,
        deterministic=True,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=["情境", "候选", "方案", "scenario", "推演"],
        output_semantic_type="json",
        result_size_policy="inline_small",
        failure_modes=["missing_data"],
    )
    async def webgis_scenario_inspect(
        reference: str = "",
        session_id: Optional[str] = None,
    ) -> dict:
        if not session_id:
            return {"error": "Missing session_id context"}
        try:
            from app.services.scenario_harness.candidates import list_candidates
            from app.services.scenario_harness.locks import list_active_locks
            from app.services.scenario_harness.projection import (
                resolve_scenario_reference,
                scenario_situation_value,
            )

            value, corrupt = await scenario_situation_value(session_id)
            resolved = None
            if reference:
                resolved = await resolve_scenario_reference(session_id, reference)
                if resolved is None:
                    return _err(
                        "reference_unresolvable",
                        f"指代 {reference!r} 解析不了（用 inspect 列出候选后指名 id）",
                    )
            candidates = await list_candidates(session_id)
            locks = await list_active_locks(session_id)
            out: dict = {
                "ok": True,
                "overview": value,
                "resolved_candidate_id": resolved,
                "candidates": [c.model_dump(mode="json") for c in candidates],
                "active_locks": [lk.model_dump(mode="json") for lk in locks],
            }
            if corrupt:
                out["corrupt_entries"] = corrupt[:8]
            return out
        except Exception as exc:  # noqa: BLE001 — 工具面失败不外溢堆栈
            logger.warning("[scenario] inspect failed: %s", type(exc).__name__)
            return _err("tool_error", f"{type(exc).__name__}")

    @registry.tool(
        name="webgis_scenario_propose",
        capabilities=["gis_scenario_harness"],
        tier=2,
        domains=["meta"],
        description=(
            "在**隔离分支**上开一个候选方案（零父会话/权威地图突变）：捕获共享基准"
            "（MapSpec revision/指纹/数据版本）→ fork 分支 → 记录可重放干预描述。\n"
            "适用：用户想「试试另一种分级/配色/阈值」而不要动当前地图。\n"
            "**边界**：候选不写主地图、不发布、不导出；大 payload 拒绝（大数据留在会话 refs）。"
            "活跃候选上限 5；干预必须用白名单 intent kind。"
        ),
        args_model=ScenarioProposeArgs,
        execution_policy=ToolExecutionPolicy.INLINE,
        side_effect="state_mutation",
        data_mutations=["session_state"],
        network=False,
        deterministic=True,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=["情境", "候选", "提案", "scenario", "推演"],
        output_semantic_type="json",
        result_size_policy="inline_small",
        failure_modes=["invalid_args", "missing_data", "capacity"],
    )
    async def webgis_scenario_propose(
        title: str = "",
        hypothesis: str = "",
        intents: Optional[List[dict]] = None,
        session_id: Optional[str] = None,
    ) -> dict:
        if not session_id:
            return {"error": "Missing session_id context"}
        try:
            from app.services.scenario_harness.candidates import propose_candidate
            from app.services.scenario_harness.intents_codec import DeltaCodecError
            from app.services.scenario_harness.store import RegistryError

            typed = _intents_from_dicts(intents or [])
            candidate = await propose_candidate(
                session_id,
                title=title,
                hypothesis=hypothesis,
                intents=typed,
                created_by="agent",
                store=None,
            )
            return {"ok": True, "candidate": candidate.model_dump(mode="json")}
        except (RegistryError, DeltaCodecError) as exc:
            return _err(getattr(exc, "code", type(exc).__name__), str(exc))
        except ValueError as exc:
            return _err("invalid_intents", str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[scenario] propose failed: %s", type(exc).__name__)
            return _err("tool_error", f"{type(exc).__name__}")

    @registry.tool(
        name="webgis_scenario_execute",
        capabilities=["gis_scenario_harness"],
        tier=2,
        domains=["meta"],
        description=(
            "对候选**分支**施加干预（仍然零父会话突变）：样式/组件/图层 upsert/时态等"
            "白名单意图，逐条全事务应用，命中用户锁定的干预被拒并披露。\n"
            "适用：候选开出后补试验参数、或在基准漂移后局部重算候选。"
        ),
        args_model=ScenarioExecuteArgs,
        execution_policy=ToolExecutionPolicy.INLINE,
        side_effect="state_mutation",
        data_mutations=["session_state"],
        network=False,
        deterministic=True,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=["情境", "候选", "执行", "scenario", "试算"],
        output_semantic_type="json",
        result_size_policy="inline_small",
        failure_modes=["invalid_args", "missing_data", "lock_blocked"],
    )
    async def webgis_scenario_execute(
        candidate_id: str = "",
        intents: Optional[List[dict]] = None,
        session_id: Optional[str] = None,
    ) -> dict:
        if not session_id:
            return {"error": "Missing session_id context"}
        if not candidate_id:
            return _err("invalid_args", "candidate_id 必填")
        try:
            from app.services.scenario_harness.candidates import (
                apply_candidate_interventions,
            )
            from app.services.scenario_harness.intents_codec import serialize_intents
            from app.services.scenario_harness.store import RegistryError

            typed = _intents_from_dicts(intents or [])
            deltas = serialize_intents(typed) if typed else None
            results = await apply_candidate_interventions(
                session_id, candidate_id, deltas=deltas, store=None
            )
            return {"ok": True, "results": results}
        except RegistryError as exc:
            return _err(exc.code, str(exc))
        except ValueError as exc:
            return _err("invalid_intents", str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[scenario] execute failed: %s", type(exc).__name__)
            return _err("tool_error", f"{type(exc).__name__}")

    @registry.tool(
        name="webgis_scenario_compare",
        capabilities=["gis_scenario_harness"],
        tier=2,
        domains=["meta"],
        description=(
            "多候选结构化对比 + 共享基准漂移影响分级（只读）：逐候选差异行"
            "（层增删/样式字段/组件变化/指标 delta）+ 影响分级"
            "（current/recompute_required/adopt_blocked/unresolvable + 原因）。\n"
            "**纪律**：不同基准或数据版本的候选不可比时如实标注，不给虚假综合评分；"
            "数值仅在可比口径下呈现。"
        ),
        args_model=ScenarioCompareArgs,
        execution_policy=ToolExecutionPolicy.INLINE,
        side_effect="read_only",
        network=False,
        deterministic=True,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=["情境", "候选", "对比", "scenario", "比较"],
        output_semantic_type="json",
        result_size_policy="inline_small",
        failure_modes=["missing_data"],
    )
    async def webgis_scenario_compare(
        candidate_ids: Optional[List[str]] = None,
        session_id: Optional[str] = None,
    ) -> dict:
        if not session_id:
            return {"error": "Missing session_id context"}
        try:
            from app.services.scenario_harness.candidates import list_candidates
            from app.services.scenario_harness.compare import compare_candidates

            ids = list(candidate_ids or [])
            if not ids:
                ids = [
                    c.candidate_id
                    for c in await list_candidates(session_id)
                    if c.state in ("comparable", "stale", "draft", "running")
                ]
            if not ids:
                return {"ok": True, "result": None, "reason": "no_candidates"}
            result = await compare_candidates(session_id, ids)
            return {"ok": True, "result": result.model_dump(mode="json")}
        except Exception as exc:  # noqa: BLE001
            from app.services.scenario_harness.store import RegistryError

            if isinstance(exc, RegistryError):
                return _err(exc.code, str(exc))
            logger.warning("[scenario] compare failed: %s", type(exc).__name__)
            return _err("tool_error", f"{type(exc).__name__}")

    @registry.tool(
        name="webgis_scenario_adopt",
        capabilities=["gis_scenario_harness"],
        tier=2,
        domains=["meta"],
        description=(
            "把候选的选择性变更**采纳到权威地图**（父会话单事务，CAS + user-wins + "
            "锁检查 + 幂等回执）：mode=all/groups/seqs 选择采纳面；用户已改或锁定的"
            "字段不会被覆盖（逐项披露 user_wins_skip / agent_conflict / target_missing）。\n"
            "**纪律**：采纳前先 compare 展示差异；采纳是用户可见决议 —— Agent 请求采纳时"
            "必须已向用户呈现候选对比；采纳与用户在 UI 点采纳完全同链。"
        ),
        args_model=ScenarioAdoptArgs,
        execution_policy=ToolExecutionPolicy.INLINE,
        side_effect="state_mutation",
        data_mutations=["session_state", "mapspec"],
        network=False,
        deterministic=True,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=["情境", "候选", "采纳", "scenario", "合并"],
        output_semantic_type="json",
        result_size_policy="inline_small",
        failure_modes=["invalid_args", "missing_data", "stale_base", "lock_blocked"],
    )
    async def webgis_scenario_adopt(
        candidate_id: str = "",
        mode: str = "all",
        groups: Optional[List[str]] = None,
        seqs: Optional[List[int]] = None,
        expected_revision: Optional[int] = None,
        session_id: Optional[str] = None,
    ) -> dict:
        if not session_id:
            return {"error": "Missing session_id context"}
        if not candidate_id:
            return _err("invalid_args", "candidate_id 必填")
        try:
            from app.services.scenario_harness.adopt import adopt_candidate
            from app.services.scenario_harness.contracts import AdoptSelection
            from app.services.scenario_harness.store import RegistryError

            selection = AdoptSelection(
                mode=mode,  # type: ignore[arg-type]
                groups=groups or [],  # type: ignore[arg-type]
                seqs=seqs or [],
                expected_revision=expected_revision,
            )
            receipt = await adopt_candidate(
                session_id,
                candidate_id,
                selection,
                origin="agent",
                actor="agent_scenario_adopt",
                turn_id="",
            )
            return {"ok": True, "receipt": receipt.model_dump(mode="json")}
        except RegistryError as exc:
            return _err(exc.code, str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[scenario] adopt failed: %s", type(exc).__name__)
            return _err("tool_error", f"{type(exc).__name__}")

    @registry.tool(
        name="webgis_scenario_abandon",
        capabilities=["gis_scenario_harness"],
        tier=2,
        domains=["meta"],
        description=(
            "放弃候选：hard_delete=True 连隔离分支一起回收（内存/Redis/磁盘联动）；"
            "False 仅归档保留审计。已采纳候选不可 abandon（走回退）。"
        ),
        args_model=ScenarioAbandonArgs,
        execution_policy=ToolExecutionPolicy.INLINE,
        side_effect="state_mutation",
        data_mutations=["session_state"],
        network=False,
        deterministic=True,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=["情境", "候选", "放弃", "scenario"],
        output_semantic_type="json",
        result_size_policy="inline_small",
        failure_modes=["invalid_args", "missing_data"],
    )
    async def webgis_scenario_abandon(
        candidate_id: str = "",
        hard_delete: bool = True,
        reason: str = "",
        session_id: Optional[str] = None,
    ) -> dict:
        if not session_id:
            return {"error": "Missing session_id context"}
        if not candidate_id:
            return _err("invalid_args", "candidate_id 必填")
        try:
            from app.services.scenario_harness.candidates import (
                abandon_candidate,
                get_candidate,
            )
            from app.services.scenario_harness.store import RegistryError

            existing = await get_candidate(session_id, candidate_id)
            if existing.state == "adopted":
                return _err(
                    "abandon_adopted", "已采纳候选不可放弃（用 revert_adoption 回退）"
                )
            removed = await abandon_candidate(
                session_id,
                candidate_id,
                reason=reason,
                hard_delete=hard_delete,
            )
            return {"ok": True, "removed": removed}
        except RegistryError as exc:
            return _err(exc.code, str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[scenario] abandon failed: %s", type(exc).__name__)
            return _err("tool_error", f"{type(exc).__name__}")

    @registry.tool(
        name="webgis_scenario_lock",
        capabilities=["gis_scenario_harness"],
        tier=2,
        domains=["meta"],
        description=(
            "登记**用户显式锁定**（任务级「这个不许动」）：之后任何候选干预/采纳命中"
            "锁定字段都会被拒并披露。kind=layer_field/component_field/analysis_param。\n"
            "**纪律**：只能登记用户明确表达的锁定（如「这个配色定死」「分母必须用常住人口」），"
            "不得把 Agent 推断伪装成用户决定。"
        ),
        args_model=ScenarioLockArgs,
        execution_policy=ToolExecutionPolicy.INLINE,
        side_effect="state_mutation",
        data_mutations=["session_state"],
        network=False,
        deterministic=True,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=["情境", "锁定", "用户优先", "scenario"],
        output_semantic_type="json",
        result_size_policy="inline_small",
        failure_modes=["invalid_args", "capacity"],
    )
    async def webgis_scenario_lock(
        kind: str = "",
        target_id: str = "",
        field_paths: Optional[List[str]] = None,
        observed_value: str = "",
        reason: str = "",
        session_id: Optional[str] = None,
    ) -> dict:
        if not session_id:
            return {"error": "Missing session_id context"}
        if kind not in ("layer_field", "component_field", "analysis_param"):
            return _err("invalid_args", f"kind {kind!r} 非法")
        if not field_paths:
            return _err("invalid_args", "field_paths 必填")
        try:
            from app.services.scenario_harness.locks import set_lock
            from app.services.scenario_harness.store import RegistryError

            lock = await set_lock(
                session_id,
                kind=kind,
                target_id=target_id,
                field_paths=field_paths,
                observed_value=observed_value,
                reason=reason,
            )
            return {"ok": True, "lock": lock.model_dump(mode="json")}
        except RegistryError as exc:
            return _err(exc.code, str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[scenario] lock failed: %s", type(exc).__name__)
            return _err("tool_error", f"{type(exc).__name__}")

    @registry.tool(
        name="webgis_scenario_unlock",
        capabilities=["gis_scenario_harness"],
        tier=2,
        domains=["meta"],
        description=(
            "释放用户锁定：仅当用户明确要求解除时调用（Agent 无权自行解用户锁 —— "
            "服务端校验 origin，违规拒绝并留审计）。"
        ),
        args_model=ScenarioUnlockArgs,
        execution_policy=ToolExecutionPolicy.INLINE,
        side_effect="state_mutation",
        data_mutations=["session_state"],
        network=False,
        deterministic=True,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=["情境", "锁定", "释放", "scenario"],
        output_semantic_type="json",
        result_size_policy="inline_small",
        failure_modes=["invalid_args", "missing_data", "forbidden"],
    )
    async def webgis_scenario_unlock(
        lock_id: str = "",
        reason: str = "",
        session_id: Optional[str] = None,
    ) -> dict:
        if not session_id:
            return {"error": "Missing session_id context"}
        if not lock_id:
            return _err("invalid_args", "lock_id 必填")
        try:
            from app.services.scenario_harness.locks import release_lock
            from app.services.scenario_harness.store import RegistryError

            lock = await release_lock(session_id, lock_id, reason=reason, origin="user")
            return {"ok": True, "lock": lock.model_dump(mode="json")}
        except RegistryError as exc:
            return _err(exc.code, str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[scenario] unlock failed: %s", type(exc).__name__)
            return _err("tool_error", f"{type(exc).__name__}")


__all__ = ["register_scenario_harness_tools"]
