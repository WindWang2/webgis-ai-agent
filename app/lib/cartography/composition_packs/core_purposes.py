"""Core Purposes Pack — F11 四大地图目的的显式组织（ADR-0214 D8）.

F11 目标要求至少一组高质量 composition packs 覆盖四类地图目的。其中
basic_thematic / heat_distribution_stats / change_comparison 已由 seed 与
既有域包语义覆盖（由 ``component_presets.PURPOSE_PRESETS`` 以引用组织，
不堆重复模板）；本包只补**真缺口**：通用分类专题图（非遥感域的
categorical/landuse/zoning 语义，此前仅 rs_classification 覆盖遥感分类）。

同时导出配对的 versioned 组合契约 ``CORE_PURPOSE_CONTRACTS``
（``composition_contract.ContractRegistry.load_builtins`` 在 seed 之后
确定性载入 —— 与本包模板被 composition 注册表消费的既有模式同构）。

红线：不改 seed id/priority/fallback；本包模板 priority=46，不得改变
既有 map model 的默认模板选择（golden corpus 守护）。
"""
from __future__ import annotations

from typing import Tuple

from app.lib.cartography.composition_contract import (
    CompositionContractV1,
    ContractLink,
    ContractSlot,
)
from app.lib.cartography.composition_packs._base import (
    attribution_slot,
    charts_slot,
    composition,
    export_slot,
    legend_slot,
    map_border_slot,
    north_arrow_slot,
    scale_bar_slot,
    stats_slot,
    subtitle_slot,
    title_slot,
)

CLASSIFIED_MODELS = [
    "categorical_thematic",
    "classification_result_map",
    "classified_raster",
    "landform_classification_map",
    "zoning_planning",
]

CORE_PURPOSES_PACK = [
    composition(
        cid="composition.classified_categorical",
        name="Classified Categorical Map",
        description=(
            "通用分类专题图：分类图例主绑定（categorical_legend）+ 标题/指北针/"
            "比例尺/归属必备，统计与图表可选。适用于土地利用/区划/地貌分类/"
            "分类结果等类别语义图层（非遥感域专用）。"),
        models=CLASSIFIED_MODELS,
        outputs=["interactive", "png", "pdf"],
        slots=[
            title_slot(),
            subtitle_slot(),
            legend_slot(
                ["categorical_legend", "legend"],
                cardinality="required", required=True, max_count=2),
            north_arrow_slot(),
            scale_bar_slot(),
            attribution_slot(),
            stats_slot(),
            charts_slot(),
            map_border_slot(),
            export_slot(),
        ],
        profile="standard",
        fallback="composition.standard_analysis",
        priority=46,
        tags=["core-purpose", "categorical", "f11"],
    ),
]

#: classified_categorical 的配对契约（versioned 绑定；图例槽位锁定分类
#: 图例优先 —— 契约覆写 preferred_template 演示 override 通道）。
CLASSIFIED_CATEGORICAL_CONTRACT = CompositionContractV1(
    contract_id="contract.core.classified_categorical",
    template_id="composition.classified_categorical",
    purpose="classified_categorical",
    slots=(
        ContractSlot(slot_id="title"),
        ContractSlot(slot_id="subtitle"),
        ContractSlot(
            slot_id="legend", preferred_template="categorical-legend/academic"),
        ContractSlot(slot_id="north_arrow"),
        ContractSlot(slot_id="scale_bar"),
        ContractSlot(slot_id="attribution"),
        ContractSlot(slot_id="statistics_panel"),
        ContractSlot(slot_id="chart_panel"),
        ContractSlot(slot_id="map_border"),
        ContractSlot(slot_id="export_layout"),
    ),
    links=(ContractLink(src_slot="subtitle", dst_slot="title", type="annotates"),),
    style_token_preset="screen",
    export_targets=("interactive", "png", "pdf"),
    compatible_map_models=tuple(CLASSIFIED_MODELS),
    description="分类专题图契约：categorical_legend 主绑定 + publication 完整版式。",
)

CORE_PURPOSE_CONTRACTS: Tuple[CompositionContractV1, ...] = (
    CLASSIFIED_CATEGORICAL_CONTRACT,
)

__all__ = [
    "CORE_PURPOSES_PACK",
    "CORE_PURPOSE_CONTRACTS",
    "CLASSIFIED_CATEGORICAL_CONTRACT",
    "CLASSIFIED_MODELS",
]
