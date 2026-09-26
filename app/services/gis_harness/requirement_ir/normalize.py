"""Requirement IR 确定性归一化（F02 DoD #6 的"稳定条件"面）。

只做**表驱动、有界**的值规范化——不做开放式 NLP、不复制
intent_semantic 的双语解析权威。目标：同义槽位值（"各区"/"各 district"、
"蓝色"/"blue"、"成都市"/"成都"）归一到同一规范形，使 requirement
digest 在稳定条件下可比（同义 → 同 digest；语义变化 → digest 必变）。
"""
from __future__ import annotations

import re
from typing import Tuple

from app.services.gis_harness.requirement_ir.contracts import (
    DimensionVocab,
    NormalizationVocab,
    StatisticVocab,
    TimeGranularity,
)

# ── 行政区名后缀（有界、常用集合；剥后缀使「成都市」==「成都」） ──────────
_ADMIN_SUFFIXES: Tuple[str, ...] = (
    "特别行政区", "自治区", "自治州", "地区", "盟", "市", "县", "省", "区", "州", "旗",
)

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_admin_name(name: str) -> str:
    """行政区名规范形：去空白、剥常见后缀（迭代剥，如「成都市」→「成都」）。"""
    value = _WHITESPACE_RE.sub("", (name or "")).strip()
    changed = True
    while changed and value:
        changed = False
        for suffix in _ADMIN_SUFFIXES:
            if len(value) > len(suffix) and value.endswith(suffix):
                value = value[: -len(suffix)]
                changed = True
    return value


def normalize_text(value: str) -> str:
    """通用规范形：trim + 压空白 + lower（用于枚举值/锁定值比较）。"""
    return _WHITESPACE_RE.sub(" ", (value or "").strip()).lower()


# ── 统计词 ───────────────────────────────────────────────────────────

_STATISTIC_TABLE: Tuple[Tuple[Tuple[str, ...], StatisticVocab], ...] = (
    (("数量", "个数", "多少", "几所", "几条", "count", "number of"), "count"),
    (("密度", "每平方公里", "per area", "density"), "density"),
    (("占比", "比例", "份额", "share", "percent", "proportion"), "share"),
    (("平均", "均值", "mean", "average"), "mean"),
    (("中位", "median"), "median"),
    (("总和", "总量", "总数", "sum", "total"), "sum"),
    (("比率", "比值", "ratio"), "ratio"),
    (("率", "rate"), "rate"),
    (("指数", "index", "ndvi"), "index"),
    (("最大", "max", "最高"), "max"),
    (("最小", "min", "最低"), "min"),
)


def normalize_statistic(text: str) -> StatisticVocab:
    """统计词规范形；未表达 → ``none``（歧义由 clarify 判定，不猜）。"""
    value = normalize_text(text)
    if not value:
        return "none"
    for synonyms, statistic in _STATISTIC_TABLE:
        for synonym in synonyms:
            if synonym in value:
                return statistic
    return "none"


# ── 分组维度 ─────────────────────────────────────────────────────────

_GROUP_BY_TABLE: Tuple[Tuple[Tuple[str, ...], DimensionVocab, str], ...] = (
    (("各区", "各个区", "每个区", "按区", "分区分", "district", "per district"),
     "administrative", "district"),
    (("街道", "subdistrict"), "administrative", "subdistrict"),
    (("各县", "按县", "county"), "administrative", "county"),
    (("各市", "按市", "per city"), "administrative", "city"),
    (("网格", "渔网", "hex", "h3", "grid"), "grid", "grid"),
    (("类别", "类型", "分类", "category", "class"), "category", "category"),
)

_CUSTOM_GROUP_RE = re.compile(r"按\s*([^,，。;；\s]{1,16}?)\s*(?:分组|统计|聚合|分)")


def normalize_group_by(text: str) -> Tuple[DimensionVocab, str]:
    """分组词规范形 → (dimension, group_by)。

    词表未命中 = **无分组信号**（返回 ("none", "")）——edit 语句的任意
    词面不得充当分组（否则「隐藏道路图层」会被当成 custom 分组）。
    显式「按X分/按X统计」句式 → custom。
    """
    value = normalize_text(text)
    if not value:
        return ("none", "")
    for synonyms, dimension, canonical in _GROUP_BY_TABLE:
        for synonym in synonyms:
            if synonym in value:
                return (dimension, canonical)
    custom = _CUSTOM_GROUP_RE.search(value)
    if custom and custom.group(1).strip():
        return ("custom", custom.group(1).strip()[:48])
    return ("none", "")


