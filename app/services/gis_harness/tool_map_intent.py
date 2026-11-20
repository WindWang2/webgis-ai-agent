"""Independently testable webgis_map_intent handler and explicit registration."""
from __future__ import annotations
import asyncio
from app.tools.registry import tool
from functools import partial

from app.services.gis_harness.tools import (
    ToolRegistry,
    Any,
    Dict,
    MapIntentArgs,
    Optional,
    _project_verified_recipes,
    logger,
)


async def webgis_map_intent(
    registry: ToolRegistry,
    query: str,
    task_hint: Optional[str] = None,
    scope_hint: Optional[str] = None,
    subject_hint: Optional[str] = None,
    geometry_hint: Optional[str] = None,
) -> dict:
    from app.services.gis_harness.intent import merge_intent_hints, resolve_map_request_intent
    from app.services.gis_harness.planner_runtime import get_planner_runtime

    base = resolve_map_request_intent(query)
    hints: Dict[str, Any] = {}
    if task_hint:
        hints["task"] = task_hint
    if geometry_hint:
        hints["geometry_expectation"] = geometry_hint
    intent = merge_intent_hints(base, hints)

    # 方向 9（ADR-0255）R6 复用缝：本轮 scope 未解析（「再看看医院」）
    # 且调用方未给 scope_hint 时，以记忆里最近仍可信的 resolved_place
    # 兜底——fresh 解析永远优先；兜底走 hint_applied 披露 + 置信折扣，
    # 是「沿用上次的范围」的显式先验，不是本轮事实。
    try:
        from app.lib.runtime.context import current_runtime_context

        _rt = current_runtime_context()
        _sid = getattr(_rt, "session_id", "") if _rt else ""
        if _sid and not intent.scope.name and not scope_hint:
            from app.services.gis_memory.queries import (
                consult_scope_fallback_sync,
                read_memory_identity,
            )

            _org, _mem_user = await read_memory_identity(_sid)
            if _org:
                _fallback = await asyncio.to_thread(
                    consult_scope_fallback_sync, _org, _sid,
                    getattr(_rt, "project_id", None),
                )
                if _fallback and _fallback.get("subject"):
                    from app.services.gis_harness.intent import ScopeIntent

                    _value = _fallback.get("value") or {}
                    _level = str(_value.get("level") or "city")
                    if _level not in ("country", "province", "city", "district"):
                        _level = "city"
                    _name = str(_fallback.get("subject"))
                    intent.scope = ScopeIntent(name=_name, level=_level)
                    _conf = 0.9 * float(_fallback.get("confidence") or 0.0)
                    intent.hint_applied.append(
                        f"memory_scope->{_name}({_level}; prior_conf≈{_conf:.2f})"
                    )
    except Exception as _mem_exc:  # noqa: BLE001 — 记忆兜底绝不阻断意图解析
        logger.debug("[GISMemory] map_intent scope fallback skipped: %s", _mem_exc)
    # 解析成功（fresh 或 hint）→ resolved_place/boundary_ref 候选入
    # pending 缓冲（org 由 turn 端 harvest 烙印——工具热路径零 IO）。
    if intent.scope.name:
        try:
            from app.lib.runtime.context import current_runtime_context
            from app.services.gis_memory.contract import (
                KIND_BOUNDARY_REF,
                KIND_RESOLVED_PLACE,
                SCOPE_SESSION,
                SOURCE_INTENT_RESOLUTION,
                MemoryEvidence,
                MemoryWriteRequest,
            )
            from app.services.gis_memory.pending import pending_memory_buffer

            _rt2 = current_runtime_context()
            _sid2 = getattr(_rt2, "session_id", "") if _rt2 else ""
            if _sid2:
                pending_memory_buffer.offer(_sid2, MemoryWriteRequest(
                    kind=KIND_RESOLVED_PLACE,
                    scope=SCOPE_SESSION, scope_id=_sid2,
                    subject=intent.scope.name,
                    value={"name": intent.scope.name, "level": intent.scope.level},
                    evidence=MemoryEvidence(
                        source=SOURCE_INTENT_RESOLUTION, method="map_intent",
                        turn_id=getattr(_rt2, "turn_id", None),
                    ),
                    confidence=0.8, org_id="",
                ))
                pending_memory_buffer.offer(_sid2, MemoryWriteRequest(
                    kind=KIND_BOUNDARY_REF,
                    scope=SCOPE_SESSION, scope_id=_sid2,
                    subject=intent.scope.name,
                    value={
                        "level": intent.scope.level,
                        "identity": f"local:admin:{intent.scope.level}:{intent.scope.name}",
                    },
                    evidence=MemoryEvidence(
                        source=SOURCE_INTENT_RESOLUTION, method="map_intent",
                        turn_id=getattr(_rt2, "turn_id", None),
                    ),
                    confidence=0.75, org_id="",
                ))
        except Exception as _mem_cand_exc:  # noqa: BLE001 — 记忆候选绝不阻断
            logger.debug("[map_intent] memory candidates skipped: %s", _mem_cand_exc)
    # scope/subject 提示只做补全（不覆盖确定性命中）
    if scope_hint and not intent.scope.name:
        from app.services.gis_harness.intent import ScopeIntent
        intent.scope = ScopeIntent(name=scope_hint, level="city")
        intent.hint_applied.append(f"scope->{scope_hint}")
    if subject_hint and intent.subject.type == "unknown":
        # #785: 主体提示先经栅格/面/线 token 表定类型（如 NDVI → raster），
        # 不再无条件 poi —— 否则栅格数据的 evidence 声称 POI 主体、
        # geometry_expectation 停留在旧值。token 表无命中才默认 poi。
        from app.services.gis_harness.intent import (
            SubjectIntent,
            _entity_geometry,
            _match_subject,
        )
        typed = _match_subject(subject_hint)
        if typed.type != "unknown":
            intent.subject = typed
        else:
            intent.subject = SubjectIntent(type="poi", category=subject_hint)
        intent.entity_type = intent.subject.type
        intent.geometry_expectation = _entity_geometry(
            intent.subject, intent.task)
        intent.hint_applied.append(f"subject->{subject_hint}")

    planner = get_planner_runtime()
    # ADR-0069 / spec 开放问题 3：推荐排序带项目记忆——本项目验证过的
    # recipe 前置。project_id 来自 turn 级 RuntimeContext（HTTP 入口
    # 绑定），无项目上下文时 verified 为空集，排序与既有行为一致。
    # V1（ADR-0181）：situation 参与候选资格层（当前调用面只有 task
    # 语义 —— 其余事实缺席保持 unknown 诚实披露；plan.capability_evidence
    # 携带能力级候选/资格证据）。
    try:
        from app.services.gis_harness.capability_resolution import (
            capability_planning_v1_enabled,
            build_situation,
        )

        situation = (
            build_situation(task_hint=str(intent.task or ""))
            if capability_planning_v1_enabled() else None
        )
    except Exception:  # noqa: BLE001 — 能力层缺席不阻断意图解析
        situation = None
    candidates = planner.recipes.select_candidates(
        intent, project_verified=await _project_verified_recipes(),
        situation=situation,
    )
    try:
        available = set(registry.list_tools())
    except Exception:  # noqa: BLE001 - 能力解析是建议性信息
        available = set()
    # audit #825: 把注册表可见工具传给 planner —— 解析不到的能力在 plan
    # 里标记 unavailable（docstring 承诺的诚实报告）。
    plan = await asyncio.to_thread(
        planner.plan_from_intent, intent, available_tools=available or None, situation=situation)

    capabilities = []
    # #1076(D-7): resolved_tool 与 resolved_algorithm 同源于
    # plan.algorithm_selections（此前 tool 字段对每 capability 再跑一遍
    # resolver —— probe 实测恰 2× 解析，且两字段可能分叉）。
    selection_by_cap = {
        r.capability: r for r in plan.algorithm_selections
    }
    for req in plan.data_requirements:
        record = selection_by_cap.get(req.capability)
        capabilities.append({
            "capability": req.capability,
            "purpose": req.purpose,
            "resolved_tool": (
                record.tool if record and record.status == "resolved" else ""
            ),
            "resolved_algorithm": (
                record.algorithm if record and record.status == "resolved"
                else ""
            ),
        })

    # audit4 #979: 有界 guidance 投影 —— slim_tool_result 的 summary 分支
    # 此前把 intent/candidates/plan 整包丢弃，capability→tool 裁决从未
    # 到达 LLM（harness 只有建议权的具体机制）。guidance 在
    # _PRESERVED_META_KEYS 白名单内，每行一条裁决，总量闸兜底。
    guidance = [
        f"{c['capability']} → {c['resolved_tool'] or '无对应工具(需自选)'}"
        f"（{c['purpose']}）"
        for c in capabilities[:10]
    ]
    if len(capabilities) > 10:
        guidance.append(f"（另有 {len(capabilities) - 10} 项数据需求，见 map_product 阶段）")
    missing = [c["capability"] for c in capabilities if not c["resolved_tool"]]
    if missing:
        guidance.append(
            "⚠ 未解析能力: " + ", ".join(missing[:5]) + " —— 计划内无注册工具，需换路径"
        )
    # v3(Phase D)：依赖序披露 —— plan 已携带 depends_on/optional；这里
    # 给一行有界摘要（什么必须先执行）。无依赖边时不加行（零噪声）。
    try:
        from app.services.gis_harness.plan_graph import build_plan_graph
        graph = build_plan_graph(plan)
        dep_pairs = [
            f"{n.capability} <- {','.join(n.depends_on)}"
            for n in graph.nodes if n.depends_on
        ]
        if dep_pairs:
            guidance.append("依赖序: " + "; ".join(dep_pairs[:4]))
    except Exception as _cap_graph_exc:  # noqa: BLE001 — 增值信号不阻断意图解析
        logger.debug("[map_intent] capability graph guidance skipped: %s", _cap_graph_exc)

    # V1（ADR-0181）：能力资格摘要 —— situation 提供时
    # plan.capability_evidence 携带 status_summary；非 eligible 的必需
    # 能力以一行披露（为什么 + 怎么补），供 LLM 在数据工具选择前知情。
    try:
        evidence = plan.capability_evidence or {}
        summary = evidence.get("status_summary") or {}
        if summary:
            guidance.append(
                "能力资格: " + " ".join(
                    f"{k}×{v}" for k, v in sorted(summary.items())))
            for dec in (evidence.get("decisions") or []):
                if dec.get("required") and dec.get("status") == "ineligible":
                    hints = "; ".join(dec.get("make_available", [])[:2])
                    guidance.append(
                        f"⚠ 能力 {dec.get('capability')} 失格 — {hints}")
    except Exception as _eligibility_exc:  # noqa: BLE001 — 增值信号不阻断意图解析
        logger.debug("[map_intent] eligibility guidance skipped: %s", _eligibility_exc)

    # D04：Situation→plan SkillPolicy attach (kill-switch → omit keys).
    skill_guidance = {}
    hotpath_pi = {}
    plan_payload = plan.model_dump()
    # ADR-0220：Skill procedure → 计划的结构化绑定（派生投影；随
    # plan payload 经既有 apply_tool_result 路径持久化进 gis_chapter；
    # 技能不可绑定 → 无键，planner 原路回落）。
    try:
        from app.services.gis_harness.hotpath_convergence import (
            bind_skill_guidance_at_plan_seam,
            build_hotpath_pi_context,
            set_skill_bundle,
        )
        # G30：chat.skill_name 显式选择（turn 端解析后存 session ctx）——
        # 计划缝尊重用户选择（policy 内仍裁决 eligibility/弃用/隔离）。
        _explicit_skill_id = ""
        try:
            from app.lib.runtime.context import current_runtime_context
            from app.services.gis_harness.hotpath_convergence.session_ctx import (
                peek_explicit_skill,
            )
            _rt_es = current_runtime_context()
            _sid_es = str(getattr(_rt_es, "session_id", "") or "") if _rt_es else ""
            _sel = peek_explicit_skill(_sid_es) if _sid_es else None
            if _sel and _sel.get("skill_id"):
                _explicit_skill_id = str(_sel["skill_id"])[:80]
        except Exception:  # noqa: BLE001 — 增值信号不阻断
            logger.debug("[map_intent] explicit skill peek skipped", exc_info=True)
        _inputs, _bundle = bind_skill_guidance_at_plan_seam(
            intent, situation=situation,
            explicit_skill_id=_explicit_skill_id,
        )
        skill_guidance = dict(_inputs.get("skill_guidance") or {})
        if _bundle is not None:
            try:
                from app.lib.runtime.context import current_runtime_context
                _rt_hp = current_runtime_context()
                sid = str(getattr(_rt_hp, "session_id", "") or "") if _rt_hp else ""
            except Exception:  # noqa: BLE001
                sid = ""
            if sid:
                set_skill_bundle(sid, _bundle, skill_guidance)
            hotpath_pi = build_hotpath_pi_context(skill_bundle=_bundle)
            # ── ADR-0220 C1/C2/C3：绑定 + plan-time enforcement ──
            try:
                from app.services.gis_harness.skills.loader import (
                    get_skill_library,
                )
                from app.services.gis_harness.skills.procedure_binding import (
                    bind_procedure_to_plan,
                    enforce_procedure_binding,
                )

                _lib = get_skill_library()
                _skill = (
                    _lib.get(_bundle.decision.selected_skill)
                    if _bundle.decision.selected_skill else None
                )
                # review P2-8：生产 resolver 不带 _capability_exists
                # 谓词 —— 显式走 bridges 的默认谓词（CapabilityRegistry
                # 存在性；缺席时保守 False），硬能力缺口披露才可在
                # 生产路径生效。
                from app.services.gis_harness.skills.bridges import (
                    default_capability_exists,
                )
                _binding = bind_procedure_to_plan(
                    _skill, _bundle.decision, plan_payload,
                    capability_exists=default_capability_exists,
                )
                if _binding is not None:
                    _block = _binding.to_bounded_dict()
                    plan_payload["procedure_binding"] = _block
                    _check = enforce_procedure_binding(
                        _binding, plan_payload)
                    if not _check.ok:
                        # C3：违例进 guidance（披露不阻断；execute_guided
                        # 为硬提示，guide 为 advisory）—— 计划缺口不再
                        # "静默看起来完整"。
                        for v in _check.violations[:3]:
                            guidance.append(
                                f"⚠ 方法步骤缺口 [{v.code}] {v.subject}:"
                                f" {v.hint}")
                    # C7：有界 procedure 卡片（进 hotpath pi context）。
                    try:
                        from app.services.gis_harness.skills.procedure_coverage import (
                            compute_procedure_coverage,
                            procedure_pi_card,
                        )

                        _coverage = compute_procedure_coverage({
                            "procedure_binding": _block,
                            "data_requirements":
                                plan_payload.get("data_requirements") or [],
                            "analysis_steps":
                                plan_payload.get("analysis_steps") or [],
                        })
                        _card = procedure_pi_card(_coverage, check=_check)
                        if _card:
                            hotpath_pi = dict(hotpath_pi)
                            hotpath_pi["procedure"] = _card
                    except Exception as _pi_card_exc:  # noqa: BLE001 — 卡片是增值披露
                        logger.debug("[map_intent] procedure pi card skipped: %s", _pi_card_exc)
                    # 证据面（fail-open telemetry）：绑定即记录组合计划。
                    try:
                        from app.services.gis_harness.skills.evidence import (
                            SkillEvidenceRecord,
                        )
                        from app.tools.skill_library_tools import (
                            get_skill_evidence_recorder,
                        )

                        get_skill_evidence_recorder().record(
                            SkillEvidenceRecord(
                                event="composition_planned",
                                skill_id=_binding.skill_id,
                                skill_version=_binding.skill_version,
                                execution_order=[
                                    s.step_id for s in _binding.steps[:8]],
                            ))
                    except Exception as _telemetry_exc:  # noqa: BLE001 — 证据失败不阻断
                        logger.debug("[map_intent] procedure telemetry skipped: %s", _telemetry_exc)
            except Exception:  # noqa: BLE001 — 绑定失败=无键回落
                plan_payload.pop("procedure_binding", None)
    except Exception:  # noqa: BLE001 — additive; never block intent
        skill_guidance = {}
        hotpath_pi = {}
        plan_payload.pop("procedure_binding", None)
    out = {
        "success": True,
        "intent": intent.model_dump(),
        "candidates": [
            {"id": r.id, "name": r.name, "primary_cartography": r.primary_cartography,
             "description": r.description}
            for r in candidates
        ],
        "recommended_recipe": plan.recipe_id,
        "plan": plan_payload,
        "capabilities": capabilities,
        "guidance": guidance,
        "summary": (
            f"意图:{intent.task} 范围:{intent.scope.name or '未识别'} "
            f"主体:{intent.subject.category or '未识别'} → 推荐 recipe:{plan.recipe_id}"
        ),
    }
    if skill_guidance:
        out["skill_guidance"] = skill_guidance
    if hotpath_pi:
        out["hotpath_pi_context"] = hotpath_pi
    # F02（ADR-0266）：requirement IR 面——omittable 加法键（仿
    # skill_guidance 先例：fail-open，绝不阻断意图主链路；复用同一
    # 解析产物保证 intent 载荷与 requirement 文档一致）。
    # 方向 3 修复（ADR-0219 recon 同期独立发现）：此前调用把 session_id
    # 误传为 session_store、query 误传为 session_id（等价于漏传三参形状，
    # TypeError 被本 except 静默吞掉）——F02 生产缝实为死代码，每次意图
    # 解析都重建文档、IR 状态永不持久（澄清去重/答案 journal 全失效）。
    # 现按 service 签名传入生产 SessionStore 与真实参数。
    try:
        from app.lib.runtime.context import current_runtime_context as _crc
        _rt_req = _crc()
        _req_sid = str(getattr(_rt_req, "session_id", "") or "") if _rt_req else ""
        if _req_sid:
            from app.services.session_data import session_data_manager as _sdm
            from app.services.gis_harness.requirement_ir import service as _req_svc
            try:
                _turn = int(getattr(_rt_req, "turn_id", 0) or 0)
            except (TypeError, ValueError):
                _turn = 0
            _req = await _req_svc.ensure_document(
                _sdm, _req_sid, query, core=intent, turn=_turn)
            _req_payload = dict(_req["summary"])
            # patch 拒绝/超替事件随载荷透出（否则用户修订被拒时无信号）
            _req_payload["patch_errors"] = _req["patch_errors"]
            _req_payload["superseded"] = _req["superseded"]
            out["requirement"] = _req_payload
    except Exception as _req_exc:  # noqa: BLE001 — requirement 面绝不阻断 intent
        logger.debug("[requirement_ir] intent seam skipped: %s", _req_exc)
    # ADR-0219 C3：requirement → MapSpec 绑定应用（显式用户表达面经
    # 正式 mutation 事务落地；omittable 加法键，fail-open）。
    try:
        from app.lib.runtime.context import current_runtime_context as _crc_rb
        _rt_rb = _crc_rb()
        _rb_sid = str(getattr(_rt_rb, "session_id", "") or "") if _rt_rb else ""
        if _rb_sid:
            from app.services.gis_harness.representation_binding import (
                requirement_to_mapspec as _rb,
            )
            _rb_report = await _rb.apply_pending_bindings_safe(_rb_sid)
            if _rb_report:
                out["representation_bindings"] = {
                    key: value for key, value in _rb_report.items()
                    if value not in ((), [], {}, None)
                }
    except Exception as _rb_exc:  # noqa: BLE001 — 绑定面绝不阻断 intent
        logger.debug("[representation_binding] intent seam skipped: %s", _rb_exc)
    return out



