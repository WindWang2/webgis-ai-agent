"""Purpose Presets — 地图目的 → 组合预设的统一引用面（ADR-0214 D7）.

F11 四大地图目的（basic_thematic / heat_distribution_stats /
classified_categorical / change_comparison）各一份 ``PurposeBundle``。
**引用表，不是第四处真值**：bundle 只引用

- composition 模板 id（⊆ CompositionTemplateRegistry，槽位语义单一事实）；
- versioned 契约 id（⊆ ContractRegistry，绑定/锁/tokens 单一事实）；
- slot 级组件模板预设（⊆ ComponentTemplateRegistry，variant/default_options
  单一事实）—— 只在**确有增量语义**的槽位登记（如密度色条横排、分类
  图例学术式），其余留空 = 沿模板槽位 preferred_templates / descriptor
  默认；
- style token preset（⊆ style_tokens.STYLE_PRESET_IDS）。

``validate_purpose_presets`` fail-closed：全部引用必须可解析，slot 预设
的 key 必须是被引模板的真实槽位、value 必须 native 且类型 ∈ 槽位允许
清单；bundle 与契约的模板锚定/purpose/tokens 不一致即 issue（测试锁）。

纯函数、确定性（purpose 字典序）、有界载荷（``purpose_bundles_bounded``）；
本模块不做选择裁决（那是 resolver/planner 的职责）、不改 MapSpec。
"""
from __future__ import annotations

from typing import Any, Dict, List

from pydantic import BaseModel

#: 单 bundle 的 slot 预设条数封顶（有界载荷纪律）。
_MAX_SLOT_PRESETS = 8


class PurposeBundle(BaseModel):
    """一个地图目的的组合预设（全部是引用 id，不复制定义）。"""

    purpose: str
    composition_template_id: str
    contract_id: str = ""
    #: slot_id → component template id（⊆ 槽位 allowed_component_types）。
    slot_presets: Dict[str, str] = {}
    style_token_preset: str = ""       # ⊆ style_tokens.STYLE_PRESET_IDS
    description: str = ""


#: F11 四大地图目的 → 组合预设（purpose 键与契约 ``purpose`` 字段对齐；
#: 新增条目必须过 ``validate_purpose_presets`` fail-closed 校验）。
PURPOSE_PRESETS: Dict[str, PurposeBundle] = {
    "basic_thematic": PurposeBundle(
        purpose="basic_thematic",
        composition_template_id="composition.standard_analysis",
        contract_id="contract.core.basic_thematic",
        slot_presets={},
        style_token_preset="screen",
        description=(
            "基础专题图：标准分析版式 + screen tokens；槽位沿模板/契约"
            "默认（无额外组件预设）。"),
    ),
    "heat_distribution_stats": PurposeBundle(
        purpose="heat_distribution_stats",
        composition_template_id="composition.density_map",
        contract_id="contract.core.heat_distribution_stats",
        slot_presets={"colorbar": "colorbar/horizontal"},
        style_token_preset="screen",
        description=(
            "热力/点分布+统计：密度图版式；色条横排预设（与模板槽位"
            "偏好一致）。"),
    ),
    "classified_categorical": PurposeBundle(
        purpose="classified_categorical",
        composition_template_id="composition.classified_categorical",
        contract_id="contract.core.classified_categorical",
        slot_presets={
            "legend": "categorical-legend/academic",
            "title": "title/academic",
        },
        style_token_preset="screen",
        description=(
            "分类专题图（土地利用/区划/分类结果等类别语义）：分类图例"
            "主绑定，学术图例与标题预设。"),
    ),
    "change_comparison": PurposeBundle(
        purpose="change_comparison",
        composition_template_id="composition.temporal_change_report",
        contract_id="contract.core.change_comparison",
        slot_presets={
            "title": "title/report",
            "legend": "legend/report",
            "export_layout": "export-layout/A4-portrait",
        },
        style_token_preset="publication",
        description=(
            "变化对比图：时相变化报告版式 + publication tokens；报告"
            "族标题/图例/版面预设（与模板槽位偏好一致）。"),
    ),
}


# ── fail-closed 校验（引用完整性；返回 issue 字符串列表）────────────────