# ── 归一化口径 ───────────────────────────────────────────────────────

_NORMALIZATION_TABLE: Tuple[Tuple[Tuple[str, ...], NormalizationVocab], ...] = (
    (("每平方公里", "per km", "per_km", "单位面积"), "per_area"),
    (("人均", "每万人", "per capita", "per_capita"), "per_capita"),
)


def normalize_normalization(text: str) -> NormalizationVocab:
    """归一化口径规范形。只有显式口径词（每平方公里/人均…）才产出；
    其余话语（如「同比」「对比」这类比较语义）一律 ``none``——
    任意词面不得充当口径，否则会误触发分母澄清（review §3）。"""
    value = normalize_text(text)
    if not value:
        return "none"
    for synonyms, normalization in _NORMALIZATION_TABLE:
        for synonym in synonyms:
            if synonym in value:
                return normalization
    return "none"


# ── 时间粒度 ─────────────────────────────────────────────────────────

_TIME_TABLE: Tuple[Tuple[Tuple[str, ...], TimeGranularity, bool], ...] = (
    (("逐年", "历年", "每年", "yearly", "annual", "per year"), "year", True),
    (("逐月", "每月", "monthly"), "month", True),
    (("逐季", "每季", "quarterly"), "quarter", True),
    (("逐日", "每日", "daily"), "day", True),
    (("近十年", "近10年", "近五年", "近5年", "recent"), "year", True),
)

_YEAR_RE = re.compile(r"(19|20)\d{2}")


def normalize_time(text: str) -> Tuple[str, str, TimeGranularity, bool]:
    """时间词规范形 → (range_start, range_end, granularity, series)。

    「2020-2024」→ 显式区间；「逐年/近五年」→ series；未表达 → 全空。
    """
    value = normalize_text(text)
    if not value:
        return ("", "", "none", False)
    for synonyms, granularity, series in _TIME_TABLE:
        for synonym in synonyms:
            if synonym in value:
                return ("", "", granularity, series)
    year_values = [m.group(0) for m in _YEAR_RE.finditer(value)]
    if len(year_values) >= 2:
        return (year_values[0], year_values[-1], "year", True)
    if len(year_values) == 1:
        return (year_values[0], year_values[0], "year", False)
    return ("", "", "none", False)


# ── 调色板 / 表达 ────────────────────────────────────────────────────

_PALETTE_TABLE: Tuple[Tuple[Tuple[str, ...], str], ...] = (
    (("蓝色", "蓝", "blue"), "blue"),
    (("红色", "红", "red"), "red"),
    (("绿色", "绿", "green"), "green"),
    (("橙色", "橙", "orange"), "orange"),
    (("紫色", "紫", "purple"), "purple"),
    (("暖色", "热力色", "warm"), "warm"),
    (("冷色", "cool"), "cool"),
    (("灰度", "黑白", "greyscale", "grayscale"), "greyscale"),
    (("viridis",), "viridis"),
)


def normalize_palette(text: str) -> str:
    value = normalize_text(text)
    if not value:
        return ""
    for synonyms, palette in _PALETTE_TABLE:
        for synonym in synonyms:
            if synonym in value:
                return palette
    return value[:48]


_GEOMETRY_KIND_TABLE: Tuple[Tuple[Tuple[str, ...], str], ...] = (
    (("热力图", "热力", "heatmap", "heat map"), "heatmap"),
    (("分级", "choropleth", "填色"), "choropleth"),
    (("气泡", "比例符号", "proportional symbol", "bubble"), "proportional_symbol"),
    (("等值线", "等高线", "contour", "isoline"), "isoline"),
    (("点图", "散点", "point map", "dot"), "point"),
    (("流向", "流线", "flow"), "flow"),
)


def normalize_geometry_kind(text: str) -> str:
    value = normalize_text(text)
    if not value:
        return ""
    for synonyms, kind in _GEOMETRY_KIND_TABLE:
        for synonym in synonyms:
            if synonym in value:
                return kind
    return value[:48]


# ── 导出格式（对齐 intent.ExportFormat 词表） ──────────────────────────

_EXPORT_FORMATS = frozenset({"png", "pdf", "svg", "csv", "geojson"})


def normalize_format(text: str) -> str:
    value = normalize_text(text).replace(" ", "")
    return value if value in _EXPORT_FORMATS else ""


def normalize_formats(values) -> Tuple[str, ...]:
    seen: list[str] = []
    for raw in values or ():
        fmt = normalize_format(str(raw))
        if fmt and fmt not in seen:
            seen.append(fmt)
    return tuple(seen)
