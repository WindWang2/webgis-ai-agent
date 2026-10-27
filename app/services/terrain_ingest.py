"""会话 DEM 摄入：校验 → 资格 receipt → session raster ref 注册（G3D-V2-A A1/A2）。

闭合 #1771 之后的核心断链：上传的 GeoTIFF 此前被 ingest lane 显式跳过
（upload.py ``vector data only``），永远无法成为 terrain-tiles 路由可服务
的 session raster ref。本模块在**摄入期**（而非渲染期）完成判定并留下
机器可读 receipt：

资格算法（fail-closed，逐条留 QualificationReason）：
- 多波段 / 无 CRS / 旋转网格 / 单位不可换算 / 非有限值占比>1% →
  ``unavailable``（该文件不可作为 terrain 源，诚实拒绝）；
- 未知垂直基准 / 单位为假定米 / synthetic tag / 异常值占比>0.1% →
  ``display_only``（可渲染，绝不进科学分析门）；
- ``analysis_grade`` 仅当：单波段 + CRS 可解析 + 北向上 + 非 synthetic +
  垂直基准**声明**（表单或 compound CRS）+ 单位确认（声明或 band tag）+
  质量门槛全过。

诚实红线：
- 绝不把未知垂直 datum 偷换为 EGM96/椭球高（无 geoid 模型，只记录）；
- 非米单位（ft / US survey ft）生成**换算副本**并留双 sha256 证据
  （DECISIONS D2），源文件不动，渲染路径零语义变更；
- nodata 洞 / 非有限像素 / 异常尖峰只统计披露，不插值伪造连续测量。

资源纪律：剖析读取降采样至最长边 ≤2048（#595/#1797 同款）；换算副本按
GDAL block 窗口流式读写（块内 NumPy 向量化，无逐像元 Python 循环）；
上传本体 200MB 上界即副本上界。

reason code 全词表（_ALL_REASON_CODES 锁定 + 契约测试逐码核对）：
  unavailable 级：TERRAIN_REQUIRES_SINGLE_BAND / TERRAIN_INGEST_NO_CRS /
    CRS_NOT_RESOLVABLE / ROTATED_GRID_UNSUPPORTED / UNIT_NOT_CONVERTIBLE /
    CORRUPT_ELEVATION / SOURCE_FILE_MISSING
  display_only 级：UNKNOWN_VERTICAL_DATUM / UNIT_ASSUMED_M /
    SYNTHETIC_DEM_DISPLAY_ONLY / ELEVATION_OUTLIERS /
    ELEVATION_EXTREME_OUTLIERS / ELEVATION_OUT_OF_RANGE /
    VERTICAL_UNIT_CONFLICT
  披露级（不降级）：NON_FINITE_PIXELS / ANTIMERIDIAN_CROSSING /
    POLAR_EXTENT_CLIPPED
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import math
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import rasterio
from rasterio.warp import transform_bounds

from app.contracts.terrain_sources import (
    QualificationReason,
    TerrainSourceReceipt,
    UnitConversionEvidence,
)

#: reason code 全词表（契约锁定：测试断言 emit 的每个码都在此集合内，
#: 且模块 docstring 与本集合一致 —— 消费方按 code 全枚举披露）。
_ALL_REASON_CODES: frozenset[str] = frozenset({
    # unavailable 级
    "TERRAIN_REQUIRES_SINGLE_BAND", "TERRAIN_INGEST_NO_CRS", "CRS_NOT_RESOLVABLE",
    "ROTATED_GRID_UNSUPPORTED", "UNIT_NOT_CONVERTIBLE", "CORRUPT_ELEVATION",
    "SOURCE_FILE_MISSING",
    # display_only 级
    "UNKNOWN_VERTICAL_DATUM", "UNIT_ASSUMED_M", "SYNTHETIC_DEM_DISPLAY_ONLY",
    "ELEVATION_OUTLIERS", "ELEVATION_EXTREME_OUTLIERS", "ELEVATION_OUT_OF_RANGE",
    "VERTICAL_UNIT_CONFLICT",
    # 披露级（记录不降级）
    "NON_FINITE_PIXELS", "ANTIMERIDIAN_CROSSING", "POLAR_EXTENT_CLIPPED",
})

logger = logging.getLogger(__name__)

#: 会话 raster ref 的 payload kind 标记（terrain-tiles 路由读 file_path）。
TERRAIN_REF_KIND = "terrain_dem"

#: 已知可换算单位 → 精确米制因子（DeclaredVerticalUnit 词表的执行面）。
_UNIT_FACTORS: Dict[str, float] = {
    "m": 1.0,
    "ft": 0.3048,
    "us_survey_ft": 1200.0 / 3937.0,
}

#: GeoTIFF band unit tag → 规范单位词表的别名表（GDAL 常见拼写）。
_TAG_UNIT_ALIASES: Dict[str, str] = {
    "m": "m", "meter": "m", "metre": "m", "meters": "m", "metres": "m",
    "ft": "ft", "foot": "ft", "feet": "ft", "international foot": "ft",
    "us_survey_ft": "us_survey_ft", "us survey ft": "us_survey_ft",
    "us survey foot": "us_survey_ft", "u.s. survey foot": "us_survey_ft",
    "us_survey_foot": "us_survey_ft", "us foot": "us_survey_ft",
    "u.s. foot": "us_survey_ft",
}

#: plausible elevation 包络（米）：死海底 ≈ -430，珠峰 8849 —— 外侧即 outlier。
_OUTLIER_ENVELOPE_M = (-500.0, 8850.0)
#: Terrarium 可编码域（terrain_encoding 守卫同锚）。
_ENCODABLE_ENVELOPE_M = (-32768.0, 32767.996)
#: 非有限值占有效样本比例超过此值 → unavailable（数据损坏）。
_MAX_NON_FINITE_FRACTION = 0.01
#: outlier 占有效样本比例超过此值 → display_only。
_MAX_OUTLIER_FRACTION = 0.001
#: 剖析降采样最长边（与 raster_tile_service._STATS_MAX_SIDE 同纪律）。
_PROFILE_MAX_SIDE = 2048
#: 幂等扫描的 terrain ref 上限（防极端会话下 O(n) get 放大）。
_IDEMPOTENCY_SCAN_CAP = 64


def _sha256_file(path: Path) -> str:
    """流式文件指纹（1MB 分块，绝不整读 200MB 进内存）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _resolve_declared_unit(declared: Optional[str]) -> tuple[Optional[str], Optional[QualificationReason]]:
    """用户表单声明的单位 → 规范词表；非法声明如实拒绝（不猜）。"""
    if declared is None or declared == "":
        return None, None
    key = str(declared).strip().lower()
    if key not in _UNIT_FACTORS:
        return None, QualificationReason(
            code="UNIT_NOT_CONVERTIBLE",
            detail=(
                f"声明的垂直单位 {declared!r} 不在可换算词表 "
                "(m/ft/us_survey_ft)；拒绝注册而非按未知单位伪造高程"
            ),
        )
    return key, None


