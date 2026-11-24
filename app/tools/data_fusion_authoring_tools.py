"""多源实体融合的 Agent 工具面（Pi 经 extensions → dispatch 到此）。

工具语义（reviewer 重点）：
- 全部经 ``app.services.data_fusion_authoring.service`` 单一编排面 ——
  与 REST 同一权限/同一收据/同一幂等键；
- session_id 由 ToolRegistry 从已授权上下文注入（模型不能指定别的
  session）；计划/运行/决定归属注入 session，跨会话一律 not_found；
- 结果只带有界证据：预览/审阅分页样本 ≤50、结果页 ≤200、质量摘要
  计数；绝不把全表/全部候选塞进上下文；
- commit 是受权发布（require_resolved 门在 service 内强制）；Agent 只
  能发起，不能绕过审阅门或人工锁定；
- execute 返回 job 时用 status 工具轮询；有界等待 wait_max_s 内联收敛。
"""
import asyncio
from typing import Any, Dict, Optional

from app.services.data_fusion_authoring import service
from app.services.data_fusion_authoring.errors import DataFusionError
from app.tools.registry import ToolRegistry, tool

_MAX_PLAN_BLOB = 32 * 1024


def _fail(exc: DataFusionError) -> Dict[str, Any]:
    out = exc.payload()
    out["error"] = out.get("error_code", exc.code)
    return out


def _actor(session_id: str) -> Dict[str, Any]:
    """工具平面 actor：JobOrigin 上下文 + 受信 session（注入即授权）。"""
    from app.services.jobs.context import current_origin

    origin = current_origin()
    return {
        "user_id": origin.owner_id if origin else None,
        "org_id": origin.org_id if origin else None,
        "owner_token": origin.owner_token if origin else None,
        "trusted_session_id": session_id,
    }


