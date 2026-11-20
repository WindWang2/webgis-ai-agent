"""G10 项目数据刷新 · Pi 自然语言工具面（沿既有 ToolRegistry → ToolDispatchService 链）。

链：Pi → 动态工具发现/资格 → ToolRegistry → ToolDispatchService →
ProjectDataRefreshService（领域服务）→ 类型化回执 → SSE/前端投影 → 真实观察。

红线：
- 不另设裸 HTTP 执行器 / eval / shell / 不受权限保护的后门；
- owner **不从模型参数取**：由 ``session_id`` 反查会话所有权 + 项目鉴权
  （服务端权威）；``RefreshToolArgs`` 显式 ``extra=forbid``，模型传来的
  ``verified`` / ``approved`` / ``owner_id`` 一律被拒；
- 大 FeatureCollection / 大表 / 全量日志不进上下文：只回有界摘要，
  带关键数值、证据级别、完整/截断状态与警告；
- 工具执行 / 任务执行 / 地图观察 / 版本采纳四态分别披露，
  不把 stub 工具返回值冒充真实 GIS 执行，不伪造 renderer ACK。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)

MAX_SUMMARY_ITEMS = 8

#: 可注入的 service factory（默认生产实现；本地合成数据测试沿同一条
#: ToolRegistry → ToolDispatchService → 领域服务链，不替换 dispatch 本身）。
_SERVICE_FACTORY = None


def set_refresh_service_factory(factory) -> None:
    global _SERVICE_FACTORY
    _SERVICE_FACTORY = factory


def _service():
    from app.services.project_runtime.refresh.service import ProjectDataRefreshService

    return (_SERVICE_FACTORY or ProjectDataRefreshService)()


class RefreshToolArgs(BaseModel):
    """所有刷新工具共用的严格参数模型（future annotations 下显式 args_model）。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(..., min_length=1, max_length=64,
                            description="项目 ID（必须属于当前会话 owner）")
    proposal_id: Optional[str] = Field(
        None, max_length=96,
        description="预览提案 ID（执行/采纳/放弃/回退/状态/解释时必填）")
    style_only: bool = Field(
        False, description="仅呈现层变化（不触发 GIS 数值重算）")
    cancel: bool = Field(False, description="取消候选执行")


async def _authorized_context(db: Any, project_id: str, session_id: str) -> str:
    """服务端权威 owner：会话所有权 → 项目鉴权（跨 owner 一律拒绝）。

    绝不读取模型传入的 owner_id / approved / verified（args_model 已 forbid）。
    """
    from app.services.project_service import ProjectService

    def _resolve() -> str:
        from app.models.db_model import Conversation

        row = db.query(Conversation).filter(
            Conversation.id == session_id).first()
        user_id = str(getattr(row, "user_id", "") or "") if row is not None else ""
        if not user_id:
            raise PermissionError("session owner not resolvable")
        project = ProjectService.get_project_with_auth(
            db=db, project_id=project_id, user_id=user_id, org_id=None)
        if not project:
            raise PermissionError("project not accessible")
        return user_id

    return await asyncio.to_thread(_resolve)


def _error(exc: Exception) -> Dict[str, Any]:
    from app.services.project_runtime.refresh.service import RefreshError

    if isinstance(exc, RefreshError):
        return {"success": False, "error": exc.code, "detail": exc.detail,
                "tool_execution": "failed", "task_execution": "not_run",
                "map_observation": "unverified", "version_adoption": "unchanged"}
    if isinstance(exc, PermissionError):
        return {"success": False, "error": "forbidden", "detail": str(exc)[:160],
                "tool_execution": "failed", "task_execution": "not_run",
                "map_observation": "unverified", "version_adoption": "unchanged"}
    logger.warning("[G10Tool] failed", exc_info=True)
    return {"success": False, "error": "refresh_failed",
            "detail": str(exc)[:200], "tool_execution": "failed",
            "task_execution": "not_run", "map_observation": "unverified",
            "version_adoption": "unchanged"}


def _bounded(items: list, limit: int = MAX_SUMMARY_ITEMS) -> tuple[list, bool]:
    truncated = len(items) > limit
    return items[:limit], truncated