def _resolve_tag_unit(tag: Optional[str]) -> tuple[Optional[str], bool]:
    """GeoTIFF band unit tag → 规范词表；未知 tag 返回 (None, 曾尝试)。"""
    if not tag:
        return None, False
    key = str(tag).strip().lower()
    return _TAG_UNIT_ALIASES.get(key), True


def _vertical_datum_from_crs(crs: Any) -> Optional[str]:
    """compound/vertical CRS 的 best-effort 提取（无 geoid 模型，只读名字）。

    pyproj ``CRS.is_vertical`` 存在（实测 3.7）：compound CRS 的 sub_crs_list
    里取 vertical 分量的 name。纯粹水平 CRS（绝大多数 DEM 文件）返回 None
    —— 诚实：文件没写就是没写。
    """
    try:
        from pyproj import CRS as PyprojCRS

        parsed = PyprojCRS.from_wkt(crs.to_wkt())
        if not parsed.is_vertical:
            for sub in getattr(parsed, "sub_crs_list", []) or []:
                if getattr(sub, "is_vertical", False):
                    return str(sub.name)
            return None
        return str(parsed.name)
    except Exception:  # noqa: BLE001 — best-effort 元数据读取，失败=不可用
        return None


def _north_up(transform: Any) -> bool:
    """北向上网格判定：无旋转/错切（b==0 且 d==0）+ 列向东（a>0）+ 行向南下（e<0）。

    rasterio ``Affine(a,b,c,d,e,f)``：x = a·col + b·row + c，y = d·col + e·row + f。
    北向上（左上原点）⇒ b=d=0、a>0、e<0。render_terrarium_tile 的
    from_bounds 窗口推导假设轴对齐；旋转网格走该路径会取错窗口 —— 拒绝
    而非错读（DECISIONS D10）。
    """
    return (
        float(transform.b) == 0.0
        and float(transform.d) == 0.0
        and float(transform.a) > 0.0
        and float(transform.e) < 0.0
    )


