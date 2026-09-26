"""视觉缺陷 taxonomy（F15/ADR-0214 决策三）—— 封闭 8 类 + 单一归一点。

三套既有表述（v2 五维 ADR-0185 / healer 四类 ADR-0186 / 语义检查码）在
消费侧归一到本词表；不可映射的输入**诚实丢弃**（返回 ""，调用方计入
``unmapped``），绝不猜。finding code 沿 ADR-0209 的 ``visual_`` 命名
空间（防与确定性码撞名翻转 layer/component_status）。

纯函数、零 I/O、确定性。
"""
from __future__ import annotations

from typing import Dict, Tuple

#: 封闭 taxonomy 词表（F15 任务书词表，冻结；扩展 = 改这里 + 测试互锁）。
VISUAL_TAXONOMY: Tuple[str, ...] = (
    "overlap",           # 组件/图面元素相互遮挡（z-order / 版面）
    "crop",              # 内容越界/裁切（出视口、出画布）
    "legibility",        # 可读性（字号过小、文字模糊）
    "contrast",          # 对比度不足（前景/背景、相邻色）
    "label_collision",   # 注记/标签相互压盖
    "legend_mismatch",   # 图例与图面形态/内容不配
    "empty_space",       # 空图/大面积空白（渲染缺失或信息过疏）
    "hierarchy",         # 视觉层级失衡（主次不清/重心失衡）
)

_TAXONOMY_SET = frozenset(VISUAL_TAXONOMY)

#: taxonomy → finding code（``visual_`` 命名空间，ADR-0209 决策四纪律）。
def finding_code(category: str) -> str:
    """taxonomy 类 → 统一 finding code（未知类按 unmapped 处理返回空）。"""
    cat = str(category or "")
    if cat not in _TAXONOMY_SET:
        return ""
    return f"visual_{cat}"


# ── v2 维度（ADR-0185）→ taxonomy 归一表 ──────────────────────────────────
# 维度是必要不充分条件：defect_type/evidence 关键词优先细分，无命中时
# 走维度缺省；两表都未命中的维度不可映射（诚实丢弃）。

_V2_DIMENSION_DEFAULT: Dict[str, str] = {
    "readability": "legibility",
    "color_discriminability": "contrast",
    "composition_balance": "hierarchy",
    "information_density": "label_collision",
    "spatial_alignment": "overlap",
}

#: 关键词 → taxonomy（在维度缺省之前判定；序即优先级）。
_KEYWORD_RULES: Tuple[Tuple[Tuple[str, ...], str], ...] = (
    (("overlap", "collision", "重叠", "压盖", "碰撞"), "label_collision"),
    (("occlud", "cover", "遮挡", "覆盖", "遮盖", "hidden", "below", "z-order"), "overlap"),
    (("crop", "offscreen", "越界", "裁切", "出界", "cut off", "truncat"), "crop"),
    (("contrast", "对比", "辨", "看不清"), "contrast"),
    (("legend", "图例"), "legend_mismatch"),
    (("blank", "empty", "空白", "空图", "no content"), "empty_space"),
    (("hierarch", "balance", "重心", "层级", "失衡", "clutter"), "hierarchy"),
    (("tiny", "small text", "字号", "过小", "unreadable", "模糊"), "legibility"),
)

# ── healer 类别（ADR-0186 HEALABLE_CATEGORIES）→ taxonomy ─────────────────
_HEALER_CATEGORY_MAP: Dict[str, str] = {
    "label_collision": "label_collision",
    "contrast": "contrast",
    "layer_order": "overlap",
    "opacity": "contrast",
}


def normalize_to_taxonomy(
    dimension: str,
    defect_type: str = "",
    evidence: str = "",
    *,
    healer_category: str = "",
) -> str:
    """单一归一点：任一来源表述 → taxonomy 类（不可映射 → ""）。

    ``healer_category`` 命中时优先（healer 类别本身已是定位后的封闭类）；
    否则按 evidence/defect_type 关键词细分，最后落维度缺省。
    """
    cat = str(healer_category or "")
    if cat in _HEALER_CATEGORY_MAP:
        return _HEALER_CATEGORY_MAP[cat]
    text = f"{defect_type} {evidence}".lower()
    for needles, target in _KEYWORD_RULES:
        if any(needle in text for needle in needles):
            return target
    return _V2_DIMENSION_DEFAULT.get(str(dimension or ""), "")


# ── taxonomy ↔ 确定性 finding 的融合亲和表（fusion.py 消费）──────────────
# 精确码（completion/semantic check/grammar audit）与前缀码并存；
# 值 = 与该 taxonomy 类"同因"的确定性 finding code 判定面。

_TAXONOMY_EXACT_CODES: Dict[str, Tuple[str, ...]] = {
    "overlap": ("layout_conflict", "stale_overlay", "layer_order_issue"),
    "crop": ("viewport_no_bbox", "invalid_result_bounds"),
    "contrast": ("layer_transparent",),
    "label_collision": ("label_collision",),
    "legend_mismatch": ("semantic_legend_mismatch", "semantic_legend_missing",
                        "GRAMMAR.AUDIT.PAIRING"),
}

_TAXONOMY_CODE_PREFIXES: Dict[str, Tuple[str, ...]] = {
    "contrast": ("carto.color.",),
    "label_collision": ("carto.label.",),
    "legend_mismatch": ("carto.legend.", "GRAMMAR.AUDIT."),
    "empty_space": ("carto.load.", "blank_map"),
    "hierarchy": ("carto.visualvar.",),
}


def deterministic_affinity(category: str, code: str) -> bool:
    """判定确定性 finding code 是否与 taxonomy 类同因（融合判定的单一面）。"""
    cat = str(category or "")
    code = str(code or "")
    if not cat or not code:
        return False
    if code in _TAXONOMY_EXACT_CODES.get(cat, ()):  # 精确命中
        return True
    return any(code.startswith(p) for p in _TAXONOMY_CODE_PREFIXES.get(cat, ()))


def taxonomy_counts_of(categories) -> Dict[str, int]:
    """taxonomy 类计数（有界披露面；未归一入 unmapped）。"""
    counts: Dict[str, int] = {}
    for cat in categories:
        key = str(cat or "") if str(cat or "") in _TAXONOMY_SET else "unmapped"
        counts[key] = counts.get(key, 0) + 1
    return counts


__all__ = [
    "VISUAL_TAXONOMY",
    "finding_code",
    "normalize_to_taxonomy",
    "deterministic_affinity",
    "taxonomy_counts_of",
]