def validate_purpose_presets() -> List[str]:
    """purpose 预设表 ↔ 各注册表交叉审计（fail-closed）。

    - composition / contract / component template id 必须可解析；
    - bundle 与契约的模板锚定、purpose、tokens 必须一致（引用不撒谎）；
    - slot_presets key 必须是被引 composition 模板的真实槽位；
    - slot_presets value 必须 native 且 component_type ∈ 槽位允许清单。
    """
    issues: List[str] = []
    try:
        from app.lib.cartography.composition_templates import (
            get_composition_template_registry,
        )
        from app.lib.cartography.component_templates import (
            get_component_template_registry,
        )
        from app.lib.cartography.composition_contract import get_contract_registry
        from app.lib.cartography.style_tokens import STYLE_PRESET_IDS

        compo_reg = get_composition_template_registry()
        tmpl_reg = get_component_template_registry()
        contract_reg = get_contract_registry()
        for purpose in sorted(PURPOSE_PRESETS):
            bundle = PURPOSE_PRESETS[purpose]
            tag = f"purpose {purpose}"
            if bundle.purpose != purpose:
                issues.append(f"{tag}: bundle.purpose '{bundle.purpose}' 与键不一致")
            tpl = compo_reg.get(bundle.composition_template_id)
            if tpl is None:
                issues.append(
                    f"{tag}: composition {bundle.composition_template_id} 未注册")
            contract = (
                contract_reg.get(bundle.contract_id) if bundle.contract_id else None)
            if contract is None:
                issues.append(f"{tag}: contract {bundle.contract_id} 未注册")
            else:
                if contract.template_id != bundle.composition_template_id:
                    issues.append(
                        f"{tag}: 契约锚定模板 {contract.template_id} != "
                        f"bundle 模板 {bundle.composition_template_id}")
                if contract.purpose != purpose:
                    issues.append(
                        f"{tag}: 契约 purpose '{contract.purpose}' 与键不一致")
                if (bundle.style_token_preset and contract.style_token_preset
                        and bundle.style_token_preset != contract.style_token_preset):
                    issues.append(
                        f"{tag}: style_token_preset 与契约 "
                        f"({contract.style_token_preset}) 不一致")
            if bundle.style_token_preset and \
                    bundle.style_token_preset not in STYLE_PRESET_IDS:
                issues.append(
                    f"{tag}: style_token_preset {bundle.style_token_preset} 不在词表")
            if len(bundle.slot_presets) > _MAX_SLOT_PRESETS:
                issues.append(f"{tag}: slot_presets 超过 {_MAX_SLOT_PRESETS} 上限")
            slots = (
                {s.id: s for s in tpl.component_slots} if tpl is not None else {})
            for slot_id in sorted(bundle.slot_presets):
                slot = slots.get(slot_id)
                if slot is None:
                    issues.append(
                        f"{tag}: slot {slot_id} 不在模板 "
                        f"{bundle.composition_template_id} 中")
                    continue
                preset_id = bundle.slot_presets[slot_id]
                ct = tmpl_reg.get(preset_id)
                if ct is None:
                    issues.append(f"{tag}: component template {preset_id} 未注册")
                    continue
                if ct.runtime_status != "native":
                    issues.append(f"{tag}: component template {preset_id} 非 native")
                if ct.component_type not in slot.allowed_component_types:
                    issues.append(
                        f"{tag}: {preset_id} 类型 {ct.component_type} 不在槽位 "
                        f"{slot_id} 允许清单")
    except Exception as e:  # pragma: no cover - 防御性
        issues.append(f"purpose preset validation error: {e}")
    return issues


# ── 有界载荷（agent 工具消费面）─────────────────────────────────────────


def purpose_bundles_bounded() -> List[Dict[str, Any]]:
    """确定性（purpose 字典序）有界投影：id 截断、slot 预设封顶。"""
    out: List[Dict[str, Any]] = []
    for purpose in sorted(PURPOSE_PRESETS):
        bundle = PURPOSE_PRESETS[purpose]
        out.append({
            "purpose": bundle.purpose[:32],
            "composition_template_id": bundle.composition_template_id[:48],
            "contract_id": bundle.contract_id[:48],
            "slot_presets": dict(sorted(
                (k[:32], v[:48])
                for k, v in list(bundle.slot_presets.items())[:_MAX_SLOT_PRESETS]
            )),
            "style_token_preset": bundle.style_token_preset[:16],
            "description": bundle.description[:160],
        })
    return out


__all__ = [
    "PurposeBundle",
    "PURPOSE_PRESETS",
    "validate_purpose_presets",
    "purpose_bundles_bounded",
]