def _gsd_meters(src: rasterio.DatasetReader, bbox_4326: tuple[float, float, float, float]) -> Optional[float]:
    """源网格 GSD（米）。地理 CRS 按中心纬度近似（111320 m/deg，cos 修正）。"""
    try:
        xres_crs_units = abs(float(src.transform.a))
        if src.crs is not None and src.crs.is_geographic:
            center_lat = math.radians((bbox_4326[1] + bbox_4326[3]) / 2.0)
            return xres_crs_units * 111320.0 * max(math.cos(center_lat), 1e-9)
        factor = 1.0
        try:
            _, factor = src.crs.linear_units_factor  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001 — 无单位信息按米（投影 CRS 惯例）
            factor = 1.0
        return xres_crs_units * float(factor)
    except Exception:  # noqa: BLE001
        return None


def _convert_to_meters(
    src_path: Path,
    out_path: Path,
    factor: float,
    declared_nodata: Optional[float],
) -> None:
    """非米单位 → 米换算副本（block 窗口流式，向量化；DECISIONS D2）。

    - 副本统一 nodata=-9999（哨兵像素不参与换算，防换算后与真实高程撞值）；
    - tags（含 SYNTHETIC_DEM）原样拷贝，副本保持自描述；
    - 原子发布：先写 .tmp 再 os.replace（#1634 同纪律）。
    """
    import os

    tmp_path = out_path.with_name(f".{out_path.name}.tmp")
    with rasterio.open(src_path) as src:
        profile = src.profile.copy()
        profile.update(dtype="float32", nodata=-9999.0)
        with rasterio.open(tmp_path, "w", **profile) as dst:
            for _, win in src.block_windows(1):
                arr = src.read(1, window=win).astype(np.float32)
                with np.errstate(invalid="ignore"):
                    nodata_mask = ~np.isfinite(arr)
                    if declared_nodata is not None:
                        nodata_mask |= arr == float(declared_nodata)
                    nodata_mask |= arr == -9999.0
                converted = arr * np.float32(factor)
                out = np.where(nodata_mask, np.float32(-9999.0), converted)
                dst.write(out.astype(np.float32), 1, window=win)
            tags = src.tags()
            if tags:
                dst.update_tags(**tags)
            band_tags = src.tags(1)
            if band_tags:
                dst.update_tags(1, **band_tags)
            descriptions = src.descriptions or (None,)
            dst.set_band_description(1, descriptions[0] or "")
    os.replace(tmp_path, out_path)