def _receipt(tool_ok: bool = True, task: str = "ok",
             adoption: str = "unchanged") -> Dict[str, str]:
    return {
        "tool_execution": "ok" if tool_ok else "failed",
        "task_execution": task,
        "map_observation": "unverified",   # 工具成功 ≠ 地图已正确显示
        "version_adoption": adoption,
    }


def _diff_summary(diffs: list) -> Dict[str, Any]:
    out = []
    for d in diffs[:MAX_SUMMARY_ITEMS]:
        out.append({
            "logical_id": d.logical_id,
            "change_class": d.change_class,
            "evidence": d.evidence.value,
            "alignment": d.alignment,
            "counts": (d.exact_counts.model_dump() if d.exact_counts else None),
            "warnings": d.warnings[:2],
        })
    return {"items": out, "truncated": len(diffs) > MAX_SUMMARY_ITEMS,
            "total": len(diffs)}


def register_project_data_refresh_tools(registry: ToolRegistry) -> None:
    """注册 8 个刷新语义工具（一个模块，独立防并发冲突）。"""
    from app.core.database import SessionLocal

    def _need(proposal_id: Optional[str]) -> str:
        if not proposal_id:
            raise ValueError("proposal_id is required for this action")
        return str(proposal_id)

    @tool(
        registry, name="webgis_check_project_source_updates",
        tier=2, domains=["project", "dataset"],
        capabilities=["project_source_version_inventory"],
        args_model=RefreshToolArgs,
        description=(
            "检查项目来源是否有新版本：学校点 / 行政统计表 / 栅格数据更新后，"
            "列出稳定逻辑身份、内容版本、证据级别与 pinned/refreshable/missing 等状态。"
            "\n何时用：用户说『数据更新了』『检查更新』『哪些来源变了』。"
            "\n关键语义：默认手动检查，不自动拉付费数据、不自动覆盖项目；"
            "首次只建立基线（写入隔离刷新账本，不改 MapSpec/数据/版本），"
            "不声称数据变化；ETag/更新时间不等于精确内容版本。"
            "\n返回有界摘要 + stale 标记 + 完整/截断状态。"
        ),
        side_effect="artifact_creation", deterministic=False, latency_class="medium",
        memory_class="light", scale_class="small",
        tags=["检查更新", "数据版本", "来源清单", "project refresh"],
        output_semantic_type="text", result_size_policy="inline_small",
        failure_modes=["forbidden", "missing_session", "unknown_version"],
    )
    async def check_project_source_updates(
        project_id: str, proposal_id: Optional[str] = None,
        style_only: bool = False, cancel: bool = False,
        session_id: str = "",
    ) -> dict:
        from app.services.project_runtime.refresh.records import CheckReceipt

        if not session_id:
            return {"error": "Missing session_id context", **_receipt(False, "not_run")}
        try:
            with SessionLocal() as db:
                uid = await _authorized_context(db, project_id, session_id)
                receipt: CheckReceipt = await _service().check_updates(
                    db, project_id=project_id, session_id=session_id, user_id=uid)
            items, truncated = _bounded(receipt.inventory.items)
            return {
                "success": True, "project_id": project_id,
                "sources_total": receipt.sources_total,
                "changed": receipt.changed, "unchanged": receipt.unchanged,
                "unverifiable": receipt.unverifiable, "stale": receipt.stale,
                "sources": [i.model_dump(mode="json") for i in items],
                "truncated": truncated or receipt.truncated,
                "warnings": receipt.warnings[:6],
                **_receipt(),
            }
        except Exception as exc:  # noqa: BLE001 — 工具错误契约
            return _error(exc)

    @tool(
        registry, name="webgis_explain_stale_map_product",
        tier=2, domains=["project", "mapspec"],
        capabilities=["refresh_impact_explanation"],
        args_model=RefreshToolArgs,
        description=(
            "解释某张图/某个成果为什么过期：给出上游来源变化类别、证据级别、"
            "受影响分析节点、失效原因以及哪些结论不可判定。"
            "\n何时用：用户问『为什么这张图要重算』『哪些结果过期了』。"
            "\n只读，不修改任何状态；证据不足时明确说 unverifiable，不编造原因。"
        ),
        side_effect="pure", deterministic=True, latency_class="fast",
        memory_class="light", scale_class="small",
        tags=["过期解释", "影响分析", "why stale"],
        output_semantic_type="text", result_size_policy="inline_small",
        failure_modes=["proposal_not_found", "forbidden"],
    )
    async def explain_stale_map_product(
        project_id: str, proposal_id: Optional[str] = None,
        style_only: bool = False, cancel: bool = False,
        session_id: str = "",
    ) -> dict:
        try:
            pid = _need(proposal_id)
            with SessionLocal() as db:
                uid = await _authorized_context(db, project_id, session_id)
                data = await _service().explain_stale(
                    project_id=project_id, proposal_id=pid, user_id=uid)
            reasons, truncated = _bounded(data["reasons"])
            return {"success": True, "project_id": project_id,
                    "proposal_id": pid, "stale": data["stale"],
                    "reasons": reasons, "truncated": truncated or data["truncated"],
                    "affected_nodes": data["affected_nodes"][:MAX_SUMMARY_ITEMS],
                    "conservative": data["conservative"],
                    "unverifiable": data["unverifiable"][:MAX_SUMMARY_ITEMS],
                    "warnings": data["warnings"][:6], **_receipt()}
        except Exception as exc:  # noqa: BLE001
            return _error(exc)

    @tool(
        registry, name="webgis_preview_project_data_refresh",
        tier=2, domains=["project", "dataset"],
        capabilities=["refresh_impact_preview"],
        args_model=RefreshToolArgs,
        description=(
            "预览数据更新的影响：精确的新增/删改要素数、字段/单位/CRS/覆盖范围变化、"
            "将重算的最小可证明子图、可复用结果、预算、保留的人工调整与冲突。"
            "\n何时用：用户说『先看看会改什么』『预览更新』。"
            "\n关键语义：只读预览，不改 MapSpec / 数据 / 基线；输出绑定输入版本集、"
            "MapSpec revision、用户锁与权限的 proposal_fingerprint；"
            "无法证明局部安全时保守整项重算，抽样不得证明『没有变化』。"
        ),
        side_effect="artifact_creation", deterministic=False,
        latency_class="medium", memory_class="light", scale_class="small",
        tags=["预览影响", "重算计划", "proposal"],
        output_semantic_type="text", result_size_policy="inline_small",
        failure_modes=["forbidden", "proposal_persist_failed", "drift"],
    )
    async def preview_project_data_refresh(
        project_id: str, proposal_id: Optional[str] = None,
        style_only: bool = False, cancel: bool = False,
        session_id: str = "",
    ) -> dict:
        try:
            if not session_id:
                return {"error": "Missing session_id context",
                        **_receipt(False, "not_run")}
            with SessionLocal() as db:
                uid = await _authorized_context(db, project_id, session_id)
                prop = await _service().preview(
                    db, project_id=project_id, session_id=session_id,
                    user_id=uid, style_only=style_only)
            recompute, truncated = _bounded(prop.plan.recompute)
            return {
                "success": True, "project_id": project_id,
                "proposal_id": prop.proposal_id,
                "proposal_fingerprint": prop.proposal_fingerprint,
                "mapspec_revision": prop.mapspec_revision,
                "diffs": _diff_summary(prop.diffs),
                "recompute_nodes": recompute,
                "reuse_nodes": prop.plan.reuse[:MAX_SUMMARY_ITEMS],
                "conservative": prop.plan.conservative,
                "style_only": prop.plan.style_only,
                "estimated_budget": prop.plan.estimated_budget,
                "preserved_user_edits": prop.plan.preserved_user_edits[:MAX_SUMMARY_ITEMS],
                "conflicts": prop.plan.conflicts[:MAX_SUMMARY_ITEMS],
                "truncated": truncated or prop.inventory.truncated,
                "warnings": prop.warnings[:6],
                "preview_is_read_only": True,
                **_receipt(),
            }
        except Exception as exc:  # noqa: BLE001
            return _error(exc)

    @tool(
        registry, name="webgis_execute_project_data_refresh",
        tier=2, domains=["project", "dataset"],
        capabilities=["refresh_candidate_execution"],
        args_model=RefreshToolArgs,
        description=(
            "执行获授权的隔离候选更新：冻结基线、只重算计划内受影响节点、"
            "生成候选 MapSpec，并保留人工颜色/层序/隐藏状态/组件族锁/文本。"
            "\n何时用：用户确认『执行更新』『生成候选』（必须先 preview）。"
            "\n关键语义：候选写隔离命名空间，当前可见地图零触碰；"
            "版本集/proposal/revision 漂移会被拒绝并要求重预览；"
            "重复执行幂等；cancel=true 取消且当前成果仍可用。"
            "\n工具执行成功不等于地图已更新，也不等于已采纳新版本。"
        ),
        side_effect="state_mutation", deterministic=False, latency_class="slow",
        memory_class="medium", scale_class="medium",
        tags=["执行候选", "增量更新", "user-wins"],
        output_semantic_type="text", result_size_policy="inline_small",
        failure_modes=["proposal_drift", "execution_in_progress", "candidate_not_adoptable"],
    )
    async def execute_project_data_refresh(
        project_id: str, proposal_id: Optional[str] = None,
        style_only: bool = False, cancel: bool = False,
        session_id: str = "",
    ) -> dict:
        try:
            pid = _need(proposal_id)
            with SessionLocal() as db:
                uid = await _authorized_context(db, project_id, session_id)
                result = await _service().execute(
                    db, project_id=project_id, proposal_id=pid,
                    user_id=uid, cancel=cancel)
            cand = result.get("candidate")
            return {
                "success": True, "project_id": project_id, "proposal_id": pid,
                "replayed": result.get("replayed", False),
                "candidate_status": (cand.status.value if cand else "not_started"),
                "adoptable": bool(cand and cand.adoptable),
                "preserved": (cand.merge_preserved[:MAX_SUMMARY_ITEMS] if cand
                              else result.get("preserved", [])[:MAX_SUMMARY_ITEMS]),
                "conflicts": (cand.merge_conflicts[:MAX_SUMMARY_ITEMS] if cand else []),
                "truncated": False, "warnings": [],
                **_receipt(task=result["receipt"]["task_execution"]),
            }
        except Exception as exc:  # noqa: BLE001
            return _error(exc)

    @tool(
        registry, name="webgis_adopt_project_data_refresh",
        tier=2, domains=["project", "mapspec"],
        capabilities=["refresh_atomic_adoption"],
        args_model=RefreshToolArgs,
        description=(
            "受控采纳候选为新项目成果版本：走既有 MapSpec CAS（expected revision）"
            "+ proposal fingerprint 双核对，原子提交，revision 单调增。"
            "\n何时用：用户看过候选对比后说『采纳』『应用更新』。"
            "\n关键语义：采纳 ≠ 地图已正确显示（map_observation 仍为 unverified）；"
            "冲突/硬语义错误未解决时拒绝；旧快照不可变、不倒写 revision。"
        ),
        side_effect="state_mutation", deterministic=False, latency_class="medium",
        memory_class="light", scale_class="small",
        tags=["采纳候选", "原子提交", "CAS"],
        output_semantic_type="text", result_size_policy="inline_small",
        failure_modes=["proposal_drift", "superseded", "candidate_not_ready"],
    )
    async def adopt_project_data_refresh(
        project_id: str, proposal_id: Optional[str] = None,
        style_only: bool = False, cancel: bool = False,
        session_id: str = "",
    ) -> dict:
        try:
            pid = _need(proposal_id)
            with SessionLocal() as db:
                uid = await _authorized_context(db, project_id, session_id)
                receipt = await _service().adopt(
                    db, project_id=project_id, proposal_id=pid, user_id=uid)
            return {"success": receipt.status == "adopted",
                    "project_id": project_id, "proposal_id": pid,
                    "status": receipt.status,
                    "revision_before": receipt.revision_before,
                    "revision_after": receipt.revision_after,
                    "replayed": receipt.replayed,
                    "warnings": receipt.warnings[:6],
                    **_receipt(task=receipt.task_execution,
                               adoption=receipt.version_adoption)}
        except Exception as exc:  # noqa: BLE001
            return _error(exc)

    @tool(
        registry, name="webgis_discard_project_data_refresh",
        tier=2, domains=["project"],
        capabilities=["refresh_candidate_discard"],
        args_model=RefreshToolArgs,
        description=(
            "放弃候选更新：保留旧成果版本，释放候选 GC pin 与配额，当前地图不变。"
            "\n何时用：用户说『不要这次更新』『放弃候选』。已采纳的提案请用回退。"
        ),
        side_effect="state_mutation", deterministic=False, latency_class="fast",
        memory_class="light", scale_class="small",
        tags=["放弃候选", "GC pin", "discard"],
        output_semantic_type="text", result_size_policy="inline_small",
        failure_modes=["already_adopted", "proposal_not_found"],
    )
    async def discard_project_data_refresh(
        project_id: str, proposal_id: Optional[str] = None,
        style_only: bool = False, cancel: bool = False,
        session_id: str = "",
    ) -> dict:
        try:
            pid = _need(proposal_id)
            with SessionLocal() as db:
                uid = await _authorized_context(db, project_id, session_id)
                receipt = await _service().discard(
                    project_id=project_id, proposal_id=pid, user_id=uid)
            return {"success": True, "project_id": project_id,
                    "proposal_id": pid, "status": receipt.status,
                    "warnings": receipt.warnings[:6], **_receipt()}
        except Exception as exc:  # noqa: BLE001
            return _error(exc)

    @tool(
        registry, name="webgis_rollback_project_data_refresh",
        tier=2, domains=["project", "mapspec"],
        capabilities=["refresh_version_rollback"],
        args_model=RefreshToolArgs,
        description=(
            "回退已采纳的更新：调用既有版本恢复接口或以新 revision 前向恢复，"
            "不倒写 revision、不修改不可变快照、不删除用户后续数据。"
            "\n何时用：用户说『回退到旧版』『撤销采纳』。回退后地图需重新观察。"
        ),
        side_effect="state_mutation", deterministic=False, latency_class="medium",
        memory_class="light", scale_class="small",
        tags=["回退", "版本恢复", "rollback"],
        output_semantic_type="text", result_size_policy="inline_small",
        failure_modes=["rollback_unavailable", "forbidden"],
    )
    async def rollback_project_data_refresh(
        project_id: str, proposal_id: Optional[str] = None,
        style_only: bool = False, cancel: bool = False,
        session_id: str = "",
    ) -> dict:
        try:
            pid = _need(proposal_id)
            with SessionLocal() as db:
                uid = await _authorized_context(db, project_id, session_id)
                receipt = await _service().rollback(
                    project_id=project_id, proposal_id=pid, user_id=uid, db=db)
            return {"success": receipt.status == "rolled_back",
                    "project_id": project_id, "proposal_id": pid,
                    "status": receipt.status,
                    "revision_before": receipt.revision_before,
                    "revision_after": receipt.revision_after,
                    "warnings": receipt.warnings[:6],
                    **_receipt(task=receipt.task_execution,
                               adoption=receipt.version_adoption)}
        except Exception as exc:  # noqa: BLE001
            return _error(exc)

    @tool(
        registry, name="webgis_project_refresh_status",
        tier=2, domains=["project"],
        capabilities=["refresh_status_query"],
        args_model=RefreshToolArgs,
        description=(
            "查询刷新提案与候选进度（刷新页面后可恢复）：preview/ready/adopted/"
            "drifted/discarded 状态、候选完成步骤、已采纳 revision。"
            "\n何时用：用户问『更新到哪一步了』『候选还在吗』。"
        ),
        side_effect="pure", deterministic=True, latency_class="fast",
        memory_class="light", scale_class="small",
        tags=["刷新状态", "进度恢复", "status"],
        output_semantic_type="text", result_size_policy="inline_small",
        failure_modes=["proposal_not_found", "forbidden"],
    )
    async def project_refresh_status(
        project_id: str, proposal_id: Optional[str] = None,
        style_only: bool = False, cancel: bool = False,
        session_id: str = "",
    ) -> dict:
        try:
            pid = _need(proposal_id)
            with SessionLocal() as db:
                uid = await _authorized_context(db, project_id, session_id)
                data = await _service().status(
                    project_id=project_id, proposal_id=pid, user_id=uid)
            return {"success": True, **data, **_receipt()}
        except Exception as exc:  # noqa: BLE001
            return _error(exc)


__all__ = ["register_project_data_refresh_tools", "RefreshToolArgs"]
