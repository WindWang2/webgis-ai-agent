"""Agent-facing composition tools（ADR-0214 D6）.

组合契约的 LLM 工具面——发现、应用、替换规划三件：

- ``webgis_discover_components``：按**结构化**地图目的（map_model /
  semantic_roles / output_target / artifact_types —— 禁 query 字符串，
  沿 ADR-0160 W0 负例纪律）发现组件与组合备选：recommend() 有界候选 +
  理由、``composition_alternatives_payload``（W5 接线，ADR-0160 W0.3
  契约的生产调用点）、组合契约目录、purpose presets。候选携带
  renderer/exporter 支持与 ABI 版本 —— live/export 能力缺失**提前披露**。
- ``webgis_apply_composition``：契约 → conformance 预检（error 级存在即
  fail-closed 不落盘）→ workbench 锁预检（命中即拒，
  ``component_locked:user_wins``）→ ``apply_contract`` 确定性重放 →
  经 SetLayoutIntent 单一写入通道提交（component_links + composition
  身份块随 layout 落盘）。幂等；用户未锁实例的原有编辑逐位保留。
  引擎守卫（guard_intent_locks）在事务内二次裁决 —— 预检只是提前失败。
- ``webgis_plan_component_replace``：**只读**替换规划器——同语义角色
  alternatives + ABI props 前置校验 + 锁预检 + conformance 预披露，返回
  精确的 ``webgis_component_update`` 调用参数。不写状态：组件突变的
  单一入口仍是 webgis_component_update（ADR-0070 PatchComponentIntent
  单变更入口 —— 本工具不做第二写路径）。

失败面全部携带结构化 reason codes（``COMPOSITION_TOOL_REASON_CODES``，
测试锁）。无 DB 触点。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool

#: reason code 词表（工具失败/拒绝面；测试锁）。
COMPOSITION_TOOL_REASON_CODES = (
    "missing_session",
    "contract_not_found",
    "contract_apply_error",
    "apply_superseded",
    "conformance_error",
    "component_locked:user_wins",
    "props_invalid",
    "component_not_found",
    "replace_same_role_required",
)

#: purpose 词表（presets 单一事实在 component_presets.PURPOSE_PRESETS；
#: 本词表用于参数校验与文档，测试锁同表）。
COMPOSITION_PURPOSES = (
    "basic_thematic",
    "heat_distribution_stats",
    "classified_categorical",
    "change_comparison",
)


async def _spec_of(session_id: str) -> Dict[str, Any]:
    from app.services.mapspec_store import mapspec_store
    return await mapspec_store.get_mapspec(session_id) or {}


def _spec_component_types(spec: Dict[str, Any]) -> List[str]:
    layout = spec.get("layout") if isinstance(spec, dict) else None
    raw = layout.get("components") if isinstance(layout, dict) else None
    if not isinstance(raw, list):
        return []
    types: List[str] = []
    for c in raw:
        if isinstance(c, dict):
            t = str(c.get("type") or "")
            if t:
                types.append(t)
    return types


def _contracts_bounded(purpose: str = "") -> List[Dict[str, Any]]:
    from app.lib.cartography.composition_contract import get_contract_registry
    contracts = get_contract_registry().all_contracts()
    if purpose:
        contracts = [c for c in contracts if c.purpose == purpose]
    return [c.to_bounded_dict() for c in contracts[:8]]


def _presets_bounded() -> List[Dict[str, Any]]:
    try:
        from app.lib.cartography.component_presets import purpose_bundles_bounded
        return purpose_bundles_bounded()
    except ImportError:  # pragma: no cover - presets 由同方向 milestone 提供
        return []


class DiscoverComponentsArgs(BaseModel):
    map_model: str = Field(default="", description="主表达 MapModel id（可别名）")
    semantic_roles: List[str] = Field(
        default_factory=list,
        description="期望语义角色（SEMANTIC_ROLES 词表：legend/orientation/"
                    "title_block/statistics/annotation/inset/…）")
    output_target: str = Field(
        default="interactive", description="interactive/png/pdf/svg/print")
    artifact_types: List[str] = Field(default_factory=list, description="在场产物语义类型")
    task_categories: List[str] = Field(default_factory=list, description="任务类目")
    geometry_kind: str = Field(
        default="polygon", description="point/line/polygon/raster（备选组合用）")
    purpose: str = Field(
        default="", description="地图目的过滤（basic_thematic/heat_distribution_stats/"
                                "classified_categorical/change_comparison）")
    limit: int = Field(default=8, ge=1, le=16, description="候选上限")


def register_composition_tools(registry: ToolRegistry) -> None:
    """注册组合契约三工具（app/tools/__init__.py 注册表挂载）。"""

    @tool(
        registry,
        tier=2, domains=["report"], name="webgis_discover_components",
        capabilities=["thematic_cartography"],
        description=(
            "按结构化地图目的发现可用制图组件与组合备选（只读，不改状态）。"
            "\n输入是结构化上下文（map_model / semantic_roles / output_target / "
            "artifact_types / task_categories），不接受自由文本 query。"
            "\n返回：有界候选组件（含 renderer/exporter 支持、ABI 版本、确定性"
            "得分与理由——导出能力缺失提前披露）+ 组合备选版面 + 组合契约目录"
            "（contract.core.*）+ purpose presets。"
            "\n典型链路：本工具发现 → webgis_apply_composition 应用契约 → "
            "webgis_component_update 局部突变。"
        ),
        args_model=DiscoverComponentsArgs,
        side_effect="pure",
        deterministic=True,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=("组件", "发现", "组合", "模板", "备选", "discover"),
        output_semantic_type="json",
        result_size_policy="bounded",
        failure_modes=("invalid_args",),
        summary=(
            "按地图目的/模型/角色发现组件与组合契约（只读）；候选带支持矩阵"
            "与理由，备选版面与 purpose presets 一并返回。"
        ),
        examples=(
            "做一幅行政区分级专题图有哪些组件可用",
            "热力图适合什么组合版面",
        ),
        anti_examples=(
            "搜索『好看的颜色』——本工具收结构化上下文，不收自由文本 query",
        ),
    )
    async def webgis_discover_components(
        session_id: Optional[str] = None,
        map_model: str = "",
        semantic_roles: Optional[List[str]] = None,
        output_target: str = "interactive",
        artifact_types: Optional[List[str]] = None,
        task_categories: Optional[List[str]] = None,
        geometry_kind: str = "polygon",
        purpose: str = "",
        limit: int = 8,
    ) -> dict:
        from app.lib.cartography.component_abi import abi_record_for
        from app.lib.cartography.component_registry import (
            ComponentRecommendationContext,
            get_component_registry,
        )
        from app.lib.cartography.composition_selection import (
            composition_alternatives_payload,
        )

        spec = await _spec_of(session_id) if session_id else {}
        existing_types = _spec_component_types(spec)
        comp_reg = get_component_registry()
        ctx = ComponentRecommendationContext(
            map_model=map_model,
            output_target=output_target,
            task_categories=tuple(task_categories or ()),
            semantic_roles=tuple(semantic_roles or ()),
            existing_components=tuple(existing_types),
            artifact_types=tuple(artifact_types or ()),
        )
        recommendations = comp_reg.recommend(ctx, limit=limit)
        candidates: List[Dict[str, Any]] = []
        for rec in recommendations:
            desc = comp_reg.get(rec.component_id)
            if desc is None:
                continue
            abi = abi_record_for(desc)
            candidates.append({
                **abi.to_bounded_dict(),
                "score": rec.score,
                "reasons": [r[:64] for r in rec.reasons[:4]],
            })
        alt_ctx_payload: Dict[str, Any] = {}
        try:
            from app.lib.cartography.composition_selection import TaskCartographyContext
            alt_ctx = TaskCartographyContext(
                task_categories=tuple(task_categories or ()),
                geometry_kind=geometry_kind,
                output_target=output_target,
                artifact_types=tuple(artifact_types or ()),
                available_components=tuple(existing_types),
            )
            alt_ctx_payload = composition_alternatives_payload(alt_ctx)
        except Exception:  # noqa: BLE001 - 备选面失败不阻塞候选发现（如实披露）
            alt_ctx_payload = {"version": 0, "count": 0, "candidates": [],
                               "disclosure": "alternatives_unavailable"}
        return {
            "success": True,
            "candidates": candidates,
            "candidate_count": len(candidates),
            "composition_alternatives": alt_ctx_payload,
            "contracts": _contracts_bounded(purpose),
            "purpose_presets": _presets_bounded(),
            "existing_component_types": sorted(set(existing_types))[:16],
            "reason_codes": [],
        }

    class ApplyCompositionArgs(BaseModel):
        contract_id: str = Field(description="组合契约 id（contract.core.*）")
        expected_revision: Optional[int] = Field(
            default=None,
            description="乐观并发：先读 mutation_revision，落后会被 superseded")

    @tool(
        registry,
        tier=2, domains=["report"], name="webgis_apply_composition",
        capabilities=["thematic_cartography"],
        description=(
            "把 versioned 组合契约（contract.core.*）确定性地应用到当前会话："
            "只填空槽位、保留用户已有组件编辑、用户锁定（workbench "
            "lockedComponentIds）的组件零触碰并披露。应用前自动做 conformance "
            "预检（导出能力缺口/版本兼容为 error 时拒绝落盘）。"
            "\n同一契约重复应用幂等（零重复创建）；应用后 layout.composition "
            "携带模板/契约/ABI 版本身份（进入指纹链）。"
            "\n单组件微调用 webgis_component_update；本工具是版面级组合。"
        ),
        args_model=ApplyCompositionArgs,
        side_effect="state_mutation",
        deterministic=False,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=("组合", "契约", "模板应用", "版面", "apply"),
        output_semantic_type="json",
        result_size_policy="bounded",
        required_context=("map_state",),
        map_mutations=("component",),
        data_mutations=("session_state",),
        failure_modes=("invalid_args", "missing_data"),
        summary=(
            "应用组合契约：conformance + 锁预检后确定性填充空槽位，"
            "保留用户编辑与锁；写入组合身份数据块。"
        ),
        examples=(
            "按分类专题图契约组织版面",
            "应用基础专题图组合",
        ),
        anti_examples=(
            "把标题字改大——那是 webgis_component_update 的局部突变",
        ),
    )
    async def webgis_apply_composition(
        session_id: Optional[str] = None,
        contract_id: str = "",
        expected_revision: Optional[int] = None,
    ) -> dict:
        from app.lib.cartography.composition_contract import (
            ContractApplyError,
            apply_contract,
            get_contract_registry,
        )
        from app.lib.cartography.component_abi import COMPONENT_ABI_VERSION

        if not session_id:
            return {"success": False, "reason_codes": ["missing_session"],
                    "message": "Missing session_id"}
        contract = get_contract_registry().get(contract_id)
        if contract is None:
            from app.lib.cartography.composition_contract import (
                get_contract_registry as gcr,
            )
            known = [c.contract_id for c in gcr().all_contracts()][:8]
            return {
                "success": False, "reason_codes": ["contract_not_found"],
                "message": f"contract {contract_id} 未注册",
                "correction_hint": f"可用契约: {known}",
            }
        spec = await _spec_of(session_id)

        # conformance 预检（ADR-0214 D5）：error 级 → fail-closed 不落盘。
        try:
            from app.lib.cartography.composition_conformance import (
                conformance_report,
                report_to_dicts,
            )
            issues = conformance_report(spec, contract=contract)
            errors = [i for i in issues if i.severity == "error"]
            if errors:
                return {
                    "success": False,
                    "reason_codes": ["conformance_error"],
                    "message": "conformance 预检存在 error 级问题，未落盘",
                    "issues": report_to_dicts(errors[:8]),
                }
            warnings = report_to_dicts(
                [i for i in issues if i.severity == "warning"][:8])
        except ImportError:  # pragma: no cover - conformance 由同方向 milestone 提供
            warnings = []

        # workbench 锁预检：被锁组件若在提交载荷内（全表替换语义）→
        # 提前失败（引擎守卫会二次拒绝 —— 这里给出更精确的 reason）。
        from app.lib.cartography.composition_contract import (
            locked_component_ids_of,
        )
        locked_ids = set(locked_component_ids_of(spec))
        payload_ids = {
            str(c.get("id") or "")
            for c in (spec.get("layout") or {}).get("components") or []
            if isinstance(c, dict)}
        hit_locked = sorted(locked_ids & payload_ids)
        if hit_locked:
            return {
                "success": False,
                "reason_codes": ["component_locked:user_wins"],
                "message": "存在用户锁定组件在提交载荷内，契约应用拒绝（全表替换语义）",
                "locked_component_ids": hit_locked[:16],
                "correction_hint": (
                    "请用户在 workbench 解锁对应组件后再应用契约；或先用 "
                    "webgis_plan_component_replace 规划不受锁影响的局部替换。"),
            }

        revision = int(expected_revision or 0)
        try:
            new_spec, report = apply_contract(spec, contract, revision=revision)
        except ContractApplyError as exc:
            return {"success": False, "reason_codes": ["contract_apply_error"],
                    "message": str(exc)[:200]}

        layout = new_spec.get("layout") or {}
        from app.services.mapspec_store import mapspec_store
        res = await mapspec_store.layout_set(
            session_id,
            components=layout.get("components") or [],
            component_links=layout.get("component_links") or [],
            composition=layout.get("composition") or {},
            # 乐观并发：读-改-写窗口内的并发突变 → superseded 而非静默覆盖。
            expected_revision=expected_revision,
            origin="agent", actor="composition_apply",
        )
        out: Dict[str, Any] = {
            "success": bool(res.get("success", False)),
            "reason_codes": [],
            "report": report.to_bounded_dict(),
            "component_abi_version": COMPONENT_ABI_VERSION,
        }
        if warnings:
            out["conformance_warnings"] = warnings
        if res.get("mapspec_fingerprint"):
            out["mapspec_fingerprint"] = res["mapspec_fingerprint"]
        if res.get("mutation_revision") is not None:
            out["mutation_revision"] = res["mutation_revision"]
        if not out["success"]:
            # review P2-1：引擎拒绝必须映射为机器可读 reason code（契约面
            # 承诺结构化理由，不留裸中文 message）。单码契约：引擎锁拒绝
            # error_code=layer_locked（W15 前端消费同码）→ 组件域语义码。
            if res.get("status") == "superseded":
                out["reason_codes"] = ["apply_superseded"]
            elif res.get("error_code") == "layer_locked":
                out["reason_codes"] = ["component_locked:user_wins"]
                if res.get("locked_component_ids"):
                    out["locked_component_ids"] = res["locked_component_ids"]
            else:
                out["reason_codes"] = ["contract_apply_error"]
            out["message"] = str(res.get("message") or "layout_set failed")[:200]
            if res.get("correction_hint"):
                out["correction_hint"] = res["correction_hint"]
        return out

    class PlanReplaceArgs(BaseModel):
        component_id: str = Field(default="", description="目标组件实例 id")
        component_type: str = Field(
            default="", description="或按类型寻址（该类型唯一实例时）")
        to_template_id: str = Field(
            default="", description="目标组件模板 id（如 north-arrow/compass-rose）")
        to_variant: str = Field(default="", description="或目标 variant 名")
        options: Optional[Dict[str, Any]] = Field(
            default=None, description="随替换写入的 options（ABI props 校验前置）")

    @tool(
        registry,
        tier=2, domains=["report"], name="webgis_plan_component_replace",
        capabilities=["thematic_cartography"],
        description=(
            "只读规划一次组件替换（换指南针/图例样式/色卡/统计面板）：返回同"
            "语义角色的可选替代（有界 + 理由）、ABI props 前置校验、用户锁预检"
            "与 conformance 预披露，以及可直接执行的 webgis_component_update "
            "参数。本工具不改状态 —— 确认后用 webgis_component_update 提交。"
        ),
        args_model=PlanReplaceArgs,
        side_effect="pure",
        deterministic=True,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=("替换", "组件", "规划", "替代", "replace"),
        output_semantic_type="json",
        result_size_policy="bounded",
        failure_modes=("invalid_args",),
        summary=(
            "规划组件替换（只读）：替代项 + props 校验 + 锁预检 + "
            "webgis_component_update 执行参数。"
        ),
        examples=(
            "把指南针换成玫瑰罗盘",
            "图例换成学术样式",
        ),
        anti_examples=(
            "直接替我换上——本工具只规划，提交用 webgis_component_update",
        ),
    )
    async def webgis_plan_component_replace(
        session_id: Optional[str] = None,
        component_id: str = "",
        component_type: str = "",
        to_template_id: str = "",
        to_variant: str = "",
        options: Optional[Dict[str, Any]] = None,
    ) -> dict:
        from app.lib.cartography.component_abi import (
            abi_record_for,
            validate_props,
        )
        from app.lib.cartography.component_registry import get_component_registry
        from app.lib.cartography.component_templates import (
            get_component_template_registry,
        )

        if not session_id:
            return {"success": False, "reason_codes": ["missing_session"],
                    "message": "Missing session_id"}
        if not component_id and not component_type:
            return {
                "success": False, "reason_codes": ["component_not_found"],
                "message": "component_id 或 component_type 必须提供其一",
            }
        spec = await _spec_of(session_id)
        layout = spec.get("layout") or {}
        raw_components = [
            c for c in (layout.get("components") or []) if isinstance(c, dict)]
        target = None
        for c in raw_components:
            if component_id and c.get("id") == component_id:
                target = c
                break
            if not component_id and component_type and c.get("type") == component_type:
                target = c
                break
        if target is None:
            current = ", ".join(
                f"{c.get('id')}({c.get('type')})" for c in raw_components[:12])
            return {
                "success": False, "reason_codes": ["component_not_found"],
                "message": "未找到目标组件",
                "correction_hint": f"当前组件: {current or '（无）'}",
            }
        ctype = str(target.get("type") or "")
        comp_reg = get_component_registry()
        desc = comp_reg.get_by_type(ctype)
        # 锁预检（W15 workbench 锁集；引擎守卫会二次拒绝）
        from app.lib.cartography.composition_contract import (
            LOCK_REASON_USER_WINS,
            locked_component_ids_of,
        )
        locked_ids = set(locked_component_ids_of(spec))
        is_locked = str(target.get("id") or "") in locked_ids
        # 同语义角色 alternatives
        role = desc.semantic_role if desc is not None else ""
        alternatives: List[Dict[str, Any]] = []
        if role:
            for other in comp_reg.native_descriptors():
                if other.semantic_role == role and other.type != ctype:
                    abi = abi_record_for(other)
                    alternatives.append({
                        "type": other.type[:32],
                        "name_zh": other.name_zh[:32],
                        "score": int(other.priority),
                        "abi": abi.to_bounded_dict(),
                    })
            alternatives.sort(key=lambda a: (a["score"], a["type"]))
            alternatives = alternatives[:6]
        # 组件模板级替代（同型不同 variant/preset）
        tmpl_reg = get_component_template_registry()
        same_type_templates = [
            {"template_id": t.id[:48], "variant": t.variant[:32],
             "runtime_status": t.runtime_status}
            for t in tmpl_reg.find_by_type(ctype)
            if not t.deprecated
        ][:8]
        # ABI props 前置校验
        props_issues: List[str] = []
        if options:
            props_issues = validate_props(ctype, options)
        if to_template_id:
            t = tmpl_reg.get(to_template_id)
            if t is None or t.component_type != ctype:
                props_issues.append("props_invalid:to_template_id:type_mismatch")
        plan: Dict[str, Any] = {
            "read_only": True,
            "target": {"id": str(target.get("id") or "")[:48],
                       "type": ctype[:32], "semantic_role": role[:24]},
            "locked": is_locked,
            "locked_reason": LOCK_REASON_USER_WINS if is_locked else "",
            "alternatives": alternatives,
            "same_type_templates": same_type_templates,
            "props_issues": props_issues[:8],
            "conformance_note": (
                "应用后可再跑 webgis_validate；导出能力缺口见候选的 "
                "exporter_support 字段"),
        }
        if is_locked:
            plan["update_params"] = None
            plan["disclosure"] = (
                "目标被用户锁定：替换被拒绝（component_locked:user_wins）；"
                "请用户解锁后重试")
        elif props_issues:
            plan["update_params"] = None
            plan["disclosure"] = "props 校验未通过：修正后再提交"
        else:
            merged_options = dict(options or {})
            if to_variant:
                merged_options.setdefault("variant", to_variant)
            plan["update_params"] = {
                "tool": "webgis_component_update",
                "params": {
                    "session_id": session_id,
                    "component_id": str(target.get("id") or ""),
                    "variant": to_variant or None,
                    "options": merged_options or None,
                    "expected_revision": None,
                },
            }
            plan["disclosure"] = (
                "执行参数就绪：调用 webgis_component_update 提交"
                "（先读 webgis_component_catalog 的 mutation_revision 作 "
                "expected_revision）")
        success = not is_locked and not props_issues
        return {
            "success": success,
            "reason_codes": (
                ([LOCK_REASON_USER_WINS] if is_locked else []) +
                (["props_invalid"] if props_issues else [])),
            "plan": plan,
        }
