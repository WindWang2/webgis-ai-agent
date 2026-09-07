"""工作空间工具 —— Agent 的快照/恢复/盘点面（Workspace V4，Wave 2）。

设计约束（与 data_discovery 同款纪律）：
- **manifest + 有界摘要，绝不是数据本体**（§三十二 large-data policy）：
  快照保存/恢复只返回 id/计数/指针统计；describe 返回盘点（覆盖率%），
  全部有界；
- 会话归属走 ``_resolve_session_id`` 运行时上下文校验 —— LLM 提供的
  session_id 不可信；
- 工具面是**会话域**的（无项目鉴权上下文）：save/list/restore/describe
  只作用于当前会话；项目域快照治理（跨会话 clone / 项目级 delete）走
  REST（/projects/{pid}/workspace/...，带项目+会话双重鉴权）；
- 任何后端故障返回诚实错误 dict（success/False + reason），绝不静默
  返回空结果冒充「没有快照」。
"""
import logging
from typing import Optional

from app.tools.registry import tool
from app.tools.upload_tools import _resolve_session_id

logger = logging.getLogger(__name__)


def register_workspace_tools(registry) -> None:
    """注册工作空间快照工具集（tier-2）。"""

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="save_workspace_snapshot",
        description=(
            "保存当前工作空间快照：产物账本 + 图层引用 + 视图设置 + 持久指针。"
            "materialize=claimed 时把存活载荷物化到持久内容库（会话过期后仍可"
            "恢复真实数据，不止元数据）。✅ 用于：阶段性成果固化 / 长分析前留档。"
        ),
        param_descriptions={
            "label": "快照标签（如 before-merge、mid-analysis）",
            "materialize": (
                "none=只存元数据（缺省）；claimed=物化账本产物载荷；"
                "all=另加图层/图表引用的载荷"
            ),
        },
    )
    async def save_workspace_snapshot(
        label: str = "",
        materialize: str = "none",
        session_id: Optional[str] = None,
    ) -> dict:
        from app.services.workspace.snapshot import get_workspace_snapshot_service

        sid = _resolve_session_id(session_id)
        if not sid:
            return {"success": False, "code": "NO_SESSION",
                    "error": "缺少有效会话，无法保存工作空间快照"}
        if materialize not in ("none", "claimed", "all"):
            return {"success": False, "code": "INVALID_MATERIALIZE",
                    "error": "materialize 只接受 none | claimed | all"}
        snapshot = await get_workspace_snapshot_service().save_snapshot(
            sid, label=str(label)[:96], materialize=materialize
        )
        if snapshot is None:
            return {"success": False, "code": "SAVE_FAILED",
                    "error": "快照保存失败（会话状态不可读或磁盘写入失败）"}
        return {
            "success": True,
            "snapshot_id": snapshot.snapshot_id,
            "label": snapshot.label,
            "artifacts": len(snapshot.artifact_contracts),
            "layers": len(snapshot.layers),
            "durable_pointers": len(snapshot.durable_pointers),
            "materialize": materialize,
            "materialize_skipped": list(snapshot.materialize_skipped)[:8],
            "message": (
                "已保存。materialize=none 仅存元数据，会话过期后载荷不可恢复；"
                "需要真正可恢复请用 materialize=claimed"
                if materialize == "none"
                else "已保存，存活载荷已物化到持久内容库（digest 校验读回）。"
            ),
        }

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="list_workspace_snapshots",
        description=(
            "列出本会话可读的工作空间快照（id/标签/时间/产物数；≤50 条）。"
            "✅ 用于：恢复前先看看有哪些存档。"
        ),
    )
    async def list_workspace_snapshots(session_id: Optional[str] = None) -> dict:
        from app.services.workspace.snapshot import get_workspace_snapshot_service

        sid = _resolve_session_id(session_id)
        if not sid:
            return {"success": True, "snapshots": [], "count": 0,
                    "message": "当前会话没有快照。可先 save_workspace_snapshot。"}
        items = await get_workspace_snapshot_service().list_snapshots(sid)
        return {"success": True, "snapshots": items, "count": len(items)}

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="restore_workspace_snapshot",
        description=(
            "核查或恢复一个工作空间快照。mode=verify 只出报告（哪些产物/图层"
            "已失效、持久指针是否完好）；mode=register 恢复产物账本血缘，并把"
            "带持久指针的失效载荷**真实写回**（digest 校验读后原位重物化）。"
            "✅ 用于：会话过期/误删后找回工作空间。"
        ),
        param_descriptions={
            "snapshot_id": "快照 id（list_workspace_snapshots 返回）",
            "mode": "verify=只核查（缺省）；register=恢复账本+重物化载荷",
        },
    )
    async def restore_workspace_snapshot(
        snapshot_id: str,
        mode: str = "verify",
        session_id: Optional[str] = None,
    ) -> dict:
        from app.services.workspace.snapshot import get_workspace_snapshot_service

        sid = _resolve_session_id(session_id)
        if not sid or not snapshot_id:
            return {"success": False, "code": "NOT_FOUND",
                    "error": "缺少有效的会话或快照 id"}
        if mode not in ("verify", "register"):
            return {"success": False, "code": "INVALID_MODE",
                    "error": "mode 只接受 verify | register"}
        result = await get_workspace_snapshot_service().restore_snapshot(
            sid, snapshot_id, mode=mode
        )
        if result.get("error"):
            return {"success": False, "code": "NOT_FOUND",
                    "error": str(result["error"])}
        return {"success": True, **result}

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="describe_workspace",
        description=(
            "盘点当前工作空间：快照数量、产物生命周期分布、图层/图表引用、"
            "持久覆盖率（多少产物已有可恢复的持久内容）。✅ 用于：评估当前"
            "工作空间的持久化健康度。"
        ),
    )
    async def describe_workspace(session_id: Optional[str] = None) -> dict:
        from app.services.workspace.snapshot import get_workspace_snapshot_service

        sid = _resolve_session_id(session_id)
        if not sid:
            return {"success": False, "code": "NO_SESSION",
                    "error": "缺少有效会话，无法盘点工作空间"}
        inventory = await get_workspace_snapshot_service().describe_workspace(sid)
        return {"success": True, **inventory}
