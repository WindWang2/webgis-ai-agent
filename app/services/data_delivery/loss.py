"""Data Delivery Loss Negotiation — 格式损失先于执行（G18 WP04/WP05）。

预览阶段逐格式分析源画像 → 损失清单（requires_ack）与阻断项；执行阶段
同一模块产出「实际应用」的变换计划（字段映射/编码策略），保证声明与
实际改变一一对应（WP11 oracle 验证）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from app.schemas.data_delivery_schema import (
    DeliveryCrsSpec,
    DeliveryFieldSelection,
    DeliveryFormat,
    DeliveryScope,
)
from app.services.data_delivery import contracts as dl_contracts
from app.services.data_delivery.source_resolver import SourceSnapshot


class LossError(Exception):
    """损失协商中的 typed 失败（error_code + reason）。"""

    def __init__(self, error_code: str, reason: str):
        super().__init__(reason)
        self.error_code = error_code
        self.reason = reason


@dataclass
class LossItemRaw:
    code: str
    message: str
    fields: Optional[List[str]] = None
    requires_ack: bool = False


@dataclass
class LossAnalysis:
    """预览结果 + 执行计划（同一计算，双用途）。"""

    losses: List[LossItemRaw] = field(default_factory=list)
    blocking: List[LossItemRaw] = field(default_factory=list)
    required_acks: List[str] = field(default_factory=list)
    field_map: Optional[Dict[str, str]] = None
    crs_source: Optional[str] = None
    crs_target: Optional[str] = None
    crs_transformed: bool = False

    def add(self, code: str, message: str, *, fields: Optional[List[str]] = None,
            requires_ack: bool = False) -> None:
        self.losses.append(LossItemRaw(code, message, fields, requires_ack))
        if requires_ack and code not in self.required_acks:
            self.required_acks.append(code)

    def block(self, code: str, message: str) -> None:
        self.blocking.append(LossItemRaw(code, message))


_DBF_UNSAFE_RE = re.compile(r"[^A-Za-z0-9_]")


def _shapefile_field_map(
    fields: List[str], explicit: Optional[Dict[str, str]]
) -> Tuple[Dict[str, str], List[LossItemRaw]]:
    """Shapefile 字段名映射：显式 map 校验（覆盖超限/非法名 + 单射），
    缺省生成截断消歧映射（无冲突；冲突显式化，绝不静默丢字段）。"""
    losses: List[LossItemRaw] = []
    needs: List[str] = []
    for name in fields:
        if len(name.encode("utf-8")) > dl_contracts.SHP_FIELD_NAME_LIMIT or \
                _DBF_UNSAFE_RE.search(name):
            needs.append(name)
    if not needs:
        return (dict(explicit) if explicit else None), losses

    if explicit:
        missing = [n for n in needs if n not in explicit]
        if missing:
            raise LossError(
                dl_contracts.ERR_LOSS_ACK_REQUIRED,
                f"以下字段名超出 Shapefile 限制但未提供映射: {missing[:8]}")
        mapped_targets = [v for k, v in explicit.items() if k in fields]
        if len(mapped_targets) != len(set(mapped_targets)):
            raise LossError(
                dl_contracts.ERR_LOSS_ACK_REQUIRED,
                "field_map 输出名冲突（两字段映射到同一输出名）")
        for name in needs:
            target = explicit[name]
            if len(target.encode("utf-8")) > dl_contracts.SHP_FIELD_NAME_LIMIT or \
                    _DBF_UNSAFE_RE.search(target):
                raise LossError(
                    dl_contracts.ERR_LOSS_ACK_REQUIRED,
                    f"映射目标 {target!r} 仍不符合 Shapefile 字段名约束")
        losses.append(LossItemRaw(
            dl_contracts.LOSS_FIELD_NAME_MAPPED,
            f"{len(needs)} 个字段名经显式映射适配 Shapefile ≤10 字节约束",
            fields=needs[:20], requires_ack=True))
        return explicit, losses

    # 自动生成：ASCII 化 + 截断 + 数值后缀消歧（确定性）。
    auto: Dict[str, str] = {}
    used: set = set()
    for name in fields:
        if name not in needs:
            if name in used:  # pragma: no cover - 源字段本应唯一
                continue
            used.add(name)
            auto[name] = name
            continue
        stem = _DBF_UNSAFE_RE.sub("_", name).strip("_") or "f"
        stem_b = stem.encode("utf-8")[: dl_contracts.SHP_FIELD_NAME_LIMIT].decode(
            "utf-8", "ignore")
        candidate = stem_b
        n = 1
        while candidate in used:
            suffix = str(n)
            candidate = stem_b[: max(1, dl_contracts.SHP_FIELD_NAME_LIMIT - len(suffix))] + suffix
            n += 1
        used.add(candidate)
        auto[name] = candidate
    truncated = [n for n in needs if auto[n] != n]
    losses.append(LossItemRaw(
        dl_contracts.LOSS_FIELD_NAME_TRUNCATED,
        "Shapefile 字段名 ≤10 字节：已生成无冲突映射（清单可还原字段对应，"
        "可在提交时用 field_map 覆盖）",
        fields=truncated[:20], requires_ack=True))
    return auto, losses


def analyze_losses(
    snapshot: SourceSnapshot,
    fmt: DeliveryFormat,
    fields_sel: DeliveryFieldSelection,
    crs_spec: DeliveryCrsSpec,
    scope: DeliveryScope,
    spreadsheet_safe: bool = False,
) -> LossAnalysis:
    """源画像 × 格式 × 规格 → 损失/阻断/映射计划（预览与执行共用）。"""
    out = LossAnalysis()
    out.crs_source = snapshot.crs

    # ── 字段集合 ────────────────────────────────────────────────────────
    all_fields = [f["name"] for f in snapshot.fields]
    if fields_sel.fields is not None:
        unknown = [f for f in fields_sel.fields if f not in all_fields]
        if unknown:
            raise LossError(
                dl_contracts.ERR_INVALID_SPEC,
                f"投影字段在源中不存在: {unknown[:8]}")
        selected = list(fields_sel.fields)
    else:
        selected = all_fields
    if len(selected) > dl_contracts.MAX_FIELDS:
        raise LossError(
            dl_contracts.ERR_INVALID_SPEC,
            f"字段数 {len(selected)} 超过上限 {dl_contracts.MAX_FIELDS}")

    complex_fields = [f["name"] for f in snapshot.fields
                      if f.get("inferred_type") == "complex" and f["name"] in selected]

    # ── CRS / 坐标语义 ──────────────────────────────────────────────────
    source_crs = snapshot.crs
    target = crs_spec.target_crs
    if fmt in dl_contracts.GEOJSON_FORMATS:
        if source_crs is None:
            out.block(dl_contracts.ERR_MISSING_SOURCE_CRS,
                      "源数据没有 CRS 标注，而 GeoJSON 要求 WGS84 经纬度坐标；"
                      "拒绝默认按 EPSG:4326 解释。请先为数据登记真实 CRS，或选择"
                      "可携带未知 CRS 的格式（GeoPackage/GeoParquet）")
        elif target is not None and target.upper() not in ("EPSG:4326", "WGS84",
                                                           "OGC:CRS84"):
            raise LossError(
                dl_contracts.ERR_INVALID_SPEC,
                "GeoJSON 目标 CRS 必须是 EPSG:4326（RFC 7946）；不要把投影米数"
                "装成经纬度文件")
        elif target is None and source_crs.upper() not in (
                "EPSG:4326", "WGS84", "OGC:CRS84"):
            # 隐式真实重投影（RFC 7946 要求）—— 源与目标在证据中分别存在；
            # 重投影本身不是损失，显式精度削减才是。
            out.crs_target = "EPSG:4326"
            out.crs_transformed = True
        if crs_spec.coordinate_precision is not None:
            out.add(dl_contracts.LOSS_COORDINATE_PRECISION_REDUCED,
                    f"坐标小数位将削减到 {crs_spec.coordinate_precision} 位"
                    "（显式有损选项：可能改变几何/拓扑）",
                    requires_ack=True)
    else:
        # 纯属性表（无几何列输出）没有坐标语义 —— 不设 CRS 闸；
        # 附 WKT 列时才要求坐标语义（表格源本身无几何，恒走 geometry_omitted）。
        table_attributes_only = snapshot.is_table and fmt == "csv"
        if source_crs is None and not table_attributes_only:
            if crs_spec.allow_unknown_source_crs:
                out.add(dl_contracts.LOSS_CRS_LABEL_UNKNOWN,
                        "源 CRS 未知：输出将如实标注未知坐标系（不做坐标变换）",
                        requires_ack=True)
            else:
                out.block(dl_contracts.ERR_MISSING_SOURCE_CRS,
                          f"源数据没有 CRS 标注。{fmt} 可携带未知 CRS，但需要你"
                          "显式确认（allow_unknown_source_crs=true），或先为数据"
                          "登记真实 CRS")
    if source_crs is not None and target is not None:
        out.crs_target = target
        out.crs_transformed = _crs_differs(source_crs, target)

    # ── 几何语义 ────────────────────────────────────────────────────────
    geometry_types: List[str] = list((snapshot.geometry or {}).get("types") or [])
    mixed = len([t for t in geometry_types if not t.endswith("Collection")]) > 1 or (
        len(geometry_types) > 1 and "GeometryCollection" not in geometry_types
        and len(set(geometry_types)) > 1)
    if not snapshot.is_table and geometry_types:
        if fmt == "shapefile_zip" and len(set(geometry_types)) > 1:
            out.add(dl_contracts.LOSS_GEOMETRY_SPLIT_BY_TYPE,
                    f"混合几何（{sorted(set(geometry_types))}）在 Shapefile 中按"
                    "类型分组为多图层（计数相加守恒）",
                    requires_ack=True)
        elif fmt == "gpkg" and len(set(geometry_types)) > 1 and scope is not None:
            # 单图层混合：GPKG 支持 Unknown 几何层 —— 披露不阻断
            out.add(dl_contracts.LOSS_MIXED_GEOMETRY_SINGLE_LAYER,
                    f"混合几何（{sorted(set(geometry_types))}）写入单一图层"
                    "（GPKG Unknown 几何类型；不丢类型信息）")
        if (snapshot.geometry or {}).get("has_z"):
            out.add(dl_contracts.LOSS_Z_PRESERVED, "Z 坐标将随几何保留")
        null_or_empty = (snapshot.geometry or {}).get("null_or_empty_count") or 0
        if null_or_empty:
            out.add(dl_contracts.LOSS_NULL_GEOMETRY_PRESERVED,
                    f"{null_or_empty} 行含空/缺失几何 —— 按原样保留"
                    "（不删除要素、不修复几何）")
    if snapshot.is_table and fmt not in ("csv",):
        # 表格源交付矢量格式：无几何可写 —— 阻断（CSV 可选几何列同理缺席）
        if fmt in ("geojson", "gpkg", "geoparquet", "shapefile_zip", "flatgeobuf"):
            out.block(dl_contracts.ERR_SOURCE_SHAPE_UNSUPPORTED,
                      "表格源没有几何 —— 请选择 CSV 交付")

    # ── 字段类型 / 值域（Shapefile 受限面）──────────────────────────────
    if fmt == "shapefile_zip":
        fmap, fmap_losses = _shapefile_field_map(selected, fields_sel.field_map)
        out.field_map = fmap
        for l in fmap_losses:
            out.losses.append(l)
            if l.requires_ack and l.code not in out.required_acks:
                out.required_acks.append(l.code)
        bool_fields = [f["name"] for f in snapshot.fields
                       if f.get("inferred_type") == "bool" and f["name"] in selected]
        if bool_fields:
            out.add(dl_contracts.LOSS_BOOL_TO_STRING,
                    f"DBF 无布尔类型：{len(bool_fields)} 个布尔字段按 true/false "
                    "字符串写出", fields=bool_fields[:20])
        # 大整数：32 位 DBF 界外 → 字符串保精度（不静默 float 化）
        def _is_big_int(sample: str) -> bool:
            t = sample.strip()
            if t.startswith("-"):
                t = t[1:]
            if not t.isdigit():
                return False
            return abs(int(t)) > 2 ** 31 - 1

        big_int_fields = [f["name"] for f in snapshot.fields
                          if f.get("inferred_type") == "int" and f["name"] in selected
                          and any(_is_big_int(v) for v in (f.get("sample_values") or [])
                                  if isinstance(v, str))]
        if big_int_fields:
            out.add(dl_contracts.LOSS_INT64_DOWNGRADE,
                    "超出 32 位整数范围的值按字符串写出（保精度，不做数值降级）",
                    fields=big_int_fields[:20], requires_ack=True)

    # ── 复杂/嵌套值 ─────────────────────────────────────────────────────
    if complex_fields and fmt != "geojson":
        code = (dl_contracts.LOSS_NESTED_VALUE_JSON_ENCODED
                if any(f.get("inferred_type") == "complex" for f in snapshot.fields)
                else dl_contracts.LOSS_COMPLEX_VALUE_JSON_ENCODED)
        out.add(code,
                f"{len(complex_fields)} 个字段含嵌套/列表值 —— 按 JSON 字符串编码"
                "（结构保留，值不再是非字符串标量）",
                fields=complex_fields[:20], requires_ack=(fmt == "csv"))

    # ── CSV 特有 ────────────────────────────────────────────────────────
    if fmt == "csv":
        if snapshot.is_table or not fields_sel.include_geometry:
            out.add(dl_contracts.LOSS_GEOMETRY_OMITTED,
                    "CSV 交付不含几何（表格源/或显式关闭）；几何原文保留在源引用")
        if snapshot.is_table and fields_sel.include_geometry:
            # 表格源无几何可附 —— 披露为不可得而非静默
            out.add(dl_contracts.LOSS_GEOMETRY_OMITTED,
                    "表格源没有几何列 —— 输出仅属性")
        if crs_spec.coordinate_precision is not None:
            out.add(dl_contracts.LOSS_COORDINATE_PRECISION_REDUCED,
                    f"WKT 坐标小数位将削减到 {crs_spec.coordinate_precision} 位",
                    requires_ack=True)
    if spreadsheet_safe_needs(spreadsheet_safe, fmt):
        out.add(dl_contracts.LOSS_FORMULA_PREFIX_SANITIZED,
                "spreadsheet-safe 模式：以 = + - @ TAB CR 开头的单元格前加单引号"
                "（变换逐条记录进回执；原始值保留在源引用中）")

    return out


def _crs_differs(source: str, target: str) -> bool:
    def _norm(c: str) -> str:
        c = (c or "").strip().upper()
        if c in ("WGS84", "OGC:CRS84", "EPSG:4326"):
            return "EPSG:4326"
        return c
    return _norm(source) != _norm(target)


def spreadsheet_safe_needs(spec_safe: bool, fmt: str) -> bool:
    return bool(spec_safe and fmt == "csv")


FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def sanitize_csv_cell(value: str) -> Tuple[str, bool]:
    """spreadsheet-safe 单元格变换：公式前缀（= + - @ TAB CR）前加 "'"。

    返回 (新值, 是否变换)。仅当模式开启时由 writer 调用；receipt 记录
    变换计数（LOSS_FORMULA_PREFIX_SANITIZED）。
    """
    if value and value[:1] in FORMULA_PREFIXES:
        return "'" + value, True
    if value and ("\t" in value or "\r" in value):
        return value.replace("\t", " ").replace("\r", " ").replace("\n", " "), True
    return value, False
