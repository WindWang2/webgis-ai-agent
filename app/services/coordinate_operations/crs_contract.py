"""G45 WP03 — CRS 规范描述合同：轴、单位、维度、epoch。

三类事实分开，绝不混写：

1. **官方轴序**（EPSG 登记，如 EPSG:4326 为 lat-first）—— 仅展示用；
2. **载体/执行约定** —— 本系统全部载体（GeoJSON、GeoParquet WKB、会话 FC）
   一律传统 GIS ``x=east/y=north`` 序；执行链恒 ``always_xy=True``；
3. **单位** —— 从 CRS 权威 axis unit 读取（metre / US survey foot / degree
   的换算因子原样披露，不做静默换算）。

维度政策（typed，不猜）：

- 静态 2D —— 核心支持；
- Z（第三维）—— ``preserve_unconverted``：仅在载体逐值保真（round-trip 读写
  数值完全一致）时保留，回执明示 horizontal-only；
- M / 第四维 —— typed unsupported（无保真路径不实现）；
- 垂向 / compound CRS、动态 datum（coordinate epoch 域）—— typed unsupported
  （缺真实垂向基准变换路径，不把保留 Z 冒称高程基准转换）。

canonical digest：对规范化描述 JSON 取 sha256 —— 计划绑定源/目标 CRS 身份的
稳定锚（WKT 串可能含格式噪声，canonical JSON 不含随机时间戳）。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

from app.services.coordinate_operations.contracts import CoordinateOperationError


def _crs_from_input(raw: Any) -> Any:
    """解析用户/载体输入 → pyproj.CRS（失败 → typed crs_unknown）。"""
    from pyproj import CRS

    try:
        return CRS.from_user_input(raw)
    except Exception as e:
        raise CoordinateOperationError(
            "crs_unknown", f"无法解析 CRS 标识: {raw!r}（{e}）") from e


def _axis_info(crs: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for ax in crs.axis_info:
        out.append({
            "name": ax.name,
            "abbrev": ax.abbrev,
            "direction": ax.direction,
            "unit_name": ax.unit_name,
            "unit_conversion_factor": ax.unit_conversion_factor,
        })
    return out


def _bounded_str(s: Any, limit: int = 400) -> str:
    try:
        t = str(s)
    except Exception:  # noqa: BLE001
        return ""
    return t if len(t) <= limit else t[: limit - 1] + "…"


def describe_crs(raw: Any) -> Dict[str, Any]:
    """输入 CRS → 规范描述（identity/axis/unit/dimension/epoch + digest）。"""
    crs = _crs_from_input(raw)

    axis = _axis_info(crs)
    dimension = {
        "is_geographic": bool(crs.is_geographic),
        "type_name": crs.type_name,
        "axis_count": len(axis),
        # 2D 平面/地理 → supported；3D/compound/垂向 → unsupported（分派见下）
    }

    datum = getattr(crs, "datum", None)
    dynamic = False
    if datum is not None:
        try:
            # pyproj 3.8 Datum 无 is_dynamic()；动态基准在 WKT2 里带
            # DYNAMIC[FRAMEEPOCH[...]]（如 ITRF2014）—— 以此标记为准。
            dynamic = "DYNAMIC[" in datum.to_wkt()
        except Exception:  # noqa: BLE001 — 元数据不可得按非动态处理并留痕
            dynamic = False
    # compound/vertical 显式拆解（单 CRS 之外的垂向分量）
    subcompound = []
    if crs.type_name == "Compound CRS":
        try:
            subcompound = [p.name for p in crs.sub_crs_list]
        except Exception:  # noqa: BLE001
            subcompound = []
    dimension["is_dynamic_datum"] = dynamic
    dimension["compound_components"] = subcompound

    supported, unsupported_reason = _dimension_support(crs, dynamic, subcompound)

    aou = None
    try:
        a = crs.area_of_use
        if a is not None:
            aou = {
                "name": _bounded_str(a.name, 200),
                "bounds": [a.west, a.south, a.east, a.north],
            }
    except Exception:  # noqa: BLE001
        aou = None

    # 身份：authority code 优先，缺省退 WKT 名称（不猜代码）。
    authority = None
    sid = None
    try:
        sid = crs.to_authority()
    except Exception:  # noqa: BLE001
        sid = None
    if sid:
        authority = {"auth": sid[0], "code": sid[1]}
    ident = f"{sid[0]}:{sid[1]}" if sid else None

    canonical = {
        "identity": ident,
        "name": crs.name,
        "type_name": crs.type_name,
        "axis": axis,
        "dimension": dimension,
        "area_of_use": aou,
        "wkt2_sha256": hashlib.sha256(crs.to_wkt().encode("utf-8")).hexdigest(),
    }
    digest = hashlib.sha256(json.dumps(
        canonical, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()

    return {
        "input": raw if isinstance(raw, str) else str(raw),
        "identity": ident,
        "authority": authority,
        "name": crs.name,
        "type_name": crs.type_name,
        "axis_official": axis,
        "carrier_convention": "x=east/y=north (traditional GIS; always_xy)",
        "dimension": dimension,
        "dimension_supported": supported,
        "dimension_unsupported_reason": unsupported_reason,
        "area_of_use": aou,
        "wkt2_sha256": canonical["wkt2_sha256"],
        "canonical_sha256": digest,
    }


def _dimension_support(crs: Any, dynamic: bool,
                       compound: List[str]) -> tuple[bool, Optional[str]]:
    """维度支持裁决：静态 2D（含 Z=preserve_unconverted 的 3D 载体）supported；
    compound/垂向/动态 datum/M·第四维 typed 拒绝。顺序即语义：dynamic 先于
    3D 允许分支（ITRF2014 这类 Geographic 3D + dynamic 必须落拒绝）。"""
    if compound:
        return False, (
            "compound/vertical CRS 需要垂向基准变换路径，本域未实现"
            "（horizontal-only 不冒称完整 compound 转换）")
    if dynamic:
        return False, (
            "dynamic datum（coordinate epoch 域）未实现；"
            "请改用静态基准 CRS（本域不做 epoch 变换，也不静默退化为无 epoch）")
    n_axis = len(crs.axis_info)
    if n_axis > 3:
        return False, f"axis_count={n_axis}（M/第四维无逐值保真载体路径）"
    if crs.type_name == "Vertical CRS":
        return False, "Vertical CRS 不是本域水平坐标操作的合法源/目标"
    # 静态 2D / 静态 Geographic 3D / Projected（axis≤3）：支持；
    # 3D 的 Z 走 preserve_unconverted 政策（执行链显式披露）。
    return True, None


def crs_unit_summary(desc: Dict[str, Any]) -> Dict[str, Any]:
    """单位摘要（面向回执/UI：米、US survey foot、degree 不混淆）。"""
    axis = desc.get("axis_official") or []
    units = sorted({a["unit_name"] for a in axis})
    return {
        "units": units,
        "is_metric_metre": units == ["metre"],
        "note": "单位从 CRS 权威 axis 读取；换算因子见 axis_official",
    }


def parse_crs_for_execution(raw: Any) -> Any:
    """执行链 CRS 解析（describe 已验维度；此处只解析，供 Transformer 用）。"""
    return _crs_from_input(raw)