def register_map_intent(registry: ToolRegistry) -> None:
    tool(
        registry,
        # #993: tier 1 — the docstring below has always promised "意图解析廉价
        # 且恒可用"，and SYSTEM_PROMPT's 意图先行 contract requires it visible for
        # keyword-less requests like『看看成都的大学』(no statistics/report/
        # network/temporal keywords → tier-2 domain gating hid it). The declared
        # domains stay for the H-8 task-family coverage contract (proximity→
        # network / change_detection→temporal); with tier 1 they no longer gate
        # visibility. Keep webgis_map_product tier=2: it runs only after data
        # tools returned.
        tier=1, domains=["statistics", "report", "network", "temporal"], name="webgis_map_intent",
        capabilities=['thematic_cartography'],
        # #996: audit4 #979 给 result 形状加了 guidance 键（有界 capability→tool
        # 裁决投影）——RESULT 契约变更，contract_version 1→2（指纹 1.0#cv2）。
        # ADR-0181（capability graph v1）：plan.capability_evidence 键 +
        # guidance 能力资格摘要行 —— RESULT 契约再变更，2→3（1.0#cv3）。
        contract_version=3,
        description=(
            "GIS 制图意图解析器（确定性，无副作用）。输入用户请求，返回 typed "
            "MapRequestIntent（scope/subject/task/analysis_intents/cartography_intents/"
            "output_intents）、候选 CartographyRecipe 与推荐 recipe、MapProductPlan "
            "骨架（数据需求/分析步骤/图层角色/组件/输出，按能力面声明而非硬编码工具）。"
            "\n何时用：任何「做一张图/分布/密度/各区统计/周边/报告配图」类请求的"
            "第一步——先拿结构化意图与计划，再执行数据工具；数据回来后用 "
            "webgis_map_product 复检并组装产品。"
            "\n语义护栏：『每平方公里密度』是定量分析意图，不会被降级为视觉热力；"
            "『各区数量』首选行政聚合+choropleth 而非热力图。"
        ),
        args_model=MapIntentArgs,
        side_effect="deterministic_compute",
        deterministic=True,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=("意图解析", "制图计划", "recipe", "做图", "分布", "密度", "map intent"),
        output_semantic_type="text",
        result_size_policy="inline_small",
        summary=(
            "GIS 制图意图解析器（确定性、无副作用）：把『做一张图/分布/密度/各区"
            "统计/周边/报告配图』类请求解析为结构化 MapRequestIntent、候选制图 "
            "recipe 与 MapProductPlan 骨架（数据需求/分析步骤/图层角色/组件/输出）。"
            "任何成图类请求的第一步；数据工具执行完后再调 webgis_map_product "
            "组装产品。『每平方公里密度』不会被降级为视觉热力。"
        ),
        examples=(
            "看看成都的大学分布，做一张图",
            "统计各区小学数量，配上分级色带和图例",
        ),
        anti_examples=(
            "把地图缩放到北京——那是视角操作，用 webgis_view_set 或 fly_to_location",
            "直接生成分析报告文档——那是报告导出，用 generate_analysis_report",
        ),
    )(partial(webgis_map_intent, registry))
