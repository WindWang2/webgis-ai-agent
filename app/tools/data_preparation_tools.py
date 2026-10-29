"""数据准备工具 —— prepare_dataset 四态判别式（M6；契约 §6）。

状态语义（closed 词表，诚实优先）：
- ``blocked``：存在 blocked 级 finding（如 AXIS_ORDER_SUSPECT / CRS 缺失）——
  绝不自动执行，也不谎称可执行；
- ``requires_input``：字段角色存在澄清项（needs_clarification 且未被
  role_hints 解决）—— 返回澄清问题（候选 + 置信度 + 原因码，无样本值）；
- ``ready``：无阻断、无澄清，但 auto_execute=false —— 一切就绪待确认执行
  （inspect-only 模式的诚实态；不冒充 prepared）；
- ``prepared``：auto_execute=true 且执行成功，返回 output_ref + 收据摘要；
- ``degraded``：执行成功但存在变换级降级（如 pyproj 缺席 reproject 仅
  attach-CRS）—— output_ref 可用，降级码显式披露。

纪律（ingest_tools 同款）：
- 有界输出：绝不回传行数据（inspect 样本不进 llm_payload，收据只有计数/码）；
- 会话归属走 _resolve_session_id（LLM 提供的 session_id 不可信）；
- 准备本体零新逻辑：inspect/plan/execute 全部委托 data_preparation 域。
"""
import logging
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool
from app.tools.upload_tools import _resolve_session_id

logger = logging.getLogger(__name__)


class PrepareDatasetArgs(BaseModel):
    """Args model —— source_ref 是声明式 ref 游标（#1004 通道）：
    registry 的透明别名解引用不得把整个载荷内联进参数（工具自身按需
    fetch，输出走 ref_offload）。"""

    source_ref: str = Field(..., json_schema_extra={"ref_cursor": True})
    role_hints: Optional[Dict[str, str]] = None
    auto_execute: bool = False
    session_id: Optional[str] = None

#: 澄清问题字段上限（有界）。
_MAX_CLARIFICATIONS = 16
#: blocked findings 披露上限。
_MAX_BLOCKED_FINDINGS = 8


def _finding_out(f) -> Dict[str, Any]:
    return {"code": f.code, "severity": f.severity.value,
            "message": f.message_zh, "suggestion": f.suggestion.value,
            "field": f.field}


#: 自动执行可自解决的收据建议码（其余 blocked 一律硬阻断）。
#: filter_rows：越界行过滤有 20% 硬闸 + typed 拒绝兜底，可安全自动；
#: switch_xy/declare_crs/pick_timezone 等必须经用户确认 —— 绝不自动。
_AUTO_RESOLVABLE = frozenset({"filter_rows"})


