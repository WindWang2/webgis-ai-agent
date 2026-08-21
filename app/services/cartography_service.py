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
    def _jenks_natural_breaks(cls, values: np.ndarray, k: int) -> List[float]:
        """Fisher-Jenks 自然断点算法 (O(n²k) 动态规划实现)

        #441: 类内平方和 (SSM) 不再对每个 (类数, j, i) 三元组重新切片求和
        （旧实现 ~2n² 次 numpy 调用，在 n=1000 采样上限处一次 classify 需
        40-60 秒），而是用「前缀和」推导：区间 [i, j] 的 SSE 可由 cumsum(值)
        与 cumsum(值²) 在 O(1) 内得出，再把每个类数 c 的内层 argmin 向量化
        为一次 (i, j) 矩阵运算。

        等价性：DP 结构、回溯与平局规则（取首个最小值，等价于旧实现的
        严格 `<` 比较）保持不变；断点值仍从原始（未平移）数组取，因此与
        旧实现的分类边界完全一致（tests/test_jenks_441.py 用旧算法逐字
        副本在多组对抗数据上验证）。"""
        arr = np.sort(values)
        n = len(arr)
        if n <= k:
            # Too few points for k classes — return all unique values as breaks
            uniq = sorted(set(arr.tolist()))
            return uniq if len(uniq) >= 2 else [uniq[0], uniq[0]]
        # Cap sample size for performance (Jenks is O(n²k))
        if n > 1000:
            # #618-19: 披露降采样 —— 断点基于均匀抽样的 1000 个样本计算，
            # 对极端分布可能与全量断点有偏差，调用方需要知情。
            logger.info(
                "_jenks_natural_breaks: n=%d > 1000 — uniform-subsampling to 1000 "
                "samples (seed=42) for the O(n²k) DP (#441 perf cap)",
                len(values),
            )
            rng = np.random.default_rng(42)
            arr = np.sort(rng.choice(arr, size=1000, replace=False))
            n = 1000

        # 方差对平移不变：先减去 arr[0] 再做前缀和，避免「大偏移 + 小离散度」
        # 数据（如 1e12 量级坐标）在 cum2 - cum²/cnt 中发生灾难性抵消。
        # 断点从原始 arr 回填，平移不影响返回值。
        v = arr - arr[0]
        # cum[i] = sum(v[:i]) → 区间 [i, j] 的和/平方和均为 O(1) 差分查询
        cum = np.concatenate(([0.0], np.cumsum(v)))
        cum2 = np.concatenate(([0.0], np.cumsum(v * v)))

        # SSE[i, j] = S2 - S²/cnt  (i ≤ j；j < i 的格子置 inf 表示非法切分)
        idx = np.arange(n)
        cnt = idx[None, :] - idx[:, None] + 1.0
        s = cum[None, 1:] - cum[:-1, None]
        s2 = cum2[None, 1:] - cum2[:-1, None]
        ssm = np.where(cnt > 0, s2 - (s * s) / np.maximum(cnt, 1.0), np.inf)

        # DP: mat[c][j] = 把 arr[0..j] 分为 c 类的最小总方差
        mat = np.full((k + 1, n), np.inf)
        back = np.zeros((k + 1, n), dtype=np.int64)

        # 1 类：直接取区间方差
        mat[1, :] = ssm[0, :]

        inf_col = np.full(n, np.inf)
        for c in range(2, k + 1):
            # costs[i, j] = mat[c-1][i-1] + ssm(i, j)；i < c-1 的候选置 inf。
            # np.argmax/argmin 取首个最小值 ⇒ 与旧实现 `cost < best_cost`
            # 的平局语义一致。
            col = inf_col.copy()
            col[c - 1 :] = mat[c - 1, c - 2 : n - 1]
            costs = col[:, None] + ssm
            best = np.argmin(costs, axis=0)
            mat[c, :] = costs[best, idx]
            back[c, :] = best

        # 回溯断点
        breaks = [float(arr[-1])]
        j = n - 1
        for c in range(k, 1, -1):
            split_idx = int(back[c][j])
            breaks.append(float(arr[split_idx - 1]))
            j = split_idx - 1
        breaks.append(float(arr[0]))
        breaks.sort()
        return list(dict.fromkeys(breaks))  # deduplicate while preserving order

    @classmethod
    def classify(cls, values: List[float], method: str = "quantiles", k: int = 5) -> List[float]:
        """数据分类方法 (quantiles / equal_interval / natural_breaks /
        std_dev / head_tail)。方法元数据（适用场景、权威出处）见
        ``app.lib.cartography.model_library.CLASSIFICATION_METHODS``。"""
        if not values:
            return []
        arr = np.array(values, dtype=float)
        if method == "quantiles":
            return np.unique(np.quantile(arr, np.linspace(0, 1, k + 1))).tolist()
        elif method == "equal_interval":
            return np.linspace(arr.min(), arr.max(), k + 1).tolist()
        elif method == "natural_breaks":
            return cls._jenks_natural_breaks(arr, k)
        elif method == "std_dev":
            return cls._std_dev_breaks(arr, k)
        elif method == "head_tail":
            return cls._head_tail_breaks(arr, k)
        # #557 断点 3：categorical 不是数值断点方法 —— classify 只做数值分级，
        # categorical 由 build_thematic_style 的分支处理（返回类别→色表）。
        logger.warning(
            "classify: 未知分类方法 %s，按 equal_interval 兜底（categorical 不应到达此处）",
            method,
        )
        return np.linspace(arr.min(), arr.max(), k + 1).tolist()

    @classmethod
    def _std_dev_breaks(cls, arr: np.ndarray, k: int = 5) -> List[float]:
        """QGIS『Standard Deviation』模式：以均值为中心、0.5 SD 步进对称铺断点。

        断点 = mean ± m·(SD/2)，向两侧铺满 k-1 个内断点，越出 [min, max]
        的裁掉；常数场（SD=0）退化为等间距两端。适合围绕均值波动的统计面。
        """
        lo, hi = float(arr.min()), float(arr.max())
        sd = float(arr.std())
        if sd <= 0 or hi <= lo:
            return [lo, hi]
        mu = float(arr.mean())
        n_inner = max(1, k - 1)
        half = n_inner // 2
        mults: List[float] = []
        for j in range(1, half + 1):
            mults.append(j * 0.5)
            mults.append(-j * 0.5)
        if n_inner % 2 == 1:
            mults.append((half + 1) * 0.5)
        raw = sorted(mu + m * sd for m in mults)
        inner = [v for v in raw if lo < v < hi]
        return list(dict.fromkeys([lo, *inner, hi]))

    @classmethod
    def _head_tail_breaks(cls, arr: np.ndarray, k: int = 5) -> List[float]:
        """Jiang (2013) Head/Tail Breaks：重尾（长尾）分布的自然分级。

        反复对当前『头』（≤ 均值的低值主体）取算术均值作为断点，高于均值的
        『尾』自成一类，递归只作用于头。类别数由数据形态决定——重尾数据
        能产出接近 k 的类数，近均匀数据可能只有一两个断裂（这是方法特性，
        不是退化）。最小头规模 8 为 Jiang 的经验门槛。
        """
        sub = np.sort(np.asarray(arr, dtype=float))
        lo, hi = float(sub[0]), float(sub[-1])
        if hi <= lo:
            return [lo, hi]
        means: List[float] = []
        while len(means) < k - 1:
            mean = float(np.mean(sub))
            idx = int(np.searchsorted(sub, mean, side="right"))
            # 均值落在端点之外/无进展 → 停止（类数由数据决定）
            if idx <= 0 or idx >= len(sub):
                break
            means.append(mean)
            sub = sub[:idx]
            if len(sub) < 8:
                break
        return list(dict.fromkeys([lo, *sorted(means), hi]))

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
                    categorical_values.append(marker)
                    if len(categorical_values) >= k:
                        break
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
