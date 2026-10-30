"""Agent 要素编辑工具面（/goal 03）：preview / commit / history。

信任边界：
- preview 纯读（deterministic_compute），产出有界 plan 摘要并暂存 plan_id；
- commit 是 state_mutation（session_state）——仅当 plan.safe 且会话策略
  allow_safe_attribute 才自动执行；几何变更/校验 findings/confirm_all 一律
  CONFIRMATION_REQUIRED（用户在提案卡上显式 approve 后经 REST 执行）；
- history 纯读。tier ≤2（Pi native 面可见；绝不标 destructive）。
"""

from typing import Any, Dict, List, Optional

from app.services.feature_edit import executor as fe_executor
from app.services.feature_edit import service as fe_service
from app.services.session_data import session_data_manager
from app.tools.registry import ToolRegistry, tool

_MAX_OPS = 8


def register_feature_edit_tools(registry: ToolRegistry) -> None:
    """注册要素编辑工具族。"""

    @tool(
        registry,
        tier=1,
        name="vector_feature_edit_preview",
        side_effect="deterministic_compute",
        tags=["要素编辑", "feature edit", "编辑预览", "批量字段", "geometry fix"],
        capabilities=["dataset_ingest"],
        latency_class="medium",
        memory_class="medium",
        description=(
            "数据集要素编辑预览（纯读）：类型/枚举/单位校验 + before/after diff + "
            "plan_id（TTL 10min）。词表 set_fields/rename_field/repair_geometry/"
            "move_by(米制)/snap_to_ref。违规即拒。"
        ),
        param_descriptions={
            "ops": "编辑操作（≤8），如 [{\"op\":\"set_fields\",\"fields\":{\"type\":\"小学\"}}]",
            "ref_id": "目标 ref（ref:…）；缺省取当前选中",
            "feature_ids": "目标要素 id；缺省取选中集",
        },
    )
    async def webgis_vector_feature_edit_preview(
        ops: List[Dict[str, Any]],
        ref_id: Optional[str] = None,
        feature_ids: Optional[List[str]] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not session_id:
            return {"success": False, "code": "MISSING_SESSION", "message": "Missing session_id"}
        if not isinstance(ops, list) or not ops or len(ops) > _MAX_OPS:
            return {"success": False, "code": "INVALID_ARGS",
                    "message": f"ops must be a non-empty list of at most {_MAX_OPS} edit ops"}
        plan, findings = await fe_service.build_and_store_plan(
            session_id, ref_id=ref_id, feature_ids=feature_ids, ops=ops
        )
        summary = fe_service.summarize(plan, findings)
        if not plan:
            first = findings[0] if findings else {}
            return {
                "success": False,
                "code": str(first.get("code") or "invalid_op").upper(),
                "message": str(first.get("message") or "preview could not be built"),
                "findings": findings[:8],
                "correction_hint": "检查 ref/目标要素与操作词表；枚举/类型/单位违规会给出 allowed 词表。",
            }
        if findings or not plan.get("committable"):
            first = findings[0] if findings else {"code": "invalid_op", "message": "plan not committable"}
            return {
                "success": False,
                "code": str(first.get("code") or "invalid_op").upper(),
                "message": str(first.get("message") or "validation findings present"),
                "findings": findings[:8],
                "correction_hint": "按 findings 修正值/单位/目标后重新 preview；词表外的值会被拒绝而不会静默写入。",
                "plan_summary": summary,
            }
        return {"success": True, **summary}

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="vector_feature_edit_commit",
        side_effect="state_mutation",
        data_mutations=["session_state"],
        tags=["要素编辑", "feature edit", "提交", "数据修改"],
        capabilities=["dataset_ingest"],
        latency_class="medium",
        memory_class="medium",
        deterministic=False,
        description=(
            "提交一个已预览的编辑 plan（plan_id 来自 vector_feature_edit_preview）。"
            "执行链：会话锁 + 数据版本 CAS（过期即 STALE_REVISION 带冲突 diff）→"
            "应用变更（不触碰原始上传文件；journal 全程留痕 before/after）→ 数据"
            "content_revision 递增 → MapSpec 数据源新鲜度盖章 → 地图增量刷新。"
            "幂等：同 plan_id 重复提交返回同一 receipt，绝不写两版。"
            "**自动执行条件**（其余一律 CONFIRMATION_REQUIRED，等用户在提案卡 approve）："
            "纯属性编辑 + 校验零 findings + ≤50 要素 + 会话策略 allow_safe_attribute。"
            "几何位移/吸附/修复永远需要用户显式确认。undo 走 "
            "webgis_vector_feature_edit_history 查看后由用户在界面执行。"
        ),
        param_descriptions={
            "plan_id": "webgis_vector_feature_edit_preview 返回的 plan_id",
            "session_id": "会话 id（运行时注入，无需传）",
        },
    )
    async def webgis_vector_feature_edit_commit(
        plan_id: str,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not session_id:
            return {"success": False, "code": "MISSING_SESSION", "message": "Missing session_id"}
        if not isinstance(plan_id, str) or not plan_id.startswith("fep-"):
            return {"success": False, "code": "INVALID_ARGS",
                    "message": "plan_id must come from webgis_vector_feature_edit_preview"}
        result = await fe_service.agent_commit(session_id, plan_id)
        if result.get("success"):
            receipt = result.get("receipt") or {}
            return {
                "success": True,
                "plan_id": plan_id,
                "ref_id": receipt.get("ref_id"),
                "entry_seq": receipt.get("entry_seq"),
                "content_revision_after": receipt.get("content_revision_after"),
                "changed_wfids": receipt.get("changed_wfids"),
                "stamp_status": receipt.get("stamp_status"),
                "mapspec_revision": receipt.get("mapspec_revision"),
                "message": "edit committed; dataset revision bumped and map refresh dispatched",
            }
        return result

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="vector_feature_edit_history",
        side_effect="cacheable_read",
        tags=["要素编辑", "edit history", "撤销", "审计"],
        capabilities=["dataset_ingest"],
        latency_class="fast",
        memory_class="light",
        description=(
            "读数据集（ref:…）的要素编辑历史（有界 journal：seq/kind/actor/"
            "变更要素数/数据版本 + dropped 披露）。undo/redo 由用户在界面执行。"
        ),
        param_descriptions={"ref_id": "数据集 ref（ref:…）", "limit": "条数（≤50）"},
    )
    async def webgis_vector_feature_edit_history(
        ref_id: str,
        limit: int = 20,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not session_id:
            return {"success": False, "code": "MISSING_SESSION", "message": "Missing session_id"}
        payload = await session_data_manager.get(session_id, ref_id)
        if payload is None:
            return {"success": False, "code": "DATASET_NOT_FOUND",
                    "message": f"ref not resolvable in this session: {str(ref_id)[:48]}"}
        journals = await fe_executor._load_journals(session_id)
        journal = journals.get(ref_id) or {}
        entries = list(reversed(journal.get("entries") or []))[: max(1, min(int(limit), 50))]
        return {
            "success": True,
            "ref_id": ref_id,
            "head_seq": journal.get("head_seq") or 0,
            "dropped": journal.get("dropped") or 0,
            "entries": [
                {
                    "seq": e.get("seq"),
                    "kind": e.get("kind"),
                    "actor": e.get("actor"),
                    "origin": e.get("origin"),
                    "changed": len({d.get("wfid") for d in e.get("deltas") or [] if isinstance(d, dict)}),
                    "schema_ops": e.get("schema_ops") or [],
                    "content_revision_after": e.get("content_revision_after"),
                }
                for e in entries
            ],
        }