def register_data_preparation_tools(registry: ToolRegistry) -> None:
    """注册数据准备工具集。"""

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="prepare_dataset",
        args_model=PrepareDatasetArgs,
        side_effect="artifact_creation",
        produced_refs=["data"],
        output_semantic_type="ref",
        result_size_policy="ref_offload",
        tags=["数据准备", "字段映射", "语义绑定", "preparation"],
        capabilities=["data_preparation"],
        latency_class="medium",
        memory_class="medium",
        description=(
            "检查并准备会话内数据集用于 GIS 分析/制图：空间语义收据（几何/CRS/"
            "坐标轴序/主键/时区/分母缺口）+ 字段角色候选提案（中英双语、置信度、"
            "被拒原因）→ 按需执行可审计准备计划（过滤越界行/去重/字段映射确认，"
            "原件不可变，输出新 ref + 收据）。返回 prepared/requires_input/"
            "degraded/blocked/ready 判别态 —— blocked/需要澄清时绝不谎称已就绪。"
            "✅ 用于：上传/摄入后的数据在进入分析（如分区统计）前需要检查坐标、"
            "确认字段映射、修正对调、按提案准备。"
        ),
        param_descriptions={
            "source_ref": "会话内数据 ref（如 ingest_dataset/upload 的返回或 ref:prep-*）",
            "role_hints": "可选角色提示 {字段名:角色码}（来自用户对话；与值域/类型冲突时会被拒绝并披露）",
            "auto_execute": "true 时：无 blocked 且无澄清项才自动构建并执行计划（含高置信映射 + 越界行过滤）",
            "session_id": "会话 id（通常无需传入，由运行时注入）",
        },
    )
    async def prepare_dataset(
        source_ref: str,
        role_hints: Optional[Dict[str, str]] = None,
        auto_execute: bool = False,
        session_id: Optional[str] = None,
    ) -> dict:
        from app.services.data_preparation.executor import ExecError, execute_plan
        from app.services.data_preparation.plan import (
            FieldMappingStep,
            FilterRowsStep,
            build_plan,
            save_plan,
        )
        from app.services.data_preparation.service import InspectError, inspect_source
        from app.services.data_preparation.vocabulary import FINDING_AXIS_ORDER_SUSPECT

        sid = _resolve_session_id(session_id)
        if not sid:
            return {"success": False, "code": "NO_SESSION",
                    "error": "缺少有效会话（需要在会话上下文中调用）"}
        if not source_ref or not str(source_ref).startswith("ref:"):
            return {"success": False, "code": "INVALID_REF",
                    "error": "source_ref 必须是会话内数据 ref（ref:…）"}

        # 1) 检查（用户提示进入提案引擎 —— 提示与证据冲突会被反证披露）。
        try:
            result = await inspect_source(
                sid, str(source_ref), user_hints=role_hints or {})
        except InspectError as exc:
            status = 404 if exc.code == "REF_MISSING" else 400
            return {"success": False, "code": exc.code,
                    "error": exc.detail or exc.code, "http_hint": status}
        receipt = result["receipt"]
        proposals = result["proposals"]

        # 阻断策略：收据 severity=blocked 是**事实**；是否阻断自动执行是
        # **策略** —— 建议码可自动解决（如越界行过滤，有硬闸兜底）的不算
        # 硬阻断，其余（坐标对调/CRS 声明等须用户确认的）一律 blocked。
        blocked = [f for f in receipt.findings if f.severity.value == "blocked"]
        hard_blocked = [f for f in blocked
                        if f.suggestion.value not in _AUTO_RESOLVABLE]
        if hard_blocked:
            return {
                "success": True,
                "status": "blocked",
                "input_ref": source_ref,
                "blocked_findings": [_finding_out(f)
                                     for f in hard_blocked[:_MAX_BLOCKED_FINDINGS]],
                "receipt_summary": receipt.summary(),
                "guidance": "阻断项需用户处置（如确认经纬度对调、声明 CRS）后再准备",
            }

        clarifications = [
            {"field": p.field,
             "candidates": [{"role": c.role, "confidence": c.confidence,
                             "reasons": list(c.reasons)}
                            for c in p.candidates],
             "rejected": [[r, code] for r, code in p.rejected]}
            for p in proposals if p.needs_clarification
        ][:_MAX_CLARIFICATIONS]
        if clarifications and not auto_execute:
            return {
                "success": True,
                "status": "requires_input",
                "input_ref": source_ref,
                "clarifications": clarifications,
                "receipt_summary": receipt.summary(),
                "guidance": "请用户确认字段角色（role_hints）后重试",
            }
        if not auto_execute:
            return {
                "success": True,
                "status": "ready",
                "input_ref": source_ref,
                "proposal": {
                    p.field: (p.top.role if p.top else None) for p in proposals
                    if p.top is not None},
                "receipt_summary": receipt.summary(),
                "guidance": "无阻断无澄清；确认后以 auto_execute=true 执行准备",
            }

        # 2) 自动执行：高置信映射 + 证据齐备的越界过滤（swap/重投影绝不自动）。
        steps = []
        for p in proposals:
            top = p.top
            if top is None or p.needs_clarification:
                continue
            steps.append(FieldMappingStep(role=top.role, field=p.field,
                                          source="proposal"))
        if any(f.code == "impossible_coordinates" for f in receipt.findings):
            steps.append(FilterRowsStep(condition_code="impossible_coordinates",
                                        max_drop_frac=0.2))
        if not steps:
            return {"success": True, "status": "requires_input",
                    "input_ref": source_ref,
                    "clarifications": clarifications or [{"field": "*",
                                                          "candidates": []}],
                    "receipt_summary": receipt.summary(),
                    "guidance": "无可自动执行的步（无高置信映射/无过滤证据）—— 请提供角色确认"}

        axis_evidence = None
        for f in receipt.findings:
            if f.code == FINDING_AXIS_ORDER_SUSPECT:
                axis_evidence = dict(f.evidence)
        crs_source = "declared" if receipt.srs else ""
        fields = list((result["profile"].vector.fields
                       if result["profile"].vector is not None else {}).keys())
        try:
            plan = build_plan(
                session_id=sid, owner="agent", input_ref=str(source_ref),
                input_fingerprint=result["input_fingerprint"],
                steps=steps, axis_suspect_evidence=axis_evidence,
                crs_source=crs_source, available_fields=fields)
            await save_plan(plan)
            receipt_out = await execute_plan(plan)
        except ExecError as exc:
            return {"success": False, "code": exc.code,
                    "error": exc.detail or exc.code}
        except Exception as exc:  # noqa: BLE001 — typed 兜底
            return {"success": False, "code": "PLAN_BUILD_FAILED",
                    "error": str(exc)[:200]}

        degraded = list(receipt_out.degraded)
        return {
            "success": True,
            "status": "degraded" if degraded else "prepared",
            "input_ref": source_ref,
            "output_ref": receipt_out.output_ref,
            "receipt_summary": {
                "plan_hash": receipt_out.plan_hash,
                "output_fingerprint": receipt_out.output_fingerprint,
                "row_before": receipt_out.row_before,
                "row_after": receipt_out.row_after,
                "quality_delta": {
                    k: receipt_out.quality_after.get(k, 0)
                    - receipt_out.quality_before.get(k, 0)
                    for k in sorted(set(receipt_out.quality_before)
                                    | set(receipt_out.quality_after))
                    if receipt_out.quality_after.get(k, 0)
                    != receipt_out.quality_before.get(k, 0)},
                "user_confirmations": [dict(c)
                                       for c in receipt_out.user_confirmations],
            },
            "degradations": [{"code": d} for d in degraded],
            "guidance": "已就绪：output_ref 可直接用于分析/制图（ref 语义已含确认角色）",
        }
