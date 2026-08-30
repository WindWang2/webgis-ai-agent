"""Product Action Advisor — 从欠账 facet 确定性推导下一步 GIS action（P9）。

纯函数 / 只读 / 零 LLM / 零 IO（P9 §18）。它**不是 agent loop**（P9 §20）：

- 不自己调 tool、不重跑算法、不推进任何状态 —— Pi 仍是唯一 Agent Host，
  执行仍走既有 Pi + harness（SessionPlan 行状态 → Capability →
  Algorithm Resolver → Tool）；
- 输出只是"欠哪个 facet、经由哪个 capability 补"的有界建议，供
  [GIS Plan] 投影行与测试消费；无 capability 映射时如实留空
  （chart 等产出通道是 harness 工具族而非 registry capability ——
  不 shortcut 到具体 tool id 冒充 capability，P9 §19）。

确定性优先级（同输入必同输出）：

    1. execution blocked（failed/unavailable 行）→ retry_analysis
    2. pending analysis（DAG 欠执行）→ run_analysis
    3. pending map_layer（计划层未落 MapSpec）→ produce_layer
    4. needs_repair map_layer（渲染级缺席）→ repair_layer_render
    5. pending chart（required）→ produce_chart
    6. pending/needs_repair statistics → produce_statistics
    7. pending narrative（map_product 未 complete）→ finalize_product
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from app.services.gis_harness.product_graph import (
    FS_FAILED,
    FS_NEEDS_REPAIR,
    FS_PENDING,
    KIND_ANALYSIS,
    KIND_CHART,
    KIND_MAP_LAYER,
    KIND_NARRATIVE,
    KIND_STATISTICS,
    ProductFacetCompletion,
)

# 建议 action 词表（有限集合；capability 缺省 "" 表示无 registry 映射）
ACTION_RETRY_ANALYSIS = "retry_analysis"
ACTION_RUN_ANALYSIS = "run_analysis"
ACTION_PRODUCE_LAYER = "produce_layer"
ACTION_REPAIR_LAYER_RENDER = "repair_layer_render"
ACTION_PRODUCE_CHART = "produce_chart"
ACTION_PRODUCE_STATISTICS = "produce_statistics"
ACTION_FINALIZE_PRODUCT = "finalize_product"


@dataclass
class ProductActionRecommendation:
    """确定性下一产品动作建议（bounded / serializable）。"""

    facet_id: str
    kind: str
    action: str
    capability: str = ""  # registry capability id；无映射 → ""（不虚构）
    reason: str = ""
    inputs: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "facet_id": self.facet_id,
            "kind": self.kind,
            "action": self.action,
            "capability": self.capability[:64],
            "reason": self.reason[:120],
        }

    def projection_line(self) -> str:
        """单行有界投影（进 [GIS Plan] 块尾部，P9 §17）。"""
        target = self.capability or f"{self.kind}:{self.action}"
        return f"[Next GIS Action] {target}"


def advise_next_product_action(
    chapter: Optional[dict],
    facets: List[ProductFacetCompletion],
) -> Optional[ProductActionRecommendation]:
    """欠账 facets → 下一步建议（确定性、只读；无欠账 → None）。"""
    if not facets:
        return None

    # 1) execution blocked：failed/unavailable 行优先（DAG 不会自愈）
    for f in facets:
        if f.kind == KIND_ANALYSIS and f.status == FS_FAILED:
            return ProductActionRecommendation(
                facet_id=f.facet_id,
                kind=f.kind,
                action=ACTION_RETRY_ANALYSIS,
                capability=f.capability_ids[0] if f.capability_ids else "",
                reason=f"analysis '{f.key}' failed — retry or replan owed",
            )

    # 2) pending analysis：DAG 欠执行（capability 精确映射）
    for f in facets:
        if f.kind == KIND_ANALYSIS and f.status == FS_PENDING:
            return ProductActionRecommendation(
                facet_id=f.facet_id,
                kind=f.kind,
                action=ACTION_RUN_ANALYSIS,
                capability=f.capability_ids[0] if f.capability_ids else "",
                reason=f"analysis '{f.key}' pending — run capability",
            )

    # 3) pending map_layer：计划层未落 MapSpec（capability = 层的数据源能力）
    for f in facets:
        if f.kind == KIND_MAP_LAYER and f.status == FS_PENDING:
            return ProductActionRecommendation(
                facet_id=f.facet_id,
                kind=f.kind,
                action=ACTION_PRODUCE_LAYER,
                capability=f.capability_ids[0] if f.capability_ids else "",
                reason=f"planned layer '{f.key}' not in MapSpec — produce via capability",
            )

    # 4) needs_repair map_layer：desired state 在场而渲染缺席 —— 无
    # capability 映射（runtime 缺口），披露为 render 修复建议
    for f in facets:
        if f.kind == KIND_MAP_LAYER and f.status == FS_NEEDS_REPAIR:
            return ProductActionRecommendation(
                facet_id=f.facet_id,
                kind=f.kind,
                action=ACTION_REPAIR_LAYER_RENDER,
                capability="",
                reason=f"layer '{f.key}' not rendered — re-observation after re-render",
            )

    # 5) chart facet owed（required 且缺席/禁用）
    for f in facets:
        if f.kind == KIND_CHART and f.status in (FS_PENDING, FS_NEEDS_REPAIR):
            return ProductActionRecommendation(
                facet_id=f.facet_id,
                kind=f.kind,
                action=ACTION_PRODUCE_CHART,
                capability="",
                reason="chart facet owed — produce chart data and attach chart_panel",
            )

    # 6) statistics facet owed
    for f in facets:
        if f.kind == KIND_STATISTICS and f.status in (FS_PENDING, FS_NEEDS_REPAIR):
            return ProductActionRecommendation(
                facet_id=f.facet_id,
                kind=f.kind,
                action=ACTION_PRODUCE_STATISTICS,
                capability=f.capability_ids[0] if f.capability_ids else "",
                reason=f"statistics facet '{f.key}' owed",
            )

    # 7) narrative：map_product 未 complete（完成块是其代理）
    for f in facets:
        if f.kind == KIND_NARRATIVE and f.status == FS_PENDING:
            return ProductActionRecommendation(
                facet_id=f.facet_id,
                kind=f.kind,
                action=ACTION_FINALIZE_PRODUCT,
                capability="",
                reason="product facets present — map product not finalized",
            )

    return None


def next_action_projection(
    chapter: Optional[dict],
    facets: List[ProductFacetCompletion],
) -> str:
    """[GIS Plan] 尾部的单行建议（无欠账 → 空串，零噪声）。"""
    rec = advise_next_product_action(chapter, facets)
    return rec.projection_line() if rec else ""
