"""ExecutionCatalog 只读发现工具面（F07）。

2 个 tier-2 只读工具，全部以 :mod:`app.lib.gis.execution_catalog` 为界
（零副作用、确定性、有界投影）：

- ``catalog_discover``：capability-first 候选发现 —— 按 capability + 数据
  描述 + situation + resource envelope 查询有界候选链（capability →
  algorithm → tool），**替代 prompt 工具名字面量与全 registry 泄洪**；
  每个候选携带 reason codes 与证据，被排除的候选只以计数披露。
- ``catalog_lookup``：单条目有界详情（身份/约束/降级-替代/认证证据），
  供 agent 复核某个候选的契约面。

边界：本模块不解析 provider 运行态（在线判定用声明面 ``network``），
运行期 provider 选择权威仍是 ``capability_resolution``；不修改 Pi schema、
不新增注册中心（与 skill_library_tools 同款 ``_TOOL_MODULES`` 一行注册
模式）。
"""
from __future__ import annotations

from typing import Any, Dict

from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool

_ALLOWED_GEOMETRY = ("point", "line", "polygon", "raster", "table", "unknown")
_ALLOWED_CRS = ("geographic", "projected", "unknown", "")
_ALLOWED_CLASSES = ("", "fast", "medium", "slow", "light", "heavy")


class CatalogDiscoverArgs(BaseModel):
    capabilities: str = Field(
        ..., description="逗号分隔的 capability id（1-8 个；如 admin_aggregation）")
    geometry: str = Field(
        "", description="数据几何类别 point/line/polygon/raster/table/unknown（可选）")
    approx_features: int = Field(
        0, description="数据要素数量级估计（0/缺省 = 不判规模）")
    crs_class: str = Field(
        "", description="数据 CRS 类别 geographic/projected/unknown（可选）")
    offline_required: bool = Field(
        False, description="situation：是否必须离线可执行")
    allow_destructive: bool = Field(
        False, description="situation：是否允许破坏性/需确认工具")
    max_latency_class: str = Field(
        "", description="资源上限：fast/medium/slow（空 = 不限）")
    max_memory_class: str = Field(
        "", description="资源上限：light/medium/heavy（空 = 不限）")
    limit: int = Field(5, description="返回候选上限（1-16；默认 5）")


class CatalogLookupArgs(BaseModel):
    kind: str = Field(..., description="条目类型 capability/algorithm/tool/recipe")
    entry_id: str = Field(..., description="条目 id")


def _get_catalog(refresh: bool = False):
    from app.lib.gis.execution_catalog import get_execution_catalog

    return get_execution_catalog(refresh=refresh)


def register_catalog_discovery_tools(registry: ToolRegistry):
    """注册 ExecutionCatalog 只读工具（tier 2：查询廉价、无副作用）。"""

    @tool(
        registry,
        tier=2,
        domains=["meta"],
        name="catalog_discover",
        description=(
            "执行能力发现（capability-first，确定性）。按 capability + 数据描述 + "
            "处境（离线/破坏性许可）+ 资源上限查询有界候选链"
            "（capability → algorithm → tool），每条带原因码与证据。"
            "\n何时用：需要为某个能力找可执行工具时——不要猜工具名，先查目录。"
            "被排除的候选只以计数+原因披露（不泄洪元数据）。"
        ),
        args_model=CatalogDiscoverArgs,
        side_effect="pure", deterministic=True,
        latency_class="fast", memory_class="light", scale_class="small",
        tags=("catalog", "capability", "discovery", "工具发现", "能力", "目录"),
        capabilities=["plan_workflow_orchestration"],
        output_semantic_type="object", result_size_policy="bounded",
    )
    def catalog_discover(args: CatalogDiscoverArgs) -> Dict[str, Any]:
        from app.lib.gis.execution_catalog_discovery import (
            MAX_LIMIT,
            DiscoveryQuery,
            discover,
        )

        caps = [c.strip() for c in str(args.capabilities).split(",") if c.strip()]
        if not caps:
            return {"error": "capabilities_required",
                    "detail": "provide comma-separated capability ids"}
        limit = max(1, min(int(args.limit), MAX_LIMIT))
        geometry = args.geometry.strip().lower()
        if geometry and geometry not in _ALLOWED_GEOMETRY:
            geometry = ""
        crs = args.crs_class.strip().lower()
        if crs not in _ALLOWED_CRS:
            crs = ""
        lat = args.max_latency_class.strip().lower()
        mem = args.max_memory_class.strip().lower()
        if lat not in _ALLOWED_CLASSES:
            lat = ""
        if mem not in _ALLOWED_CLASSES:
            mem = ""
        try:
            query = DiscoveryQuery(
                capabilities=caps,
                geometry=geometry,
                approx_features=max(0, int(args.approx_features or 0)),
                crs_class=crs,
                offline_required=bool(args.offline_required),
                allow_destructive=bool(args.allow_destructive),
                max_latency_class=lat,
                max_memory_class=mem,
                limit=limit,
            )
        except ValueError as exc:
            return {"error": "invalid_query", "detail": str(exc)[:200]}
        catalog = _get_catalog()
        payload = discover(catalog, query).to_dict()
        payload["catalog_generation_fingerprint"] = \
            catalog.generation_fingerprint
        return payload

    @tool(
        registry,
        tier=2,
        domains=["meta"],
        name="catalog_lookup",
        description=(
            "执行目录单条目详情（有界只读投影）。返回条目的身份/语义约束/"
            "资源分级/降级与替代目标/认证证据。"
            "\n何时用：catalog_discover 命中候选后，复核某个条目的契约面"
            "（CRS/单位/副作用/弃用关系）。"
        ),
        args_model=CatalogLookupArgs,
        side_effect="pure", deterministic=True,
        latency_class="fast", memory_class="light", scale_class="small",
        tags=("catalog", "lookup", "条目", "契约", "目录"),
        capabilities=["plan_workflow_orchestration"],
        output_semantic_type="object", result_size_policy="bounded",
    )
    def catalog_lookup(args: CatalogLookupArgs) -> Dict[str, Any]:
        from app.lib.gis.execution_catalog import CATALOG_KINDS

        kind = str(args.kind).strip().lower()
        if kind not in CATALOG_KINDS:
            return {"error": "unknown_kind",
                    "detail": f"kind must be one of {list(CATALOG_KINDS)}"}
        catalog = _get_catalog()
        entry = catalog.get(kind, str(args.entry_id).strip())
        if entry is None:
            return {"error": "entry_not_found",
                    "kind": kind, "entry_id": str(args.entry_id)[:128]}
        payload: Dict[str, Any] = dict(sorted(entry.fingerprint_payload().items()))
        payload["certification"] = dict(sorted(entry.certification.items()))
        payload["superseded_by"] = entry.superseded_by
        payload["fallback_targets"] = list(entry.fallback_targets)
        return payload


__all__ = ["register_catalog_discovery_tools"]
