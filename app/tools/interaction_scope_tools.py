"""交互情境 scope 工具（ADR-0285 D1）。

``webgis_selection_scope``：把用户当前地图选择（点选/框选/表格定位/图表
类别选择/AOI 绘制）验证为**可执行的分析 scope**——ref 所有权、数据版本、
要素存活、宿主层在架全部服务端核实；agent 拿到后显式传参给真实分析工具。

只读工具：不解析大 payload 进 LLM（ids/摘要有界）、不向
ToolDispatchService 注入隐式 scope、不写任何权威状态。多轮指代
（「刚才那组学校」）经 ``selection_ref`` 解析选择史（ADR-0285 D3）。
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from app.services.gis_situation.analysis_scope import ClaimedScope, ValidatedScope

from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool


class ScopeToolResult(ValidatedScope, total=False):
    success: bool
    error: str
    validated: bool
    claimed: ClaimedScope
    correction_hint: str
    selection_ref: str

logger = logging.getLogger(__name__)


class WebgisSelectionScopeArgs(BaseModel):
    """webgis_selection_scope 的 LLM 参数模型（future annotations 同款处理）。"""

    selection_ref: str = Field(
        "current",
        description=(
            "选择句柄：current=当前选择（默认）；previous=上一个已被替代的选择集"
            "（『刚才那组学校』）；epoch_seq 数字=选择史指定代"
        ),
    )
    perform_validation: bool = Field(
        True,
        description="true=服务端验证（ref 所有权/版本/存活，推荐）；false=只看声称面",
    )


def _selection_scope_enabled() -> bool:
    """GIS_SELECTION_SCOPE=0 关停（工具返回 typed disabled，不静默伪装）。"""
    return os.environ.get("GIS_SELECTION_SCOPE", "1") != "0"


def register_interaction_scope_tools(registry: ToolRegistry) -> None:
    """注册交互情境 scope 工具。"""

    @tool(
        registry,
        name="webgis_selection_scope",
        capabilities=["workspace_state_inspection"],
        domains=["statistics"],
        tier=2,
        args_model=WebgisSelectionScopeArgs,
        description=(
            "读取并验证用户当前的地图选择，返回可作为分析输入的 scope。"
            "\n何时用：用户话里指当前/刚才的选择（『这些学校』『刚才框选的区域』"
            "『只看选中的区』），下一步要做统计/缓冲/聚合等分析时——先取经验证"
            "的 scope（dataset_ref + feature_ids 或 AOI bbox），再把结果显式传给"
            "分析工具的 ref_id/feature_ids/bbox 参数。"
            "\n返回语义：status=usable（附 dataset_ref/feature_ids/content_revision/"
            "bbox）；stale（revision 漂移或要素全部缺失，附 reason，须向用户披露）；"
            "unusable（无选择/ref 不可解析/宿主层已删，附 reason 与 correction_hint）。"
            "selection_ref=previous 可取上一个选择集（多轮指代）。"
            "\n何时不用：用户明确给了数据范围/图层名——直接用；需要修改数据走"
            "feature_edit 工具族。"
        ),
        side_effect="pure",
        deterministic=True,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=("选择", "分析范围", "情境", "selection scope", "interaction"),
        output_semantic_type="text",
        result_size_policy="inline_small",
        required_context=("map_state",),
        map_mutations=(),
        data_mutations=(),
        failure_modes=("no_selection", "no_dataset_ref", "ref_unresolvable", "layer_removed",
                       "layer_unavailable", "source_ref_mismatch",
                       "revision_drifted", "features_missing", "disabled"),
    )
    async def webgis_selection_scope(
        selection_ref: str = "current",
        perform_validation: bool = True,
        session_id: str = "",
    ) -> ScopeToolResult:
        """当前/历史选择 → 经验证的分析 scope（只读）。"""
        if not session_id:
            return {"error": "Missing session_id context"}
        if not _selection_scope_enabled():
            return {
                "success": False, "error": "selection scope tool disabled",
                "reason": "disabled",
                "correction_hint": "GIS_SELECTION_SCOPE=0；请让用户显式描述分析范围",
            }

        from app.services.gis_situation.analysis_scope import (
            derive_claimed_scope,
            validate_analysis_scope,
        )
        from app.services.gis_harness.interaction_ir.projection import (
            interaction_snapshot_via_store,
        )
        from app.services.session_data import session_data_manager

        try:
            snapshot = await interaction_snapshot_via_store(
                session_data_manager, session_id,
            )
        except Exception:  # noqa: BLE001 — 读面失败 = 诚实 unusable
            logger.warning("[webgis_selection_scope] snapshot read failed", exc_info=True)
            snapshot = {}

        claimed: Optional[ClaimedScope] = None
        if selection_ref in ("current", ""):
            pre_turn = {}
            try:
                map_state = await session_data_manager.get_map_state(session_id)
                pre_turn = map_state.get("_cartographic_context_observation")
                pre_turn = pre_turn if isinstance(pre_turn, dict) else {}
            except Exception:  # noqa: BLE001 — pre-turn 是兜底源，缺席按空
                pre_turn = {}
            affordances = None
            try:
                ring = await session_data_manager.get_state_field(
                    session_id, "_situation_canvas_affordances")
                affordances = ring if isinstance(ring, list) else None
            except Exception:  # noqa: BLE001
                affordances = None
            claimed = derive_claimed_scope(
                snapshot,
                pre_turn.get("selected_feature")
                if isinstance(pre_turn.get("selected_feature"), dict) else None,
                affordance_ring=affordances,
            )
        else:
            from app.services.gis_situation.selection_history import (
                resolve_selection_ref,
            )
            try:
                ledger = await session_data_manager.get_state_field(
                    session_id, "_interaction_ir_ledger")
                ledger_events = ledger if isinstance(ledger, list) else []
            except Exception:  # noqa: BLE001 — 史面读失败 = 句柄不可解析
                ledger_events = []
            claimed = resolve_selection_ref(ledger_events, selection_ref)
            if claimed is None:
                return {
                    "success": False,
                    "status": "unusable",
                    "reason": "selection_ref_unresolvable",
                    "correction_hint": (
                        "选择史中没有可解析的句柄；请列出候选或让用户重新选择"
                    ),
                    "selection_ref": selection_ref,
                }

        if not perform_validation:
            # review P3：声称面必须显式标注未验证（不冒充事实）。
            return {
                "success": claimed.get("kind") != "none",
                "validated": False,
                "claimed": claimed,
            }

        try:
            validated = await validate_analysis_scope(session_id, claimed)
        except Exception as exc:  # noqa: BLE001 — 验证链异常 = fail-closed
            logger.warning("[webgis_selection_scope] validate failed", exc_info=True)
            return {
                "success": False, "status": "unusable", "reason": "validation_error",
                "correction_hint": f"scope 验证失败：{str(exc)[:120]}",
                "claimed": claimed,
            }
        result: ScopeToolResult = {**validated}
        result["success"] = validated.get("status") == "usable"
        result["selection_ref"] = selection_ref
        if validated.get("status") == "unusable":
            hints = {
                "no_selection": "用户当前没有选中任何要素/区域；请询问或让用户选择",
                "no_dataset_ref": "选中层无会话数据 ref（可能内联小层）；请改用图层级分析",
                "ref_unresolvable": "选择引用的数据已不可解析（跨会话/已清理）",
                "layer_removed": "选中要素的宿主图层已从地图移除",
            }
            result["correction_hint"] = hints.get(
                str(validated.get("reason")), "选择不可作为分析输入",
            )
        elif validated.get("status") == "stale":
            result["correction_hint"] = (
                "选择已过期（"
                + str(validated.get("reason"))
                + "）；向用户披露并请求确认后再分析"
            )
        return result
