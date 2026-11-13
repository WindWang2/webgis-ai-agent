"""Agent 拓扑工作台工具面（G31，WP11）：ruleset / run / findings / repair preview。

信任边界：
- ruleset 配置是会话内可逆配置写（state_mutation of config，不动数据）；
- run 启动走 durable job（analysis）；状态/问题/详情纯读；
- 例外（accepted exception）是**用户决定** —— 工具面不提供接受例外操作
  （REST/UI 面；无状态权限的模型输出不能充当用户例外决定，任务书 WP09）；
- repair preview 复用 G14 预览链（不写数据；提交走既有确认链）；
- tier ≤2；回执有界（绝不内联几何载荷/全量 findings）。

API 与工具共用 app.services.topology_workbench.service（单一编排面）。
"""


import logging
from typing import Any, Dict, List, Optional

from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)

_MAX_RULES = 32
_MAX_FINDINGS_PAGE = 50


def _caller() -> tuple[Optional[str], Optional[int], Optional[str]]:
    """(user_id, org_id, session_id) from tool execution context."""
    from app.tools.project_tools import _caller_identity

    return _caller_identity()


def _fail(code: str, message: str, **extra: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {"success": False, "code": code.upper(), "message": message[:400]}
    if extra:
        out.update(extra)
    return out


def register_topology_workbench_tools(registry: ToolRegistry) -> None:
    """注册拓扑工作台工具族。"""

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="topology_ruleset_configure",
        side_effect="state_mutation",
        data_mutations=["session_state"],
        network=False,
        deterministic=False,
        tags=["拓扑", "topology", "规则集", "数据质量", "quality"],
        capabilities=["dataset_ingest"],
        latency_class="medium",
        memory_class="light",
        description=(
            "创建/更新跨图层拓扑规则集（多数据集业务约束的作者面）。规则类型词表："
            "polygons_no_overlap(同源面禁重叠) / area_coverage(AOI 覆盖+排除区) / "
            "objects_covered_by_target(对象须被目标面覆盖,边界政策 covered|within) / "
            "cross_layer_no_overlap(跨层禁重叠) / "
            "line_endpoints_connected(线端点连接设施, tolerance_m 必填)。"
            "容差分离建模：tolerance_m(线性) 与 *_threshold_m2(面积) 不可互换。"
            "更新已有规则集时内容变化生成新版本（历史运行仍持旧 digest 可读）。"
            "绑定在 run 启动时提供（角色名见各规则类型）。"
        ),
        param_descriptions={
            "name": "规则集名称（1..200 字符）",
            "rules": (
                "规则数组（≤32），如 [{'rule_id':'cov1','rule_type':'area_coverage',"
                "'severity':'error','params':{'gap_area_threshold_m2':5.0}}]"
            ),
            "ruleset_id": "更新目标规则集 id（缺省=创建新规则集）",
            "project_id": "项目归属（可选）",
            "session_id": "会话归属（可选；运行时注入）",
        },
    )
    async def webgis_topology_ruleset_configure(
        name: str,
        rules: List[Dict[str, Any]],
        ruleset_id: Optional[str] = None,
        project_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        user_id, org_id, ctx_session = _caller()
        if not user_id and not (session_id or ctx_session):
            return _fail("MISSING_IDENTITY", "工具上下文缺少用户身份与会话（无法归属规则集）")
        from app.services.topology_workbench.service import (
            WorkbenchError,
            create_ruleset,
            update_ruleset,
        )
        try:
            anchor = session_id or ctx_session
            if ruleset_id:
                view = update_ruleset(
                    ruleset_id, name=name, rules=rules, actor_user_id=user_id,
                    anchor_session_id=session_id or ctx_session,
                )
            else:
                view = create_ruleset(
                    name=name, rules=rules, project_id=project_id,
                    session_id=anchor,
                    created_by=user_id, org_id=org_id,
                )
            return {
                "success": True,
                "ruleset_id": view["id"], "name": view["name"],
                "current_version": view["current_version"], "digest": view["digest"],
                "rule_count": len(view["rules"]),
                "message": (
                    f"规则集已{'更新至版本 ' + str(view['current_version']) if ruleset_id else '创建（v1）'}；"
                    "用 topology_check_run 绑定数据源并启动检查"
                ),
            }
        except WorkbenchError as exc:
            return _fail(exc.code, exc.message)

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="topology_check_run",
        side_effect="state_mutation",
        data_mutations=["session_state"],
        network=False,
        deterministic=False,
        tags=["拓扑", "topology", "检查", "durable job"],
        capabilities=["dataset_ingest"],
        latency_class="medium",
        memory_class="medium",
        description=(
            "启动跨图层拓扑检查（durable job）：绑定源角色 → 权威快照解析（会话 ref / "
            "项目产物修订；选择句柄在提交时解析为可重放 scope）→ 逐规则求值 → 问题落库。"
            "complete 角色（coverage/aoi/exclusions/target/connectors/exit_zones）必须"
            "全量绑定，携带 scope/选择句柄会被拒绝（避免虚假缺口）；checked/source_a/"
            "source_b 可带 scope。运行完成前返回 job/run id；结果经 "
            "topology_check_status 查询（异步 job，提交后立即轮询状态）。"
        ),
        param_descriptions={
            "ruleset_id": "topology_ruleset_configure 返回的规则集 id",
            "bindings": (
                "角色名 → 绑定。如 {'coverage': {'kind':'session_ref','ref':'ref:x-…',"
                "'session_id':'…'}, 'aoi': {'kind':'bbox','bbox':[w,s,e,n]}}；"
                "checked 类角色可加 'scope': {'kind':'where','where':{…}} 或 "
                "'selection_handle': 'sh-…'（G24 句柄，提交时解析）"
            ),
            "session_id": "会话 id（运行时注入，无需传）",
            "project_id": "项目 id（可选）",
        },
    )
    async def webgis_topology_check_run(
        ruleset_id: str,
        bindings: Dict[str, Dict[str, Any]],
        session_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        user_id, org_id, ctx_session = _caller()
        anchor = session_id or ctx_session
        if not user_id and not anchor:
            return _fail("MISSING_IDENTITY", "工具上下文缺少用户身份与会话")
        if not isinstance(bindings, dict) or not bindings:
            return _fail("INVALID_ARGS", "bindings 必须是非空对象（角色名 → 绑定）")
        if anchor:
            bindings = await _rebind_inline_payloads(bindings, str(anchor))
        from app.services.topology_workbench.service import WorkbenchError, submit_run
        try:
            result = await submit_run(
                ruleset_id=ruleset_id, bindings=bindings,
                actor_user_id=user_id, actor_owner_token=None,
                org_id=org_id, project_id=project_id,
                session_id=anchor, anchor_session_id=anchor,
            )
            job = result.get("job") or {}
            return {
                "success": True,
                "run_id": result["run_id"],
                "job_id": job.get("job_id"),
                "job_status": job.get("status"),
                "message": (
                    "拓扑检查已启动（durable job）；用 topology_check_status 查询进度与结论"
                ),
            }
        except WorkbenchError as exc:
            return _fail(exc.code, exc.message, correction_hint=_correction_hint(exc.code))

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="topology_check_status",
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        tags=["拓扑", "topology", "状态"],
        capabilities=["dataset_ingest"],
        latency_class="fast",
        memory_class="light",
        description=(
            "查询拓扑检查运行状态：作业层状态 + 逐规则维度分离结论"
            "（applicability / input_completeness / computation / outcome）+ 计数。"
            "注意：全部规则 blocked/unknown 时整体不是'通过' —— 诚实呈现未知范围。"
        ),
        param_descriptions={"run_id": "topology_check_run 返回的 run_id"},
    )
    async def webgis_topology_check_status(run_id: str) -> Dict[str, Any]:
        user_id, _org, ctx_session = _caller()
        from app.services.topology_workbench.service import WorkbenchError, get_run
        try:
            run = get_run(run_id, actor_user_id=user_id, anchor_session_id=ctx_session)
        except WorkbenchError as exc:
            return _fail(exc.code, exc.message)
        rule_lines = []
        for rr in run.get("rule_results") or []:
            rule_lines.append(
                f"{rr.get('rule_id')}({rr.get('rule_type')}): {rr.get('applicability')}/"
                f"{rr.get('outcome')}/计算{rr.get('completeness', rr.get('computation'))}"
                f"/输入{rr.get('input_completeness')}"
                + (f" —— {rr.get('blocked_message')}" if rr.get("blocked_code") else "")
            )
        comp = run.get("completeness") or {}
        return {
            "success": True,
            "run_id": run_id,
            "status": run["status"],
            "findings_total": run["findings_total"],
            "findings_open": run["findings_open"],
            "computation_crs": run.get("computation_crs"),
            "all_skipped_or_unknown": comp.get("all_skipped_or_unknown"),
            "budget_truncated": comp.get("budget_truncated"),
            "failure": {"code": comp.get("failure_code"), "message": comp.get("failure_message")}
            if comp.get("failure_code") else None,
            "rule_results": rule_lines[:16],
            "message": (
                f"运行 {run['status']}：{run['findings_total']} 条问题"
                f"（{run['findings_open']} 条待处理）"
                + ("；注意存在未知/跳过范围，不能按全部通过解读"
                   if comp.get("all_skipped_or_unknown") else "")
            ),
        }

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="topology_findings_list",
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        tags=["拓扑", "topology", "问题", "findings"],
        capabilities=["dataset_ingest"],
        latency_class="fast",
        memory_class="light",
        description=(
            "分页列出运行的问题（筛选 rule_id/severity/status）。total 是全量计数"
            "（非当前页）。每条含 finding_id、度量值/阈值/单位、bbox、状态"
            "（open/accepted_exception/resolved）。用 topology_finding_detail 看定位与证据。"
        ),
        param_descriptions={
            "run_id": "运行 id",
            "rule_id": "按规则筛选（可选）",
            "severity": "info|warn|error（可选）",
            "status": "open|accepted_exception|resolved|dismissed（可选）",
            "limit": "页大小（默认 20，≤50）",
            "offset": "偏移",
        },
    )
    async def webgis_topology_findings_list(
        run_id: str,
        rule_id: Optional[str] = None,
        severity: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Dict[str, Any]:
        user_id, _org, _sess = _caller()
        limit = max(1, min(int(limit or 20), _MAX_FINDINGS_PAGE))
        offset = max(0, int(offset or 0))
        from app.services.topology_workbench.service import WorkbenchError, list_findings
        try:
            items, total, counts = list_findings(
                run_id, rule_id=rule_id, severity=severity, status=status,
                actor_user_id=user_id, limit=limit, offset=offset,
            )
        except WorkbenchError as exc:
            return _fail(exc.code, exc.message)
        return {
            "success": True,
            "run_id": run_id,
            "total": total,
            "returned": len(items),
            "offset": offset,
            "has_more": (offset + len(items)) < total,
            "status_counts": counts,
            "count_semantics": "total 为筛选下的全量计数（非当前页数量）",
            "findings": [
                {
                    "finding_id": it["id"], "rule_id": it["rule_id"],
                    "severity": it["severity"], "status": it["status"],
                    "metric": it["metric"], "message": it["message"][:200],
                    "bbox": it["bbox"],
                }
                for it in items
            ],
        }

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="topology_finding_detail",
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        tags=["拓扑", "topology", "定位", "witness"],
        capabilities=["dataset_ingest"],
        latency_class="fast",
        memory_class="light",
        description=(
            "问题详情：locator（源/版本/要素身份；含 stale 判定）、度量、witness "
            "GeoJSON（问题几何证据）。locator 的 snapshot_index 身份只在该快照指纹下"
            "有效（stale=true 表示源已变化，定位过期需重检）。"
        ),
        param_descriptions={"finding_id": "问题 id（来自 topology_findings_list）"},
    )
    async def webgis_topology_finding_detail(finding_id: str) -> Dict[str, Any]:
        user_id, _org, ctx_session = _caller()
        from app.services.topology_workbench.service import (
            WorkbenchError,
            get_finding,
            get_finding_witness,
        )
        try:
            finding = get_finding(
                finding_id, actor_user_id=user_id, anchor_session_id=ctx_session)
        except WorkbenchError as exc:
            return _fail(exc.code, exc.message)
        witness: Dict[str, Any] = {}
        stale = False
        try:
            wit = get_finding_witness(
                finding_id, actor_user_id=user_id, anchor_session_id=ctx_session)
            witness = {
                "geometry": wit["witness"].get("geometry"),
                "witness_digest": wit.get("witness_digest"),
            }
            stale = bool(wit.get("stale"))
        except WorkbenchError as exc:
            witness = {"unavailable": exc.message[:200]}
        locators = finding.get("locators") or []
        return {
            "success": True,
            "finding_id": finding_id,
            "rule_id": finding["rule_id"], "rule_type": finding["rule_type"],
            "severity": finding["severity"], "status": finding["status"],
            "metric": finding["metric"], "message": finding["message"],
            "locators_stale": stale,
            "locators": [
                {
                    "role": loc.get("role"), "kind": loc.get("kind"),
                    "ref": loc.get("ref"),
                    "content_revision": loc.get("content_revision"),
                    "identity": loc.get("identity"),
                }
                for loc in locators[:8]
            ],
            "witness": witness,
            "exception_note": (
                "例外是用户决定：在项目质量工作台（REST /findings/{id}/exception）"
                "由用户接受；如需建议例外请向用户说明理由并请其确认"
            ) if finding["status"] == "open" else None,
        }

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="topology_repair_preview",
        side_effect="state_mutation",
        data_mutations=["session_state"],
        network=False,
        deterministic=False,
        tags=["拓扑", "topology", "修复", "repair", "G14"],
        capabilities=["dataset_ingest"],
        latency_class="medium",
        memory_class="medium",
        description=(
            "为问题生成候选修复并（kind 支持 snap_endpoint / trim_to_target 时）构建 "
            "G14 预览 plan：列出目标要素身份、几何变化、量变预估。绝不直接写数据 —— "
            "提交必须经用户在编辑提案卡确认（vector_feature_edit_commit 同链，CAS/"
            "journal/undo 继承）。归属选择类修复（fill_gap/resolve_overlap）需要用户"
            "先指定保留方；不支持的操作返回 unsupported 与人工编辑路径。修复后用 "
            "REST /runs/{id}/recheck（或让用户在工作台复验）验证证据确实消失。"
        ),
        param_descriptions={
            "finding_id": "问题 id",
            "kind": "snap_endpoint | trim_to_target | fill_gap | resolve_overlap",
            "session_id": "会话 id（G14 编辑在会话源上执行；运行时注入）",
            "ownership_choice": "归属选择（fill_gap/resolve_overlap 必填）: {'keeper_locator': {...}}",
        },
    )
    async def webgis_topology_repair_preview(
        finding_id: str,
        kind: str,
        session_id: Optional[str] = None,
        ownership_choice: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        user_id, _org, ctx_session = _caller()
        if not user_id and not ctx_session:
            return _fail("MISSING_IDENTITY", "工具上下文缺少用户身份与会话")
        from app.services.topology_workbench.repair import (
            WorkbenchError,
            build_g14_preview,
            build_repair_candidates,
        )
        try:
            candidates = build_repair_candidates(finding_id, actor_user_id=user_id)
        except WorkbenchError as exc:
            return _fail(exc.code, exc.message)
        if not candidates.get("candidates"):
            return _fail(
                "REPAIR_UNSUPPORTED",
                candidates.get("unsupported_reason") or "该问题没有可自动预览的修复候选",
                candidates=candidates["candidates"],
            )
        sid = session_id or ctx_session
        if not sid:
            return _fail(
                "MISSING_SESSION", "修复预览需要 session_id（编辑经 G14 在会话源上执行）"
            )
        try:
            result = await build_g14_preview(
                finding_id, session_id=str(sid), kind=kind,
                ownership_choice=ownership_choice, actor_user_id=user_id,
            )
        except WorkbenchError as exc:
            return _fail(exc.code, exc.message, correction_hint=_correction_hint(exc.code))
        return {
            "success": True,
            "finding_id": finding_id, "kind": kind,
            "committable": result["committable"],
            "plan": result["g14_plan"],
            "note": result["note"],
        }


async def _rebind_inline_payloads(
    bindings: Dict[str, Dict[str, Any]], session_id: str
) -> Dict[str, Dict[str, Any]]:
    """把被 dispatch 透明解引用的内联载荷（ref 为 dict）重新物化为会话 ref。

    registry.dispatch 会把值为 ``ref:…`` 字符串的参数解引用为其载荷（调用
    方拿到的 ref 已是 dict）。绑定层的 ref 必须是可复核的字符串 —— 这里
    把载荷存回会话（新 ref），内容指纹保持稳定（input_digest 按内容计算，
    跨运行的复用语义不受 ref 字符串变化影响）。
    """
    from app.services.session_data import session_data_manager

    out: Dict[str, Dict[str, Any]] = {}
    for role, binding in bindings.items():
        if not isinstance(binding, dict):
            continue
        ref = binding.get("ref")
        if isinstance(ref, dict) and binding.get("kind") == "session_ref":
            new_ref = await session_data_manager.store(
                session_id, ref, prefix="twbind")
            binding = {**binding, "ref": new_ref, "session_id": session_id}
        out[role] = binding
    return out


def _correction_hint(code: str) -> str:
    return {
        "scope_not_allowed": "complete 角色（coverage/aoi/target/connectors…）必须全量绑定；把检查对象放到 checked 角色或改绑完整数据源",
        "source_unresolvable": "检查会话 ref 是否过期（可先 list refs）或项目产物是否有可物化修订",
        "source_changed_after_submission": "源在提交后被编辑 —— 重新提交运行即可（新输入身份）",
        "budget_exceeded": "快照要素数超上限 —— 缩小范围（checked 角色 scope）或分批检查",
        "region_unsupported": "分析范围跨反经线/极区/过大 —— 当前平面度量不可靠，请缩小范围",
        "repair_unsupported": "该修复需要归属选择或人工编辑 —— 向用户呈现候选并请其决定",
        "invalid_locator": "目标要素无稳定身份或已消失 —— 先物化 _wfid（一次编辑）或重跑检查",
    }.get(code, "")


__all__ = ["register_topology_workbench_tools"]
