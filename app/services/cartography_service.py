"""
地图制图服务 - 处理样式生成、专题图分类和配色方案
"""
import logging
from typing import List, Dict, Any, Optional
import numpy as np

from app.lib.cartography.palettes import get_color_from_palette

logger = logging.getLogger(__name__)


class CartographyService:
    get_color_from_palette = staticmethod(get_color_from_palette)


    @classmethod
    def classify(cls, values: List[float], method: str = "quantiles", k: int = 5) -> List[float]:
        """委托到 app/lib/cartography/classify.py（E-3/#894：算法下沉 lib，
        消除 lib/cartography/thematic_spec 的 services 反向依赖）。"""
        from app.lib.cartography.classify import classify_values

        return classify_values(values, method, k)

    # E-3（#894）：算法实现已下沉 app/lib/cartography/classify.py，别名保持兼容。
    from app.lib.cartography import classify as _classify_mod  # noqa: E402

    _jenks_natural_breaks = staticmethod(_classify_mod._jenks_natural_breaks)
    _std_dev_breaks = staticmethod(_classify_mod._std_dev_breaks)
    _head_tail_breaks = staticmethod(_classify_mod._head_tail_breaks)




    @classmethod
    def build_thematic_style(
        cls,
        geojson: Dict[str, Any],
        field: str,
        method: str = "quantiles",
        k: int = 5,
        palette: str = "YlOrRd"
    ) -> Optional[Dict[str, Any]]:
        """
        计算专题图样式定义，不修改原 GeoJSON
        """
        features = geojson.get("features", [])
        if not features:
            return None

        values = []
        lisa_values = []
        categorical_values: list = []
        seen_categories: set = set()
        # #783: 超出 k 的类别不再截断丢弃 —— 继续扫描以检测剩余类别的存在
        # （不收集值本身，保持收集阶段的有界性）。
        categorical_surplus = False
        for f in features:
            val = f.get("properties", {}).get(field)
            if method == "lisa":
                if val in ["HH", "LL", "HL", "LH", "NS"]:
                    lisa_values.append(val)
            elif method == "categorical":
                # #557 断点 3：categorical 收集去重类别（字符串或数值），
                # 不做 NaN/Inf 过滤之外的数值约束 —— 分类字段本来就是离散值。
                if val is None:
                    continue
                if isinstance(val, bool):
                    marker = val
                else:
                    try:
                        marker = float(val)
                        if not np.isfinite(marker):
                            continue
                    except (TypeError, ValueError):
                        marker = val
                if marker not in seen_categories:
                    seen_categories.add(marker)
                    if len(categorical_values) < k:
                        categorical_values.append(marker)
                    else:
                        categorical_surplus = True
            else:
                # Filter NaN/Inf/bool once at the value-collection seam so a
                # stray null in the column can no longer poison the quantile/
                # Jenks breaks (ADR-0052 no-data semantics).
                if isinstance(val, (int, float)) and not isinstance(val, bool) and np.isfinite(val):
                    values.append(float(val))

        if method == "lisa":
            if not lisa_values:
                logger.warning(f"字段 {field} 未发现有效的 LISA 分类值")
                return None
            
            # 标准 LISA 颜色
            lisa_colors = {
                "HH": "#ff0000",   # 高-高 聚集 (红)
                "LL": "#0000ff",   # 低-低 聚集 (蓝)
                "HL": "#ffaaaa",   # 高-低 异常 (浅红)
                "LH": "#aaaaff",   # 低-高 异常 (浅蓝)
                "NS": "#cccccc"    # 不显著 (灰)
            }
            
            return {
                "type": "lisa",
                "field": field,
                "categories": ["HH", "LL", "HL", "LH", "NS"],
                "colors": lisa_colors,
                "legend_labels": ["High-High", "Low-Low", "High-Low", "Low-High", "Not Significant"]
            }

        if method == "categorical":
            # #557 断点 3：categorical 模板（tmpl_th_zoning/soil_type/…）此前
            # 落入 classify 的 equal_interval 兜底 —— 对字符串分类字段产生
            # 数值断点，完全错误。这里按类别数 k 上限收集去重类别，给每个类别
            # 从调色板顺序取色（颜色循环），保持值类型（数值类别保留数值键，
            # 前端 match 表达式才能命中）。
            if not categorical_values:
                logger.warning(f"字段 {field} 未发现有效的分类值")
                return None
            from app.lib.cartography.palettes import resolve_palette_colors as _resolve_palette_colors
            colors = _resolve_palette_colors(palette)
            # #783: 去重类别超过 k 时不再静默截断（截断使第 k+1..N 类被
            # match default 刷成第 k 类颜色却没有任何图例条目，不同类别在
            # 图上被合并）。前 k-1 类保留各自颜色，剩余类别并入显式「其他」
            # 桶 —— 其他桶位于末位，_categorical_to_match 的 default 恰好
            # 覆盖它（所有剩余值渲染为其他桶颜色），总类数仍以 k 为上限，
            # 颜色分配保持确定性。
            if categorical_surplus:
                kept = categorical_values[: k - 1]
                entries = [
                    {
                        "key": v,
                        "color": colors[i % len(colors)],
                        "label": str(v),
                    }
                    for i, v in enumerate(kept)
                ]
                entries.append({
                    "key": "__other__",
                    "color": colors[(k - 1) % len(colors)],
                    "label": "其他",
                })
            else:
                entries = [
                    {
                        "key": v,
                        "color": colors[i % len(colors)],
                        "label": str(v),
                    }
                    for i, v in enumerate(categorical_values)
                ]
            return {
                "type": "categorical",
                "field": field,
                "categories": entries,
                "legend_labels": [e["label"] for e in entries],
            }

        if not values:
            logger.warning(f"字段 {field} 未发现数值，无法制作专题图")
            return None

        # 计算间断点
        breaks = cls.classify(values, method, k)
        # #618-19: 全等数值列（常量字段）在 n>k 时 classify 返回单断点 [v]
        # （_jenks 的 n≤k 分支返回 [v, v]）——两种路径形状不一致会让下游
        # 拿不到合法的 2 断点数组。统一归一化为 [v, v]（单类、两相同边界）。
        if len(breaks) == 1:
            breaks = [breaks[0], breaks[0]]
        min_val, max_val = min(values), max(values)

        # ADR-0052: resolve palette colors through the single midpoint-sampling
        # path (resolve_thematic_colors) so build_thematic_style, build_graduated_spec
        # and h3_binning all share one palette-resolution implementation.
        from app.lib.cartography.thematic_spec import resolve_thematic_colors
        colors = resolve_thematic_colors(palette, len(breaks) - 1, breaks, min_val, max_val)
        legend_labels = [f"{breaks[i]:.2f} - {breaks[i + 1]:.2f}" for i in range(len(breaks) - 1)]

        return {
            "type": "choropleth",
            "field": field,
            "breaks": breaks,
            "colors": colors,
            "legend_labels": legend_labels
        }

    @classmethod
    def build_legend_spec(
        cls,
        style_def,
        palette: str = "YlOrRd",
    ):
        """把 build_thematic_style 的输出映射为对外 legend_spec 契约。

        choropleth → graduated；lisa → categorical。未知 / 空输入返回 None。
        """
        if not isinstance(style_def, dict):
            return None
        t = style_def.get("type")
        if t == "choropleth":
            return {
                "type": "graduated",
                "field": style_def.get("field", ""),
                "breaks": style_def.get("breaks", []),
                "palette": palette,
                "palette_colors": style_def.get("colors", []),
            }
        if t == "lisa":
            colors = style_def.get("colors", {}) or {}
            labels = style_def.get("legend_labels", []) or []
            keys = style_def.get("categories", []) or []
            categories = []
            for i, key in enumerate(keys):
                categories.append({
                    "key": key,
                    "color": colors.get(key, "#999999"),
                    "label": labels[i] if i < len(labels) else key,
                })
            return {
                "type": "categorical",
                "field": style_def.get("field", ""),
                "categories": categories,
            }
        if t == "categorical":
            # #557 断点 3：categorical style_def 的 categories 是 [{key,color,label}]
            # 列表（保留数值/字符串值类型），映射为与 lisa 相同的 legend 契约。
            entries = style_def.get("categories", []) or []
            return {
                "type": "categorical",
                "field": style_def.get("field", ""),
                "categories": [
                    {
                        "key": str(c.get("key")),
                        "color": c.get("color", "#999999"),
                        "label": c.get("label", str(c.get("key"))),
                    }
                    for c in entries
                ],
            }
        return None

__all__ = ["CartographyService"]