def validate_dem(
    path: Path,
    *,
    declared_vertical_datum: Optional[str] = None,
    declared_vertical_unit: Optional[str] = None,
    content_sha256: Optional[str] = None,
) -> tuple[TerrainSourceReceipt, Optional[Path]]:
    """DEM 摄入校验（同步 CPU+IO 密集 —— 调用方必须在 worker 线程执行）。

    返回 ``(receipt, normalized_path)``：normalized_path 非空 = 应注册的
    文件（原文件或换算副本）；unavailable 时为 None。
    """
    reasons: list[QualificationReason] = []
    if not path.is_file():
        return TerrainSourceReceipt(
            qualification="unavailable",
            reasons=(QualificationReason(code="SOURCE_FILE_MISSING", detail=f"DEM 文件不存在: {path.name}"),),
            content_sha256=content_sha256 or "",
        ), None

    unit_key: Optional[str]
    unit_reason: Optional[QualificationReason]
    unit_key, unit_reason = _resolve_declared_unit(declared_vertical_unit)
    if unit_reason is not None:
        return TerrainSourceReceipt(
            qualification="unavailable",
            reasons=(unit_reason,),
            content_sha256=content_sha256 or "",
        ), None

    with rasterio.open(path) as src:
        # —— 结构门（unavailable 级） ——
        if src.count != 1:
            reasons.append(QualificationReason(
                code="TERRAIN_REQUIRES_SINGLE_BAND",
                detail=f"高程源必须单波段，实际 {src.count} 波段；拒绝猜测哪一波段是高程",
            ))
            return _receipt_unavailable(reasons, content_sha256), None
        if src.crs is None:
            reasons.append(QualificationReason(
                code="TERRAIN_INGEST_NO_CRS",
                detail="栅格无 CRS；地形放错位置即伪造垂直证据，拒绝注册",
            ))
            return _receipt_unavailable(reasons, content_sha256), None
        if not _north_up(src.transform):
            reasons.append(QualificationReason(
                code="ROTATED_GRID_UNSUPPORTED",
                detail="旋转/非北向上网格不受支持（窗口推导假设轴对齐）；拒绝而非错读",
            ))
            return _receipt_unavailable(reasons, content_sha256), None
        try:
            b = src.bounds
            bbox_src = (b.left, b.bottom, b.right, b.top)
            bbox_4326 = tuple(
                float(v) for v in transform_bounds(src.crs, "EPSG:4326", *bbox_src, densify_pts=21)
            )
        except Exception as err:  # noqa: BLE001 — CRS 存在但不可解析（含 #1805 邻域）
            reasons.append(QualificationReason(
                code="CRS_NOT_RESOLVABLE",
                detail=f"CRS 声明存在但无法解析到 EPSG:4326 边界（{err}）；拒绝注册",
            ))
            return _receipt_unavailable(reasons, content_sha256), None

        # —— synthetic 自描述标记（永久 display_only） ——
        synthetic = str(src.tags().get("SYNTHETIC_DEM", "")) == "1" or str(src.tags(1).get("SYNTHETIC_DEM", "")) == "1"
        if synthetic:
            reasons.append(QualificationReason(
                code="SYNTHETIC_DEM_DISPLAY_ONLY",
                detail="文件自带 SYNTHETIC_DEM=1 标记（自描述合成数据）；永久 display_only，不可分析",
            ))

        # —— 单位解析（声明 > band tag > 假定米；声明与可解析 tag 冲突 = 披露） ——
        tag_unit, tag_present = _resolve_tag_unit((src.units or [""])[0] if src.units else None)
        if unit_key is not None:
            vertical_unit_source = "declared"
            if tag_unit is not None and tag_unit != unit_key:
                # 冲突不静默压倒：声明与文件证据矛盾时按声明换算，但资格
                # 封顶 display_only（评审 P2 —— 3.28 倍高程差不可无声放行）。
                reasons.append(QualificationReason(
                    code="VERTICAL_UNIT_CONFLICT",
                    detail=(
                        f"声明的垂直单位 {unit_key!r} 与文件 band unit tag "
                        f"{src.units[0]!r}（解析为 {tag_unit!r}）冲突；按声明处理，"
                        "仅显示 —— 请核对后以一致声明重新摄入"
                    ),
                ))
        elif tag_unit is not None:
            unit_key = tag_unit
            vertical_unit_source = "geotiff_tag"
        else:
            unit_key = "m"
            vertical_unit_source = "assumed"
            if tag_present:
                reasons.append(QualificationReason(
                    code="UNIT_NOT_CONVERTIBLE",
                    detail=f"GeoTIFF band 单位 {src.units[0]!r} 不在可换算词表；拒绝注册而非伪造高程",
                ))
                return _receipt_unavailable(reasons, content_sha256), None
            reasons.append(QualificationReason(
                code="UNIT_ASSUMED_M",
                detail="未声明垂直单位且文件无单位 tag，按惯例假定米；分析级需声明后重新摄入",
            ))
        factor = _UNIT_FACTORS[unit_key]

        # —— 剖析统计（降采样窗口读，绝不整幅解码） ——
        declared_nodata = src.nodata
        scale = min(1.0, _PROFILE_MAX_SIDE / float(max(src.width, src.height)))
        out_shape = (max(1, int(round(src.height * scale))), max(1, int(round(src.width * scale))))
        sample = src.read(1, out_shape=out_shape).astype(np.float64)
        with np.errstate(invalid="ignore"):
            finite = np.isfinite(sample)
        non_finite = int((~finite).sum())
        # 哨兵（-9999）与声明 nodata 都按"洞"统计：洞是披露事实，不是插值理由。
        sentinel_mask = finite & (sample == -9999.0)
        nodata_mask = sentinel_mask.copy()
        if declared_nodata is not None:
            nodata_mask = nodata_mask | (finite & (sample == float(declared_nodata)))
        effective = finite & ~nodata_mask
        effective_count = int(effective.sum())
        if effective_count == 0:
            reasons.append(QualificationReason(
                code="CORRUPT_ELEVATION",
                detail="全部像素均为 nodata/非有限值；没有可渲染的高程",
            ))
            return _receipt_unavailable(reasons, content_sha256), None
        vals = sample[effective] * factor
        elev_min = float(vals.min())
        elev_max = float(vals.max())
        non_finite_fraction = non_finite / float(sample.size)
        nodata_hole_fraction = float(nodata_mask.sum()) / float(sample.size)
        outlier_mask = (vals < _OUTLIER_ENVELOPE_M[0]) | (vals > _OUTLIER_ENVELOPE_M[1])
        outlier_count = int(outlier_mask.sum())
        outlier_fraction = outlier_count / float(effective_count)
        extreme_mask = (np.abs(vals) > 15000.0)
        extreme_count = int(extreme_mask.sum())

        if non_finite_fraction > _MAX_NON_FINITE_FRACTION:
            reasons.append(QualificationReason(
                code="CORRUPT_ELEVATION",
                detail=f"非有限值（NaN/Inf）占 {non_finite_fraction:.2%} > 1%；数据损坏，拒绝注册",
            ))
            return _receipt_unavailable(reasons, content_sha256), None
        if non_finite > 0:
            reasons.append(QualificationReason(
                code="NON_FINITE_PIXELS",
                detail=f"{non_finite} 个非有限像素将渲染为透明（nodata 语义），不伪造高程",
            ))
        if outlier_fraction > _MAX_OUTLIER_FRACTION:
            reasons.append(QualificationReason(
                code="ELEVATION_OUTLIERS",
                detail=f"包络 {_OUTLIER_ENVELOPE_M} 外高程占 {outlier_fraction:.3%} > 0.1%；尖峰污染，仅显示",
            ))
        if extreme_count > 0:
            reasons.append(QualificationReason(
                code="ELEVATION_EXTREME_OUTLIERS",
                detail=f"{extreme_count} 个 |高程|>15000m 极端值（编码域边缘）；披露为仅显示",
            ))
        if elev_min < _ENCODABLE_ENVELOPE_M[0] or elev_max > _ENCODABLE_ENVELOPE_M[1]:
            reasons.append(QualificationReason(
                code="ELEVATION_OUT_OF_RANGE",
                detail=f"高程 [{elev_min:.1f}, {elev_max:.1f}]m 超 Terrarium 可编码域；对应瓦片将拒绝渲染",
            ))

        # —— 垂直基准（声明 > compound CRS > 不可用） ——
        datum_from_tag = _vertical_datum_from_crs(src.crs)
        if declared_vertical_datum:
            vertical_datum = str(declared_vertical_datum).strip()
            vertical_datum_source = "declared"
        elif datum_from_tag:
            vertical_datum = datum_from_tag
            vertical_datum_source = "geotiff_tag"
        else:
            vertical_datum = None
            vertical_datum_source = "unavailable"
            reasons.append(QualificationReason(
                code="UNKNOWN_VERTICAL_DATUM",
                detail="垂直基准未声明且文件不可读出；不偷换为 EGM96/椭球高，仅显示",
            ))

        # —— 空间披露（antimeridian / 极区） ——
        minx, miny, maxx, maxy = bbox_4326
        if minx > maxx:
            reasons.append(QualificationReason(
                code="ANTIMERIDIAN_CROSSING",
                detail="覆盖跨反子午线（4326 左界>右界）；按瓦片局部重投影处理，无伪造",
            ))
        if max(miny, -90.0) < -85.0511 or min(maxy, 90.0) > 85.0511:
            reasons.append(QualificationReason(
                code="POLAR_EXTENT_CLIPPED",
                detail="覆盖触及极区（Web Mercator ±85.05°外无瓦片）；极区部分不可渲染",
            ))

        gsd_m = _gsd_meters(src, bbox_4326)
        width, height = src.width, src.height
        crs_str = src.crs.to_string()

    # —— 换算副本（非米单位） ——
    normalized_path: Optional[Path] = path
    unit_conversion: Optional[UnitConversionEvidence] = None
    if factor != 1.0:
        out_path = path.with_name(f"{path.stem}.dem-m.tif")
        _convert_to_meters(path, out_path, factor, declared_nodata)
        normalized_path = out_path
        unit_conversion = UnitConversionEvidence(
            from_unit=unit_key,  # type: ignore[arg-type]
            factor=factor,
            input_sha256=content_sha256 or _sha256_file(path),
            output_sha256=_sha256_file(out_path),
        )

    # —— 资格判定（顺序即语义：unavailable 已提前返回） ——
    blocking = {r.code for r in reasons}
    display_only_codes = {
        "SYNTHETIC_DEM_DISPLAY_ONLY", "UNKNOWN_VERTICAL_DATUM", "UNIT_ASSUMED_M",
        "ELEVATION_OUTLIERS", "ELEVATION_EXTREME_OUTLIERS", "ELEVATION_OUT_OF_RANGE",
        "VERTICAL_UNIT_CONFLICT",
    }
    if blocking & display_only_codes:
        qualification = "display_only"
    else:
        qualification = "analysis_grade"

    receipt = TerrainSourceReceipt(
        qualification=qualification,
        reasons=tuple(reasons),
        crs=crs_str,
        bbox_epsg4326=(minx, miny, maxx, maxy),  # type: ignore[arg-type]
        width=width,
        height=height,
        ground_sample_distance_m=gsd_m,
        vertical_unit="m",
        vertical_unit_source=vertical_unit_source,  # type: ignore[arg-value]
        vertical_datum=vertical_datum,
        vertical_datum_source=vertical_datum_source,  # type: ignore[arg-value]
        unit_conversion=unit_conversion,
        nodata=-9999.0 if factor != 1.0 else declared_nodata,
        nodata_hole_fraction=nodata_hole_fraction,
        non_finite_pixels=non_finite,
        outlier_pixels=outlier_count,
        outlier_envelope_m=_OUTLIER_ENVELOPE_M,
        elevation_min_m=elev_min,
        elevation_max_m=elev_max,
        synthetic=synthetic,
        content_sha256=content_sha256 or "",
        registered_at="",
    )
    return receipt, normalized_path


