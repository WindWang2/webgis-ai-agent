"""
地图制图服务 - 处理样式生成、专题图分类和配色方案
"""
import logging
from typing import List, Dict, Any, Optional
import numpy as np

logger = logging.getLogger(__name__)

COLOR_PALETTES = {
    "YlOrRd": ["#ffffb2", "#fed976", "#feb24c", "#fd8d3c", "#f03b20", "#bd0026"],
    "Blues": ["#eff3ff", "#bdd7e7", "#6baed6", "#3182bd", "#08519c"],
    "Greens": ["#edf8e9", "#bae4b3", "#74c476", "#31a354", "#006d2c"],
    "Reds": ["#fee5d9", "#fcae91", "#fb6a4a", "#de2d26", "#a50f15"],
    "Viridis": ["#440154", "#3b528b", "#21908c", "#5dc963", "#fde725"],
    "Magma": ["#000004", "#3b0f70", "#8c2981", "#de4968", "#feb078", "#fcfdbf"],
}

class CartographyService:
    @staticmethod
    def get_color_from_palette(palette_name: str, value: float) -> str:
        """
        从调色板中获取对应数值的颜色
        value: 0 ~ 1 之间的浮点数
        """
        palette = COLOR_PALETTES.get(palette_name, COLOR_PALETTES["YlOrRd"])
        n = len(palette)
        idx = min(int(value * n), n - 1)
        return palette[idx]

    @classmethod
    def classify(cls, values: List[float], method: str = "quantiles", k: int = 5) -> List[float]:
        """数据分类方法"""
        if not values: return []
        arr = np.array(values)
        if method == "quantiles":
            return np.unique(np.quantile(arr, np.linspace(0, 1, k + 1))).tolist()
        elif method == "equal_interval":
            return np.linspace(arr.min(), arr.max(), k + 1).tolist()
        elif method == "natural_breaks":
            # 简化版 Jenks (使用等间距兜底，如果需要真 Jenks 需要辅助库)
            return np.linspace(arr.min(), arr.max(), k + 1).tolist()
        return np.linspace(arr.min(), arr.max(), k + 1).tolist()

    @classmethod
    def apply_choropleth(
        cls, 
        geojson: Dict[str, Any], 
        field: str, 
        method: str = "quantiles", 
        k: int = 5, 
        palette: str = "YlOrRd"
    ) -> Dict[str, Any]:
        """
        为 GeoJSON 要素添加专题图样式属性
        """
        features = geojson.get("features", [])
        if not features:
            return geojson

        # 提取数值
        values = []
        for f in features:
            val = f.get("properties", {}).get(field)
            if isinstance(val, (int, float)):
                values.append(float(val))

        if not values:
            logger.warning(f"字段 {field} 未发现数值，无法制作专题图")
            return geojson

        # 计算间断点
        breaks = cls.classify(values, method, k)
        min_val, max_val = min(values), max(values)
        val_range = max_val - min_val if max_val > min_val else 1.0

        # 应用颜色
        new_features = []
        for f in features:
            new_f = f.copy()
            val = f.get("properties", {}).get(field)
            
            if isinstance(val, (int, float)):
                # 计算归一化位置 (0 ~ 1)
                normalized = (float(val) - min_val) / val_range
                color = cls.get_color_from_palette(palette, normalized)
                
                # 在 properties 中注入制图样式，前端将读取这些属性
                if "properties" not in new_f:
                    new_f["properties"] = {}
                
                new_f["properties"]["fill_color"] = color
                new_f["properties"]["stroke_color"] = "#ffffff"
                new_f["properties"]["stroke_width"] = 1.0
                new_f["properties"]["opacity"] = 0.8
            
            new_features.append(new_f)

        return {
            "type": "FeatureCollection",
            "features": new_features,
            "metadata": {
                "thematic_type": "choropleth",
                "field": field,
                "breaks": breaks,
                "palette": palette
            }
        }

__all__ = ["CartographyService"]
