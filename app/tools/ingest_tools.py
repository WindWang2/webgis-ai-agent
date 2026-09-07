"""数据摄入工具 —— inline FeatureCollection → V3 摄入管线（审计 R1 生产缝）。

设计约束（与 data_discovery 同一纪律）：
- **有界输出**：只返回 ref id / 描述符投影 / profile 摘要 / 质量摘要 /
  repairable 计数 —— 绝不回传数据本体（§三十二 large-data policy）；
- 会话归属走 ``_resolve_session_id`` 运行时上下文校验 —— LLM 提供的
  session_id 不可信；
- 摄入本体零新逻辑：直接调用 ``get_ingest_pipeline().ingest``（去重/
  画像/质量/登记/回滚全部是管线既有语义）；
- ``propose_repairs=True`` 只产出**修复提案**（plan-only，绝不执行 ——
  执行是 Wave-4 的 SpatialRepairPipeline 接线，不属于本工具）。
"""
import json
import logging
from typing import Any, Dict, Optional

from app.tools.registry import ToolRegistry, tool
from app.tools.upload_tools import _resolve_session_id

logger = logging.getLogger(__name__)

# 内联载荷上限（与 geocompute 内联参数同一量级）；更大的数据走 POST /upload
_MAX_PAYLOAD_BYTES = 8 * 1024 * 1024
_MAX_DESCRIPTOR_FIELDS = 16


def _bounded_descriptor(descriptor: Any) -> Optional[Dict[str, Any]]:
    """RefDescriptor → 有界投影（不回传 field_schema 全量/载荷）。"""
    if not isinstance(descriptor, dict):
        return None
    out: Dict[str, Any] = {}
    for key in (
        "ref_id", "content_revision", "feature_count",
        "geometry_types", "bbox", "crs", "raster_capable",
    ):
        value = descriptor.get(key)
        if value is not None:
            out[key] = value
    field_schema = descriptor.get("field_schema")
    if isinstance(field_schema, dict):
        out["fields"] = sorted(str(k) for k in field_schema.keys())[:_MAX_DESCRIPTOR_FIELDS]
    return out or None


def register_ingest_tools(registry: ToolRegistry) -> None:
    """注册数据摄入工具集。"""

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="ingest_dataset",
        description=(
            "把一个内联 GeoJSON FeatureCollection 摄入当前会话：内容指纹去重 →"
            "有界画像 → 质量诊断 → 会话产物登记（账本可追溯、可复用）。"
            "返回 ref id、描述符、画像与质量摘要（有界，不含数据本体）；"
            "propose_repairs=true 时附修复提案（仅提案，不执行）。"
            "✅ 用于：Agent 自产/抓取的 GeoJSON 需要成为正式会话数据资产时。"
            "大文件请改用 POST /upload。"
        ),
        param_descriptions={
            "data": "GeoJSON FeatureCollection 对象（type=FeatureCollection）",
            "declared_crs": "声明的坐标系（如 EPSG:4326）；缺省则如实按未知处理，质量报告会提示",
            "name": "数据集显示名（可选）",
            "propose_repairs": "是否附修复提案（plan-only，不会执行任何修复）",
            "session_id": "会话 id（通常无需传入，由运行时注入）",
        },
    )
    async def ingest_dataset(
        data: dict,
        declared_crs: Optional[str] = None,
        name: str = "",
        propose_repairs: bool = False,
        session_id: Optional[str] = None,
    ) -> dict:
        from app.services.data_ingest.pipeline import get_ingest_pipeline
        from app.services.session_data import session_data_manager

        sid = _resolve_session_id(session_id)
        if not sid:
            return {
                "success": False,
                "code": "NO_SESSION",
                "error": "缺少有效会话（需要在会话上下文中调用，或显式传入归属会话）",
            }
        if not isinstance(data, dict):
            return {
                "success": False,
                "code": "INVALID_PAYLOAD",
                "error": "data 必须是 GeoJSON FeatureCollection 对象",
            }
        try:
            payload_bytes = len(
                json.dumps(data, default=str, ensure_ascii=False).encode("utf-8")
            )
        except (TypeError, ValueError):
            payload_bytes = -1
        if payload_bytes < 0 or payload_bytes > _MAX_PAYLOAD_BYTES:
            return {
                "success": False,
                "code": "PAYLOAD_TOO_LARGE",
                "error": (
                    f"内联载荷不可用（{payload_bytes} bytes > {_MAX_PAYLOAD_BYTES} 上限）。"
                    "大文件请通过 POST /upload 导入。"
                ),
            }

        result = await get_ingest_pipeline().ingest(
            sid,
            data,
            name=name or "ingested dataset",
            crs=declared_crs or "",
            source_type="tool",
        )
        if not result.ok:
            return {
                "success": False,
                "code": result.error_code or "INGEST_FAILED",
                "error": result.error or "ingest failed",
            }

        try:
            descriptor = await session_data_manager.get_ref_descriptor(sid, result.ref_id)
        except Exception:  # noqa: BLE001 — 描述符缺席按 None（与 store 契约一致）
            descriptor = None

        quality = result.quality_summary or {}
        issues = quality.get("issues") or []
        from app.services.data_ingest.repair_planning import REPAIRABLE_ISSUE_CODES

        repairable_count = sum(
            1 for i in issues if str(i.get("code", "")) in REPAIRABLE_ISSUE_CODES
        )
        out: Dict[str, Any] = {
            "success": True,
            "ref_id": result.ref_id,
            "duplicate": result.duplicate,
            "descriptor": _bounded_descriptor(descriptor),
            "profile": result.profile_summary,
            "quality": quality,
            "repairable_issue_count": repairable_count,
        }
        if propose_repairs:
            # plan-only：只映射诊断码 → 修复操作词表，绝不执行
            from app.services.data_ingest.repair_planning import (
                propose_repairs_for_issue_codes,
            )

            proposals = propose_repairs_for_issue_codes(
                [str(i.get("code", "")) for i in issues],
                fields={
                    str(i.get("code", "")): str(i.get("field", "") or "")
                    for i in issues
                },
            )
            out["repair_proposals"] = [p.to_bounded_dict() for p in proposals]
        return out