def _receipt_unavailable(reasons: list[QualificationReason], content_sha256: Optional[str]) -> TerrainSourceReceipt:
    return TerrainSourceReceipt(qualification="unavailable", reasons=tuple(reasons), content_sha256=content_sha256 or "")


def build_terrain_payload(receipt: TerrainSourceReceipt, file_path: Path, name: Optional[str] = None) -> Dict[str, Any]:
    """terrain ref 的 session store payload（terrain-tiles 路由读 file_path）。"""
    from datetime import datetime, timezone

    return {
        "kind": TERRAIN_REF_KIND,
        "file_path": str(file_path),
        "name": name or file_path.name,
        "content_sha256": receipt.content_sha256,
        "receipt": receipt.model_dump(mode="json"),
        "registered_at": datetime.now(timezone.utc).isoformat(),
    }


def _resolve_manager(manager: Any = None) -> Any:
    """lane 内懒解析 store（测试可注入独立实例，缺省全局单例）。"""
    if manager is not None:
        return manager
    from app.services.session_data import session_data_manager

    return session_data_manager


async def find_existing_terrain_ref(
    session_id: str, content_sha256: str, manager: Any = None
) -> Optional[str]:
    """幂等：同会话同内容（sha256）已注册 → 复用既有 ref。

    扫描有界（≤_IDEMPOTENCY_SCAN_CAP 条 terrain ref）：list_refs 只给内存
    态 ref 清单，逐条 get payload 比对 content_sha256。
    """
    if not content_sha256:
        return None
    mgr = _resolve_manager(manager)
    try:
        refs = await mgr.list_refs(session_id)
    except Exception:  # noqa: BLE001 — 幂等探测失败按未命中处理（不阻断注册）
        return None
    terrain_refs = [r for r in refs if r.startswith("ref:terrain-")]
    for ref_id in terrain_refs[:_IDEMPOTENCY_SCAN_CAP]:
        try:
            payload = await mgr.get(session_id, ref_id)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(payload, dict) and payload.get("content_sha256") == content_sha256:
            return ref_id
    return None


