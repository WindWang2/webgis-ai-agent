"""语义级组件 QA validator（semantics facet）— ADR-0081 / ADR-0091。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..contracts import (
    F_CRS_NOT_WGS84,
    F_SEMANTIC_LEGEND_MISSING,
    F_SEMANTIC_LEGEND_MISMATCH,
    F_TITLE_MISSING_REPORT,
    MapCompletionFinding,
    _spec_layers,
)

# legend_spec 判别值 → 图例族组件类型的确定性映射。与渲染侧同源：
# composer 按 MapModel 兼容性选型（visual_heatmap/raster_surface →
# continuous_colorbar、administrative_choropleth → legend、
# categorical_thematic → categorical_legend），divergent 复用连续渲染器
# （frontend legs/divergent-legend 注释明示）。此处只是同一语义的
# 完成期镜像 —— 不引入第二词表。
_LEGEND_KIND_TO_COMPONENT: Dict[str, str] = {
    "graduated": "legend",
    "categorical": "categorical_legend",
    "continuous": "continuous_colorbar",
    "divergent": "continuous_colorbar",
}


def _normalize_crs_for_wgs84(crs: str) -> str:
    """CRS 是否 WGS84 系（渲染等价）：EPSG:4326/WGS84/CRS84 与 CGCS2000
    （EPSG:4490 —— 同为经纬度地理坐标，中文地理数据标准）；OGC urn/URL
    形式归一化取尾随 EPSG 码再判。返回归一化码（等价）或空串（需披露）。"""
    normalized = crs.strip().upper().replace(" ", "")
    if normalized in ("EPSG:4326", "WGS84", "CRS84", "EPSG:4326(0)", "EPSG:4490"):
        return normalized
    # OGC 形式：urn:ogc:def:crs:EPSG::4326 / .../crs/EPSG/0/4326
    tail = normalized.rsplit(":", 1)[-1].rsplit("/", 1)[-1]
    if tail.isdigit() and int(tail) in (4326, 4490):
        return f"EPSG:{int(tail)}"
    return ""


def validate_semantics(
    chapter: Dict[str, Any],
    mapspec: Dict[str, Any],
    required_slots: List[List[str]],
    contract: Optional[Any] = None,
    records: Optional[Dict[str, Any]] = None,
) -> List[MapCompletionFinding]:
    """语义级组件 QA（ADR-0081 validator 族的完成期语义补全）。

    覆盖组合路径被绕过（组件手工增删）时槽位校验看不见的语义：

    1. **图例类型 ↔ 图层语义匹配**：绑定层的 legend_spec 判别值决定了
       唯一正确的图例族类型（continuous/divergent → colorbar、
       categorical → categorical_legend、graduated → legend）。绑定错型
       （如给热力层挂分级图例）→ ``semantic_legend_mismatch``（warning，
       无自动修复 —— 换型是 agent 级决策，修复面不越界）。
    2. **必需图例的主题层覆盖**：facet contract 判定图例族为产品必需
       （density_map 的 colorbar 槽）而图例族在场却未覆盖某主题层 →
       ``semantic_legend_missing``（warning）。图例族整体缺席时由槽位级
       ``component_missing`` 覆盖（不重复披露）。
    3. **报告产品标题**：intent.report_product 且无 enabled title、且
       title 不在 required 槽（在则槽位级已覆盖）→
       ``title_missing_report_product``（warning）。
    4. **CRS 契约**：ArtifactRecord.crs 在场且非 WGS84（MapSpec 渲染
       契约恒为 EPSG:4326；非 4326 记录意味着单位/叠加泄漏风险）→
       ``crs_not_wgs84``（warning；crs 未知 ≠ 错，不虚构判定）。
    """
    findings: List[MapCompletionFinding] = []
    layers = _spec_layers(mapspec)
    components = [
        c
        for c in ((mapspec.get("layout") or {}).get("components") or [])
        if isinstance(c, dict)
    ]
    enabled = [c for c in components if c.get("enabled") is not False]

    thematic: Dict[str, str] = {}  # layer_id -> legend_spec 判别值
    for ly in layers:
        spec = ly.get("legend_spec")
        kind = str((spec or {}).get("type") or "") if isinstance(spec, dict) else ""
        if kind in _LEGEND_KIND_TO_COMPONENT:
            thematic[str(ly.get("id") or "")] = kind

    # 1) 类型匹配（只判绑定了主题层的图例族实例；词表单源于
    #    product_facets.LEGEND_FAMILY —— 不建第三份字面量）
    from app.services.gis_harness.product_facets import LEGEND_FAMILY as legend_family
    for c in enabled:
        ctype = str(c.get("type") or "")
        if ctype not in legend_family:
            continue
        lid = str((c.get("options") or {}).get("layerId") or "")
        kind = thematic.get(lid)
        if kind is None:
            continue  # 未绑定/绑定层无 legend_spec：无输入，不判
        expected = _LEGEND_KIND_TO_COMPONENT[kind]
        if ctype != expected:
            findings.append(
                MapCompletionFinding(
                    code=F_SEMANTIC_LEGEND_MISMATCH,
                    severity="warning",
                    target=str(c.get("id") or ctype),
                    detail=(
                        f"layer '{lid[:48]}' legend_spec is {kind} — expects "
                        f"{expected}, got {ctype}"
                    ),
                )
            )

    # 2) 必需图例的主题层覆盖（契约驱动；图例族整体缺席交给槽位级）
    legend_required = bool(getattr(contract, "legend_required", False))
    if legend_required and thematic:
        any_legend = any(str(c.get("type") or "") in legend_family for c in enabled)
        if any_legend:
            covered = {
                str((c.get("options") or {}).get("layerId") or "")
                for c in enabled
                if str(c.get("type") or "") in legend_family
            }
            # 未绑定 layerId 的图例（HUD 发现语义）按渲染现实覆盖全部主题层
            # —— 不为它制造 per-layer 欠账噪声。
            if any(
                not str((c.get("options") or {}).get("layerId") or "")
                for c in enabled
                if str(c.get("type") or "") in legend_family
            ):
                covered = set(thematic) | set(covered)
            for lid in thematic:
                if lid not in covered:
                    findings.append(
                        MapCompletionFinding(
                            code=F_SEMANTIC_LEGEND_MISSING,
                            severity="warning",
                            target=lid,
                            detail=(
                                f"legend family required by product contract but "
                                f"thematic layer '{lid[:48]}' has no bound legend"
                            ),
                        )
                    )

    # 3) 报告产品标题（槽位级 required 已覆盖时跳过）
    intent = chapter.get("intent")
    if (
        isinstance(intent, dict)
        and intent.get("report_product")
        and not any(str(c.get("type") or "") == "title" for c in enabled)
        and not any("title" in [t for t in fam if t] for fam in required_slots)
    ):
        findings.append(
            MapCompletionFinding(
                code=F_TITLE_MISSING_REPORT,
                severity="warning",
                target="title",
                detail="report product without an enabled title component",
            )
        )

    # 4) CRS 契约（记录快照在场才有输入；MapSpec 渲染契约恒为 WGS84）
    if records:
        spec_refs = {
            str((ly.get("provenance") or {}).get("result_ref") or "")
            for ly in layers
        } | {
            str((ly.get("provenance") or {}).get("source_ref") or "")
            for ly in layers
        }
        for ref, record in records.items():
            crs = str(getattr(record, "crs", "") or "")
            if not crs:
                continue  # 未知 ≠ 错
            normalized = _normalize_crs_for_wgs84(crs)
            if normalized:
                continue
            if ref in spec_refs:
                findings.append(
                    MapCompletionFinding(
                        code=F_CRS_NOT_WGS84,
                        severity="warning",
                        target=ref,
                        detail=(
                            f"artifact crs {crs[:24]} is not WGS84 while MapSpec "
                            f"rendering assumes EPSG:4326 — verify overlay units"
                        ),
                    )
                )

    return findings
