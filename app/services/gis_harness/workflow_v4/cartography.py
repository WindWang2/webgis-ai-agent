"""Cartographic Obligations V4 —— 表达-数据资格约束（renderer 之前声明）。

「画什么图」不是自由选择：表达方式与数据资格之间存在专业约束（率图
需要分母；小样本面分区禁分级色；密度语义不得用原始计数填色……）。
本模块把这些约束机器化为**制图义务**，进入完成契约（cartography 维），
由 renderer 在执行期兑现。

红线：

- 义务只声明，不渲染 —— 渲染归 Cartography Workbench / renderer；
- 表达词表引用 MapModelRegistry 注册 id（单一事实源，注册期校验）；
- 裁决确定性：同事实同义务；on_violation ⊆ workflow_schema.OBLIGATION_ACTIONS；
- 全部零 LLM、零 I/O。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from pydantic import BaseModel

#: 稳定义务码前缀。
CARTO_RATE_REQUIRES_DENOMINATOR = "CARTO_RATE_REQUIRES_DENOMINATOR"
CARTO_SMALL_N_CHOROPLETH = "CARTO_SMALL_N_CHOROPLETH"
CARTO_DENSITY_NO_RAW_COUNT = "CARTO_DENSITY_NO_RAW_COUNT"
CARTO_APPROXIMATE_UNCERTAINTY_DISPLAY = "CARTO_APPROXIMATE_UNCERTAINTY_DISPLAY"
CARTO_LEGEND_SOURCE_REQUIRED = "CARTO_LEGEND_SOURCE_REQUIRED"

#: 面分区分级色（choropleth）的最小分区数（低于此统计不稳定，禁分级）。
_MIN_REGIONS_FOR_CHOROPLETH = 3

_MAX_OBLIGATIONS = 8


class CartographicObligation(BaseModel):
    """一个制图义务：表达方式必须满足的数据/事实条件。"""
    obligation_id: str
    expression: str = ""               # ⊆ MapModelRegistry（校验）
    rule_code: str                     # 稳定码
    on_violation: str = "warn"         # ⊆ OBLIGATION_ACTIONS
    disclosure: str = ""
    detail: str = ""

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "obligation_id": self.obligation_id[:64],
            "expression": self.expression[:48],
            "rule_code": self.rule_code[:64],
            "on_violation": self.on_violation,
            "disclosure": self.disclosure[:200],
            "detail": self.detail[:200],
        }


def _is_rate_like_method(method_id: str) -> bool:
    """率/密度语义方法（其填色值必须是归一化比率而非原始计数）。"""
    return ("admin_rate" in method_id
            or "rate" in method_id
            or "density" in method_id
            or "equity" in method_id)


def _is_quantitative_density(method_id: str) -> bool:
    """定量密度方法（产出密度面；choropleth 只能承载其归一化值）。"""
    return ("kernel" in method_id or "grid_binning" in method_id
            or "density_surface" in method_id)


def evaluate_cartographic_obligations(
    selected_method: Optional[Any],
    *,
    role_states: Optional[Dict[str, str]] = None,
    profile: Optional[Dict[str, Any]] = None,
    secondary_cartography: Sequence[str] = (),
) -> List[CartographicObligation]:
    """表达-数据资格义务裁决（确定性）。

    规则（当前审定集，纯加法演进）：
    1. 率/密度语义 → choropleth 前必须有非 blocked 分母
       （denominator blocked → block_method）；
    2. 定量密度产物 → choropleth 不得呈现原始计数（有 secondary 显式
       choropleth 时 block_method）；
    3. choropleth 分区数 < 3 → 降级为比例符号/点图（degrade_with_disclosure）；
    4. 近似/代理方法 → 必须展示不确定性披露（degrade_with_disclosure）；
    5. 恒定：图例+数据源披露（warn —— 组件缺失被既有 verdict 管线捕获）。
    """
    states = role_states or {}
    obligations: List[CartographicObligation] = []
    method_id = str(getattr(selected_method, "method_id", "") or "")
    method_approximate = bool(getattr(selected_method, "approximate", False))

    # 1. 率/密度语义需要分母
    if _is_rate_like_method(method_id):
        denom_state = states.get("denominator", "unknown")
        blocked = denom_state == "blocked"
        obligations.append(CartographicObligation(
            obligation_id="carto.rate_denominator",
            expression="administrative_choropleth",
            rule_code=CARTO_RATE_REQUIRES_DENOMINATOR,
            on_violation="block_method" if blocked else "warn",
            disclosure=("分母数据缺失：率/密度填色被阻断，"
                        "不得以原始计数冒充率值。" if blocked else ""),
            detail=f"denominator={denom_state}",
        ))

    # 2. 定量密度产物不得以 choropleth 呈现原始计数
    if _is_quantitative_density(method_id):
        choropleth_secondary = "administrative_choropleth" in (
            set(secondary_cartography))
        obligations.append(CartographicObligation(
            obligation_id="carto.density_no_raw_count",
            expression="administrative_choropleth",
            rule_code=CARTO_DENSITY_NO_RAW_COUNT,
            on_violation="block_method" if choropleth_secondary else "warn",
            disclosure=("定量密度面（核密度/网格聚合）不得以行政区分级色"
                        "呈现原始计数；choropleth 只能承载归一化密度值。"
                        if choropleth_secondary else ""),
            detail=f"method={method_id}",
        ))

    # 3. 小样本面分区 → 禁分级色
    regions = None
    if profile:
        n = profile.get("regionCount")
        if isinstance(n, (int, float)):
            regions = int(n)
    if regions is not None and regions < _MIN_REGIONS_FOR_CHOROPLETH:
        obligations.append(CartographicObligation(
            obligation_id="carto.small_n_choropleth",
            expression="administrative_choropleth",
            rule_code=CARTO_SMALL_N_CHOROPLETH,
            on_violation="degrade_with_disclosure",
            disclosure=(f"分区数 {regions} < {_MIN_REGIONS_FOR_CHOROPLETH}："
                        "分级色统计不稳定，降级为比例符号/点图。"),
            detail=f"regionCount={regions}",
        ))

    # 4. 近似方法 → 不确定性展示
    if method_approximate:
        obligations.append(CartographicObligation(
            obligation_id="carto.approximate_uncertainty",
            expression=",".join(list(secondary_cartography)[:3]),
            rule_code=CARTO_APPROXIMATE_UNCERTAINTY_DISPLAY,
            on_violation="degrade_with_disclosure",
            disclosure="近似/代理方法产物必须在图面与披露中标记不确定性。",
            detail=f"method={method_id}",
        ))

    # 5. 图例/数据源恒定义务
    obligations.append(CartographicObligation(
        obligation_id="carto.legend_source",
        rule_code=CARTO_LEGEND_SOURCE_REQUIRED,
        on_violation="warn",
        detail="图例、数据来源、制图时间必须随图披露。",
    ))
    return obligations[:_MAX_OBLIGATIONS]