async def register_session_terrain_source(
    session_id: str,
    path: Path,
    *,
    declared_vertical_datum: Optional[str] = None,
    declared_vertical_unit: Optional[str] = None,
    content_sha256: Optional[str] = None,
    idempotent: bool = True,
    manager: Any = None,
    name: Optional[str] = None,
) -> Dict[str, Any]:
    """摄入入口（lane 语义：绝不 raise，错误落 ``error`` 字段）。

    返回 ``{ok, ref_id, receipt, warnings, error, duplicate}``。
    ``unavailable`` 是诚实结果不是失败：``ok=False``、receipt 照发。
    """
    out: Dict[str, Any] = {
        "ok": False, "ref_id": None, "receipt": None,
        "warnings": [], "error": None, "duplicate": False,
    }
    if not session_id:
        out["error"] = "terrain registration requires session_id"
        return out
    mgr = _resolve_manager(manager)
    try:
        if idempotent and content_sha256:
            existing = await find_existing_terrain_ref(session_id, content_sha256, manager=mgr)
            if existing is not None:
                payload = await mgr.get(session_id, existing)
                receipt_data = (payload or {}).get("receipt") if isinstance(payload, dict) else None
                out.update(
                    ok=True, ref_id=existing, duplicate=True,
                    receipt=TerrainSourceReceipt.model_validate(receipt_data) if receipt_data else None,
                )
                return out

        receipt, normalized_path = await asyncio.to_thread(
            validate_dem,
            path,
            declared_vertical_datum=declared_vertical_datum,
            declared_vertical_unit=declared_vertical_unit,
            content_sha256=content_sha256,
        )
        out["receipt"] = receipt
        if receipt.qualification == "unavailable" or normalized_path is None:
            # unavailable：如实返回 receipt（可编程消费），不注册 ref。
            out["error"] = "; ".join(f"{r.code}: {r.detail}" for r in receipt.reasons)[:400]
            return out

        payload = build_terrain_payload(receipt, normalized_path, name=name)
        ref_id = await mgr.store(session_id, payload, prefix="terrain")
        out.update(ok=True, ref_id=ref_id)
        if receipt.qualification == "display_only":
            out["warnings"].append(
                "DEM 仅限显示（" + "; ".join(r.code for r in receipt.reasons) + "）"
            )
        return out
    except Exception as e:  # noqa: BLE001 — lane 纪律：任何失败不穿出
        logger.warning("terrain registration failed (session=%s): %s", session_id, e, exc_info=True)
        out["error"] = f"terrain registration failed: {e}"
        return out


def native_zoom_for_gsd(gsd_m: Optional[float], bbox_4326: Optional[tuple[float, float, float, float]]) -> int:
    """由 GSD 推导瓦片 maxzoom（256px 瓦片分辨率 ≥ 源网格分辨率）。

    赤道 Web Mercator 分辨率 156543.034 m/px；按中心纬度 cos 修正后
    ``z = log2(res_at_lat / gsd)``，钳制 [0, 20]（terrain 路由 z≤20；
    更高 zoom 由 MapLibre overzoom 承担）。
    """
    if not gsd_m or gsd_m <= 0 or not bbox_4326:
        return 14  # 与 ElevationSourceProfile 渲染缺省同锚
    center_lat = math.radians((bbox_4326[1] + bbox_4326[3]) / 2.0)
    res_at_lat = 156543.03392 * max(math.cos(center_lat), 1e-9)
    z = math.log2(res_at_lat / gsd_m)
    return max(0, min(20, int(math.floor(z))))


__all__ = [
    "TERRAIN_REF_KIND",
    "TerrainSourceReceipt",
    "build_terrain_payload",
    "find_existing_terrain_ref",
    "native_zoom_for_gsd",
    "register_session_terrain_source",
    "validate_dem",
]