def register_data_fusion_authoring_tools(registry: ToolRegistry) -> None:
    """注册多源实体融合工具族。"""

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="webgis_data_fusion_describe_sources",
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        tags=["实体融合", "来源资格", "字段", "CRS", "许可"],
        capabilities=["data_fusion_authoring"],
        latency_class="medium",
        memory_class="light",
        result_size_policy="bounded",
        output_semantic_type="json",
        failure_modes=("invalid_args", "missing_data"),
        description=(
            "评估最多两个候选融合源（session ref / 项目产物修订）：行数、"
            "CRS 与可比性分类、几何形态、计划声明字段是否存在、时间字段"
            "可解析样本率、许可/署名声明。创建融合计划前先用它确认两源"
            "资格；只接受当前会话有权访问的源，绝不自动联网抓取。"
        ),
        param_descriptions={
            "sources": "候选源数组（1-2 项）[{side:'a'|'b', kind, ref, name_field, external_id_field, address_field, category_field, temporal_field, id_space, ...}]",
        },
    )
    async def webgis_data_fusion_describe_sources(
        sources: list,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not session_id:
            return {"error": "session_required", "message": "缺少 session_id"}
        try:
            return await service.describe_sources(
                session_id, sources, _actor(session_id))
        except DataFusionError as exc:
            return _fail(exc)

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="webgis_data_fusion_plan_upsert",
        side_effect="state_mutation",
        network=False,
        deterministic=True,
        tags=["实体融合", "计划", "draft"],
        capabilities=["data_fusion_authoring"],
        latency_class="fast",
        memory_class="light",
        result_size_policy="bounded",
        output_semantic_type="json",
        failure_modes=("invalid_args", "superseded", "validation_error"),
        description=(
            "创建或更新融合计划草稿（不执行、不改源数据、不改地图）。计划覆盖："
            "实体种类（point_poi/area_link/line_link）、两源锚与字段声明"
            "（名称/可选 id+共享 id_space/地址/类别词表映射/时间字段）、输出"
            "字段映射与逐字段裁决规则、证据权重（覆盖 5 类证据且和为 1）、"
            "阈值与歧义边际、距离窗口、几何采纳政策、提交政策。返回 "
            "plan_id/revision/digest。plan_id 空 = 新建；否则按 "
            "expected_revision CAS 更新。"
        ),
        param_descriptions={
            "plan": "FusionPlan 计划体（schema_version=1）",
            "plan_id": "已有计划 id（更新时必填）",
            "expected_revision": "更新时的 CAS 修订号（新建忽略）",
        },
    )
    async def webgis_data_fusion_plan_upsert(
        plan: Dict[str, Any],
        plan_id: Optional[str] = None,
        expected_revision: Optional[int] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not session_id:
            return {"error": "session_required", "message": "缺少 session_id"}
        import json as _json

        if len(_json.dumps(plan, ensure_ascii=False).encode("utf-8")) > _MAX_PLAN_BLOB:
            return {"error": "invalid_args",
                    "message": f"计划超过 {_MAX_PLAN_BLOB} B 上限"}
        try:
            if plan_id:
                if expected_revision is None:
                    return {
                        "error": "invalid_args",
                        "message": "更新计划必须携带 expected_revision",
                    }
                view = await service.update_plan(
                    session_id, plan_id, plan,
                    expected_revision=int(expected_revision),
                    actor=_actor(session_id))
            else:
                view = await service.create_plan(
                    session_id, plan, actor=_actor(session_id))
        except DataFusionError as exc:
            return _fail(exc)
        return {
            "plan_id": view["plan_id"],
            "revision": view["revision"],
            "normalized_digest": view["normalized_digest"],
            "status": view["status"],
            "title": view["title"],
        }

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="webgis_data_fusion_preview",
        side_effect="deterministic_compute",
        network=False,
        deterministic=True,
        tags=["实体融合", "预览", "冲突比例", "样本"],
        capabilities=["data_fusion_authoring"],
        latency_class="slow",
        memory_class="medium",
        result_size_policy="bounded",
        output_semantic_type="json",
        failure_modes=("invalid_args", "validation_error", "missing_data",
                       "budget_exceeded", "crs_unresolvable"),
        description=(
            "融合预览（只读、不建成果、不偷偷合并）：双源资格卡、覆盖对比、"
            "真实匹配运行的有界投影（自动匹配/审阅/歧义/硬阻断/未配对计数）、"
            "代表性样本对（≤20）与预算。预览后计划或源变化即失效（digest 锚）。"
        ),
        param_descriptions={
            "plan_id": "融合计划 id",
            "expected_revision": "计划 CAS 修订号",
        },
    )
    async def webgis_data_fusion_preview(
        plan_id: str,
        expected_revision: int,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not session_id:
            return {"error": "session_required", "message": "缺少 session_id"}
        try:
            out = await service.preview_plan(
                session_id, plan_id, expected_revision=int(expected_revision),
                actor=_actor(session_id))
        except DataFusionError as exc:
            return _fail(exc)
        # 有界投影：样本已 ≤20/类；预览缓存体不回传全文（digest + 统计即可）
        return {
            "cached": out.get("cached", False),
            "match_stats": out.get("match_stats"),
            "sources": [
                {k: s.get(k) for k in ("side", "state", "row_count", "crs",
                                       "crs_class", "geometry",
                                       "license_declared")}
                for s in (out.get("sources") or [])
            ],
            "samples": out.get("samples"),
            "warnings": out.get("warnings"),
            "budgets": out.get("budgets"),
        }

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="webgis_data_fusion_execute",
        side_effect="state_mutation",
        network=False,
        deterministic=True,
        tags=["实体融合", "执行", "匹配"],
        capabilities=["data_fusion_authoring"],
        latency_class="slow",
        memory_class="medium",
        result_size_policy="bounded",
        output_semantic_type="json",
        failure_modes=("invalid_args", "stale_source", "budget_exceeded",
                       "crs_unresolvable", "entity_kind_unsupported"),
        description=(
            "执行融合匹配（durable job；幂等 —— 同计划+同源内容复用收据）："
            "源冻结复核 → blocking 匹配 → 冲突/歧义检测 → 匹配包冻结。"
            "返回 run_id/job_id；用 webgis_data_fusion_status 轮询，审阅后 "
            "webgis_data_fusion_commit 发布不可变融合版本（commit 前不产生"
            "成果）。"
        ),
        param_descriptions={
            "plan_id": "融合计划 id",
            "expected_revision": "计划 CAS 修订号",
            "wait_max_s": "有界内联等待秒数（0 = 立即返回 job）",
        },
    )
    async def webgis_data_fusion_execute(
        plan_id: str,
        expected_revision: int,
        wait_max_s: float = 0.0,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not session_id:
            return {"error": "session_required", "message": "缺少 session_id"}
        try:
            view = await service.execute_plan(
                session_id, plan_id, expected_revision=int(expected_revision),
                actor=_actor(session_id), background=True)
        except DataFusionError as exc:
            return _fail(exc)
        wait_left = max(0.0, min(float(wait_max_s), 120.0))
        while view["status"] in ("pending", "running") and wait_left > 0:
            await asyncio.sleep(min(2.0, wait_left))
            wait_left -= 2.0
            view = await service.inspect_run(session_id, view["run_id"])
        out = {
            "run_id": view["run_id"],
            "plan_id": view["plan_id"],
            "status": view["status"],
            "receipt": view.get("receipt"),
            "error_code": view.get("error_code"),
        }
        if view["status"] in ("pending", "running"):
            job_id = view.get("job_id")
            out["job_id"] = job_id
            out["poll"] = f"/api/v1/tasks/jobs/{job_id}" if job_id else None
            out["note"] = "仍在执行：用 webgis_data_fusion_status 轮询"
        elif view["status"] == "completed":
            out["next"] = "webgis_data_fusion_review_queue 审阅 → commit"
        return out

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="webgis_data_fusion_status",
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        tags=["实体融合", "状态", "job"],
        capabilities=["data_fusion_authoring"],
        latency_class="fast",
        memory_class="light",
        result_size_policy="bounded",
        output_semantic_type="json",
        failure_modes=("run_not_found",),
        description=(
            "查询融合运行状态与有界收据（匹配计数、审阅队列摘要、源指纹、"
            "commit 状态）。终态区分 completed/failed/cancelled 与错误码。"
        ),
        param_descriptions={"run_id": "运行 id"},
    )
    async def webgis_data_fusion_status(
        run_id: str,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not session_id:
            return {"error": "session_required", "message": "缺少 session_id"}
        try:
            return await service.inspect_run(session_id, run_id)
        except DataFusionError as exc:
            return _fail(exc)

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="webgis_data_fusion_review_queue",
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        tags=["实体融合", "审阅", "冲突", "歧义"],
        capabilities=["data_fusion_authoring"],
        latency_class="fast",
        memory_class="light",
        result_size_policy="bounded",
        output_semantic_type="json",
        failure_modes=("result_unavailable",),
        description=(
            "分页读取审阅队列：needs_review/ambiguous/含字段冲突的配对，"
            "每项带逐证据分项、字段冲突、几何证据与既有决定。逐项用 "
            "webgis_data_fusion_decide 提交决定（accept/reject + 可选人工"
            "字段值/几何侧）；决定按内容指纹键持久，刷新自动继承。"
        ),
        param_descriptions={
            "run_id": "运行 id",
            "cursor": "分页游标（0 起）",
            "limit": "单页上限（≤200）",
            "status": "可选状态过滤（needs_review|ambiguous）",
        },
    )
    async def webgis_data_fusion_review_queue(
        run_id: str,
        cursor: int = 0,
        limit: int = 50,
        status: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not session_id:
            return {"error": "session_required", "message": "缺少 session_id"}
        try:
            return service.review_queue(
                session_id, run_id, cursor=int(cursor),
                limit=min(int(limit), 200), status_filter=status)
        except DataFusionError as exc:
            return _fail(exc)

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="webgis_data_fusion_decide",
        side_effect="state_mutation",
        network=False,
        deterministic=True,
        tags=["实体融合", "审阅决定", "锁定"],
        capabilities=["data_fusion_authoring"],
        latency_class="fast",
        memory_class="light",
        result_size_policy="bounded",
        output_semantic_type="json",
        failure_modes=("stale_review_revision", "decision_not_found",
                       "invalid_args"),
        description=(
            "提交一条审阅决定（CAS；审阅冲突 typed 拒绝不覆盖他人）："
            "accept = 确认合并（可附人工字段值/几何侧解决 unresolved）；"
            "reject = 确认保持分开。决定即人工锁定 —— 来源刷新不会无声"
            "推翻；显式删除决定才解除。"
        ),
        param_descriptions={
            "plan_id": "融合计划 id",
            "pair_key": "审阅项的 pair_key（审阅队列返回）",
            "action": "accept | reject",
            "expected_revision": "决定 CAS 修订号（新建 0）",
            "field_values": "可选 {output_field: 人工确认值}",
            "geometry_side": "可选几何采纳侧 a|b",
            "note": "可选审阅备注（≤500 字）",
        },
    )
    async def webgis_data_fusion_decide(
        plan_id: str,
        pair_key: str,
        action: str,
        expected_revision: int = 0,
        field_values: Optional[Dict[str, Any]] = None,
        geometry_side: Optional[str] = None,
        note: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not session_id:
            return {"error": "session_required", "message": "缺少 session_id"}
        try:
            return await service.upsert_decision(
                session_id, plan_id, pair_key=pair_key, action=action,
                field_values=field_values, geometry_side=geometry_side,
                reviewer="pi-agent", note=note,
                expected_revision=int(expected_revision))
        except DataFusionError as exc:
            return _fail(exc)

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="webgis_data_fusion_commit",
        side_effect="state_mutation",
        network=False,
        deterministic=True,
        tags=["实体融合", "提交", "版本", "质量报告"],
        capabilities=["data_fusion_authoring"],
        latency_class="slow",
        memory_class="medium",
        result_size_policy="bounded",
        output_semantic_type="json",
        failure_modes=("commit_blocked", "stale_source", "receipt_missing",
                       "budget_exceeded"),
        description=(
            "受权提交：匹配结果 + 已授权决定 → 不可变融合数据集版本"
            "（ref:data-fusion/<run>；附许可/署名/冲突/未知披露与质量报告）。"
            "require_resolved 政策下存在未决审阅项则 typed 阻断。产出可被"
            "既有统计/热力/聚合/交付链直接消费。"
        ),
        param_descriptions={"run_id": "已完成匹配的运行 id"},
    )
    async def webgis_data_fusion_commit(
        run_id: str,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not session_id:
            return {"error": "session_required", "message": "缺少 session_id"}
        try:
            view = await service.commit_run(
                session_id, run_id, actor=_actor(session_id))
        except DataFusionError as exc:
            return _fail(exc)
        cr = view.get("commit_receipt") or {}
        return {
            "run_id": view["run_id"],
            "output_ref": view.get("output_ref"),
            "output_features": view.get("output_features"),
            "output_sha256": view.get("output_sha256"),
            "counts": cr.get("counts"),
            "unresolved_field_count": cr.get("unresolved_field_count"),
            "data_object": cr.get("data_object"),
            "note": "用 webgis_data_fusion_result 分页读取；render-ref 可入图",
        }

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="webgis_data_fusion_result",
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        tags=["实体融合", "结果", "分页"],
        capabilities=["data_fusion_authoring"],
        latency_class="fast",
        memory_class="light",
        result_size_policy="bounded",
        output_semantic_type="json",
        failure_modes=("result_unavailable",),
        description=(
            "分页读取已提交的融合数据集要素（稳定序；每要素含 _fusion "
            "provenance：来源/字段裁决/匹配证据/几何采纳）。仅 commit 后可用。"
        ),
        param_descriptions={
            "run_id": "运行 id",
            "cursor": "分页游标",
            "limit": "单页上限（≤200）",
        },
    )
    async def webgis_data_fusion_result(
        run_id: str,
        cursor: int = 0,
        limit: int = 50,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not session_id:
            return {"error": "session_required", "message": "缺少 session_id"}
        try:
            return await service.run_result_page(
                session_id, run_id, cursor=int(cursor),
                limit=min(int(limit), 200))
        except DataFusionError as exc:
            return _fail(exc)

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="webgis_data_fusion_refresh",
        side_effect="state_mutation",
        network=False,
        deterministic=True,
        tags=["实体融合", "刷新", "影响面", "锁定"],
        capabilities=["data_fusion_authoring"],
        latency_class="slow",
        memory_class="medium",
        result_size_policy="bounded",
        output_semantic_type="json",
        failure_modes=("refresh_not_safe", "run_not_found", "receipt_missing"),
        description=(
            "来源刷新（增量维护）：与最近完成 run 比较实体内容指纹，给出"
            "added/removed/影响面占比与决定三态预测（inherited/stale/orphan"
            "—— 人工锁定绝不无声推翻）。dry_run=true 只报告；安全时受控"
            "重跑（新 run 链回基底，决定自动继承，旧版本可追溯）。变更面超"
            "安全阈 typed 要求全量重算。"
        ),
        param_descriptions={
            "plan_id": "融合计划 id",
            "expected_revision": "计划 CAS 修订号",
            "dry_run": "true = 只给影响面报告，不建 run",
        },
    )
    async def webgis_data_fusion_refresh(
        plan_id: str,
        expected_revision: int,
        dry_run: bool = True,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not session_id:
            return {"error": "session_required", "message": "缺少 session_id"}
        try:
            return await service.refresh_plan(
                session_id, plan_id, expected_revision=int(expected_revision),
                actor=_actor(session_id), dry_run=bool(dry_run))
        except DataFusionError as exc:
            return _fail(exc)
