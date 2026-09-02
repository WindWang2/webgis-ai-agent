"""Product Facet Contract —— 产品类型级的 facet 必需性契约（派生投影）。

ADR-0085/0087 把 facet 完成度建立为派生投影，但「哪些 facet 是产品**应然**
构成」此前只有两条启发式：chart 走 ``template_selection.export_profile.chart``
（而真实 planner 路径从未把 recipe.export_profile 写进 template_selection ——
该信号实际处于断线状态，仅测试手工注入时生效），legend/inset 一律
informational —— 即使组合模板把 colorbar 声明为 required 槽位。

本模块把必需性收敛为单一确定性推导：

    intent.task（产品类型词表）
      + recipe.export_profile（chart 必需信号 —— 数据面）
      + composition template required 槽位（图例族必需信号 —— 版面面）
        → ProductFacetContract（required/optional 组件类型集合）

不变式：

- **派生只读、零持久化**（与 ProductGraph 同族）：同章节必同契约，
  复算即得；不产生第二真相（ADR-0076）；
- 输入全部是既有事实（章节 recipe_id / template_selection / in-process
  registry），零 IO、零 LLM；
- **不替代** composition validation（``validate_component_composition``
  仍是槽位语义的裁决者）—— 本契约只回答「产品图视角下，某类 facet 缺席
  是否欠账」，供 ProductGraph 合成 pending 节点与 facet required 字段消费；
- 保守诚实：recipe/模板不可解析 → 空契约（不虚构 required）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# 图例族（与 component catalog 的 legend 家族同词表）
LEGEND_FAMILY: Tuple[str, ...] = ("legend", "categorical_legend", "continuous_colorbar")

_MAX_SOURCES = 4


@dataclass(frozen=True)
class ProductFacetContract:
    """产品类型 → facet 必需性（bounded / serializable / derived）。"""

    product_type: str = ""            # intent.task 词表（10 值）或空
    recipe_id: str = ""
    composition_template_id: str = ""
    # 组合模板 required 槽位允许的组件类型（chart 信号并入 chart_required）
    required_component_types: Tuple[str, ...] = ()
    optional_component_types: Tuple[str, ...] = ()
    chart_required: bool = False      # recipe export_profile 或 required 槽位
    legend_required: bool = False     # required 槽位覆盖图例族
    # 决策来源（有界，审计/测试用；如 "composition.density_map:colorbar")
    sources: Tuple[str, ...] = field(default=())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "product_type": self.product_type,
            "recipe_id": self.recipe_id,
            "composition_template_id": self.composition_template_id,
            "required_component_types": list(self.required_component_types[:12]),
            "optional_component_types": list(self.optional_component_types[:12]),
            "chart_required": self.chart_required,
            "legend_required": self.legend_required,
            "sources": list(self.sources[:_MAX_SOURCES]),
        }


EMPTY_FACET_CONTRACT = ProductFacetContract()


def _slot_required(slot: Any) -> bool:
    """槽位必需性（cardinality=required 或显式 required=True）。"""
    if getattr(slot, "required", None) is True:
        return True
    return str(getattr(slot, "cardinality", "") or "") == "required"


def derive_facet_contract(
    chapter: Optional[Dict[str, Any]],
) -> ProductFacetContract:
    """章节（计划真相的 gis_chapter 投影）→ facet 契约（纯函数）。

    输入容错：recipe / composition template 不可解析、章节缺键 → 对应信号
    缺席（空契约或部分契约），绝不虚构 required。
    """
    if not isinstance(chapter, dict):
        return EMPTY_FACET_CONTRACT

    intent = chapter.get("intent")
    product_type = str(
        (intent or {}).get("task") if isinstance(intent, dict) else ""
    ) or ""
    recipe_id = str(chapter.get("recipe_id") or "")
    template_selection = chapter.get("template_selection")
    if not isinstance(template_selection, dict):
        template_selection = {}
    composition_template_id = str(template_selection.get("composition_template_id") or "")

    sources: List[str] = []
    required_types: List[str] = []
    optional_types: List[str] = []
    chart_required = False
    legend_required = False

    # 1) 组合模板 required 槽位（版面真相；density_map 的 colorbar、
    #    statistical_map 的 legend 在此声明为产品必需构成）。
    if composition_template_id:
        try:
            from app.lib.cartography.composition_templates import (
                get_composition_template_registry,
            )

            compo = get_composition_template_registry().get(composition_template_id)
        except Exception:  # noqa: BLE001 — 投影失败退化为部分契约
            compo = None
        if compo is not None:
            for slot in getattr(compo, "component_slots", None) or []:
                types = [str(t) for t in (getattr(slot, "allowed_component_types", None) or [])]
                if _slot_required(slot):
                    required_types.extend(t for t in types if t not in required_types)
                    if any(t in LEGEND_FAMILY for t in types):
                        legend_required = True
                        sources.append(f"{compo.id}:{slot.id}:required")
                    if "chart_panel" in types:
                        chart_required = True
                else:
                    optional_types.extend(t for t in types if t not in optional_types)

    # 2) recipe export_profile（数据真相：chart 是否产品输出）。
    #    template_selection.export_profile（旧会话手工注入的键）作为兼容
    #    读面保留 —— 两者任一为真即 chart_required。
    if recipe_id:
        try:
            from app.services.gis_harness.recipes import get_recipe_registry

            recipe = get_recipe_registry().get(recipe_id)
        except Exception:  # noqa: BLE001 — 同上，退化不中断
            recipe = None
        if recipe is not None:
            profile = getattr(recipe, "export_profile", None)
            if isinstance(profile, dict) and profile.get("chart") is True:
                chart_required = True
                sources.append(f"recipe.{recipe_id}:export_profile.chart")

    legacy_profile = template_selection.get("export_profile")
    if isinstance(legacy_profile, dict) and legacy_profile.get("chart") is True:
        chart_required = True
        if not any(s.startswith("recipe.") for s in sources):
            sources.append("template_selection.export_profile.chart")

    # 3) 意图信号（ADR-0092 G7）：查询显式要求图表（output_intents 含
    #    "chart" 或计划 charts 非空）时 chart 是产品应然构成 —— 用户点名
    #    要的 facet 不因 recipe 未声明而缺席。charts/output_intents 是既有
    #    计划事实（非第二真相），此处只是把它们接进契约推导。
    if not chart_required:
        output_intents = (intent or {}).get("output_intents") if isinstance(intent, dict) else None
        if (
            (isinstance(output_intents, list) and "chart" in output_intents)
            or chapter.get("charts")
        ):
            chart_required = True
            sources.append("intent.output_intents.chart")

    return ProductFacetContract(
        product_type=product_type,
        recipe_id=recipe_id,
        composition_template_id=composition_template_id,
        required_component_types=tuple(required_types[:16]),
        optional_component_types=tuple(optional_types[:16]),
        chart_required=chart_required,
        legend_required=legend_required,
        sources=tuple(sources[:_MAX_SOURCES]),
    )


__all__ = [
    "ProductFacetContract",
    "EMPTY_FACET_CONTRACT",
    "LEGEND_FAMILY",
    "derive_facet_contract",
]
