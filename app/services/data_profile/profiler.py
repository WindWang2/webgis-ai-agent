"""Dataset Profiler V3 —— 会话 ref / 栅格文件的有界剖析服务。

原则（§六 + 审计 Agent F）：
- **descriptor-first**：RefDescriptor 已含 type/null/min/max/样本 ——
  默认零扫描投影（PARTIAL）；只有调用方显式 ``deep=True`` 才读载荷
  补 mean/std/基数/坐标可行性（COMPLETE 或 SAMPLED）；
- **绑定修订**：剖析缓存键含 content_revision/content 指纹 —— 来源
  改写自动失效（§六：source revision 改变后失效）；
- **有界缓存**：进程内 LRU（256 条 / TTL 300s），与 ref_payload_cache
  同一预算哲学；
- **有界扫描**：深剖单趟 max_scan_rows（默认 50k），超出确定性步长
  采样；扫描在 to_thread 中执行，不阻塞事件循环。

栅格剖析走 rasterio（heavy 依赖）：降采样读（≤512 边）统计，绝不
全幅解压；依赖缺席 → FAILED 剖析 + 诊断（诚实，不虚构）。
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

from app.lib.data.profile import (
    DEFAULT_MAX_SCAN_ROWS,
    DatasetProfileV3,
    ProfileQuality,
    RasterBandStats,
    RasterProfileData,
    profile_features,
    profile_from_field_schema,
)
from app.lib.data.vocabulary import category_from_any

logger = logging.getLogger(__name__)

_PROFILE_CACHE_MAX_ENTRIES = 256
_PROFILE_CACHE_TTL_S = 300.0


def _category_from_geometry_types(geometry_types: List[str]) -> str:
    kinds = {str(g) for g in geometry_types}
    if kinds <= {""} :
        return "unknown"
    cat = category_from_any(geometry_kind=_dominant_kind(kinds))
    return cat.value if cat else "unknown"


def _dominant_kind(kinds: set) -> str:
    if {"Point", "MultiPoint"} & kinds:
        return "point"
    if {"Polygon", "MultiPolygon"} & kinds:
        return "polygon"
    if {"LineString", "MultiLineString"} & kinds:
        return "line"
    if "raster" in kinds:
        return "raster"
    return "unknown"


def shallow_profile_from_descriptor(ref: str, descriptor: Dict[str, Any]) -> DatasetProfileV3:
    """RefDescriptor → V3 浅投影（零扫描、PARTIAL 语义；模块级纯函数）。"""
    field_schema = descriptor.get("field_schema")
    vp, quality = profile_from_field_schema(
        field_schema if isinstance(field_schema, dict) else None,
        complete=bool(descriptor.get("field_schema_complete", True)),
        row_count=int(descriptor.get("feature_count") or 0),
        geometry_types=[str(g) for g in (descriptor.get("geometry_types") or [])],
        bbox=descriptor.get("bbox") if isinstance(descriptor.get("bbox"), list) else None,
        crs=str(descriptor.get("crs") or ""),
    )
    category = "raster" if descriptor.get("raster_capable") else _category_from_geometry_types(
        [str(g) for g in (descriptor.get("geometry_types") or [])]
    )
    return DatasetProfileV3(
        target_ref=ref,
        category=category,
        crs=str(descriptor.get("crs") or ""),
        extent=(
            [float(x) for x in descriptor.get("bbox")]
            if isinstance(descriptor.get("bbox"), list) and len(descriptor.get("bbox")) == 4
            else None
        ),
        vector=vp,
        profile_quality=quality,
        source_revision=int(descriptor.get("content_revision") or 0),
        diagnostics=[] if field_schema else ["no_field_schema_evidence"],
    )


# ── V4（ADR-0104 #4）：profile digest —— ArtifactContract.profile_ref 的
# 有界内联存储形状。生产方（register_tool_artifact / register_artifact）
# 在画像可得时附加；缺席恒 None（绝不虚构）。确定性：剔除时间戳、键序
# 稳定、字段清单封顶 —— 同一输入恒产出同一 digest。

_PROFILE_DIGEST_MAX_FIELDS = 12
_PROFILE_DIGEST_MAX_KEYS = 24


def profile_digest(
    profile: Optional[DatasetProfileV3],
    *,
    max_fields: int = _PROFILE_DIGEST_MAX_FIELDS,
) -> Optional[Dict[str, Any]]:
    """DatasetProfileV3 → 有界 digest（profile_ref 的诚实存储形状）。

    只保留 resolver/后续波次消费需要的有界字段（类别/质量/修订/CRS/范围/
    行数/几何族/字段类型+null 率/栅格维度/重复坐标证据）。``created_at``
    等时间性字段一律不进 digest（ADR 决定论约束：证据无时间戳）。
    画像缺席或无任何证据 → None。
    """
    if profile is None:
        return None
    vector = profile.vector
    raster = profile.raster
    table = profile.table
    quality_raw = getattr(profile, "profile_quality", None)
    quality = getattr(quality_raw, "value", quality_raw)
    digest: Dict[str, Any] = {
        "v": int(profile.profile_version),
        "category": str(profile.category or "")[:32],
        "quality": str(quality)[:16],
        "revision": int(profile.source_revision or 0),
    }
    if profile.crs:
        digest["crs"] = str(profile.crs)[:64]
    if isinstance(profile.extent, list) and len(profile.extent) == 4:
        try:
            digest["extent"] = [round(float(x), 6) for x in profile.extent]
        except (TypeError, ValueError):
            pass
    if vector is not None:
        digest["row_count"] = int(vector.row_count)
        if vector.geometry_types:
            digest["geometry_types"] = [str(g)[:32] for g in vector.geometry_types[:8]]
        if vector.temporal_fields:
            digest["temporal_fields"] = [str(t)[:64] for t in vector.temporal_fields[:8]]
        if vector.empty_geometry_count:
            digest["empty_geometry_count"] = int(vector.empty_geometry_count)
        if vector.impossible_coordinate_count:
            digest["impossible_coordinate_count"] = int(vector.impossible_coordinate_count)
        if getattr(vector, "scanned_rows", 0):
            # 重复坐标证据只在深扫口径下有效（浅投影不得虚构 0 = 无重复）。
            digest["duplicate_coordinate_count"] = int(
                getattr(vector, "duplicate_coordinate_count", 0) or 0)
            digest["unique_coordinate_count"] = int(
                getattr(vector, "unique_coordinate_count", 0) or 0)
        fields: Dict[str, Any] = {}
        for name in sorted(vector.fields.keys())[:max_fields]:
            fp = vector.fields[name]
            entry: Dict[str, Any] = {"t": str(getattr(fp, "dtype", ""))[:16]}
            rate = getattr(fp, "null_rate", None)
            if isinstance(rate, (int, float)) and not isinstance(rate, bool):
                entry["nr"] = round(float(rate), 4)
            fields[str(name)[:96]] = entry
        if fields:
            digest["fields"] = fields
        if vector.fields_truncated:
            digest["fields_truncated"] = True
    elif table is not None:
        digest["row_count"] = int(table.row_count)
        if table.columns:
            digest["columns"] = [
                str(c)[:96] for c in sorted(table.columns.keys())[:max_fields]
            ]
    if raster is not None:
        rshape: Dict[str, Any] = {}
        if raster.width is not None:
            rshape["width"] = int(raster.width)
        if raster.height is not None:
            rshape["height"] = int(raster.height)
        if raster.band_count is not None:
            rshape["band_count"] = int(raster.band_count)
        if rshape:
            digest["raster"] = rshape
    return digest


def descriptor_profile_digest(
    descriptor: Any,
    *,
    ref: str = "",
) -> Optional[Dict[str, Any]]:
    """RefDescriptor（dict 形）→ V3 浅投影 → 有界 digest（O(1)）。

    产物注册接缝（artifact_registry）用：descriptor 缺席 / 无有效
    feature_count → None（注册侧诚实缺省，profile_ref 不虚构）。
    """
    if not isinstance(descriptor, dict):
        return None
    fc = descriptor.get("feature_count")
    if not isinstance(fc, int) or isinstance(fc, bool) or fc < 0:
        return None
    try:
        profile = shallow_profile_from_descriptor(ref, descriptor)
        return profile_digest(profile)
    except Exception:  # noqa: BLE001 — digest 是增值记录，绝不阻断注册
        return None


class DatasetProfiler:
    """有界剖析服务（进程内单例；缓存绑定修订）。"""

    def __init__(self) -> None:
        self._cache: "OrderedDict[Tuple, Tuple[float, DatasetProfileV3]]" = OrderedDict()

    # ── 缓存 ────────────────────────────────────────────────────────
    def _cache_get(self, key: Tuple) -> Optional[DatasetProfileV3]:
        hit = self._cache.get(key)
        if hit is None:
            return None
        ts, profile = hit
        if time.monotonic() - ts > _PROFILE_CACHE_TTL_S:
            self._cache.pop(key, None)
            return None
        self._cache.move_to_end(key)
        return profile

    def _cache_put(self, key: Tuple, profile: DatasetProfileV3) -> None:
        self._cache[key] = (time.monotonic(), profile)
        self._cache.move_to_end(key)
        while len(self._cache) > _PROFILE_CACHE_MAX_ENTRIES:
            self._cache.popitem(last=False)

    def invalidate(self, session_id: Optional[str] = None, ref: Optional[str] = None) -> int:
        """显式失效（ref 覆写/删除后调用；不带参 = 全清）。返回清除条数。"""
        if session_id is None and ref is None:
            n = len(self._cache)
            self._cache.clear()
            return n
        doomed = [
            k for k in self._cache
            if (session_id is None or k[1] == session_id)
            and (ref is None or k[2] == ref)
        ]
        for k in doomed:
            self._cache.pop(k, None)
        return len(doomed)

    # ── 会话 ref 剖析 ───────────────────────────────────────────────
    async def profile_session_ref(
        self,
        session_id: str,
        ref: str,
        *,
        deep: bool = False,
        max_scan_rows: int = DEFAULT_MAX_SCAN_ROWS,
    ) -> Optional[DatasetProfileV3]:
        """会话 ref → DatasetProfileV3。ref 不存在 → None。

        非 deep：descriptor 投影（零扫描，PARTIAL）。
        deep：读载荷单趟有界扫描（COMPLETE / SAMPLED）。
        """
        if not session_id or not ref:
            return None
        from app.services.session_data import session_data_manager

        try:
            descriptor = await session_data_manager.get_ref_descriptor(session_id, ref)
        except Exception:  # noqa: BLE001 — 描述符缺席按 None（与 store 契约一致）
            descriptor = None
        revision = int(descriptor.get("content_revision") or 0) if isinstance(descriptor, dict) else 0
        key = ("ref", session_id, ref, revision, bool(deep), max_scan_rows)
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        if not isinstance(descriptor, dict):
            return None

        if deep:
            profile = await self._deep_profile_ref(
                session_id, ref, descriptor, max_scan_rows
            )
        else:
            profile = self._shallow_profile_ref(ref, descriptor)
        self._cache_put(key, profile)
        return profile

    def _shallow_profile_ref(self, ref: str, descriptor: Dict[str, Any]) -> DatasetProfileV3:
        return shallow_profile_from_descriptor(ref, descriptor)

    async def _deep_profile_ref(
        self,
        session_id: str,
        ref: str,
        descriptor: Dict[str, Any],
        max_scan_rows: int,
    ) -> DatasetProfileV3:
        from app.services.session_data import session_data_manager

        try:
            data = await session_data_manager.get(session_id, ref)
        except Exception as e:  # noqa: BLE001 — 载荷读不到 → FAILED 剖析
            return DatasetProfileV3(
                target_ref=ref,
                category="unknown",
                profile_quality=ProfileQuality.FAILED,
                diagnostics=[f"payload_unreadable: {e}"],
            )
        features = self._extract_features(data)
        if features is None:
            # 非 FC 载荷（chart spec / dict 等）：保留浅层证据，如实降级。
            shallow = self._shallow_profile_ref(ref, descriptor)
            shallow.diagnostics = list(shallow.diagnostics) + ["deep_profile_unsupported_payload"]
            return shallow
        try:
            vp, quality = await asyncio.to_thread(
                profile_features, features, crs=str(descriptor.get("crs") or ""),
                max_scan_rows=max_scan_rows,
            )
        except Exception as e:  # noqa: BLE001
            return DatasetProfileV3(
                target_ref=ref,
                category="unknown",
                profile_quality=ProfileQuality.FAILED,
                diagnostics=[f"profile_error: {e}"],
            )
        category = _category_from_geometry_types(vp.geometry_types)
        return DatasetProfileV3(
            target_ref=ref,
            category=category,
            crs=str(descriptor.get("crs") or ""),
            extent=vp.extent,
            vector=vp,
            profile_quality=quality,
            source_revision=int(descriptor.get("content_revision") or 0),
        )

    @staticmethod
    def _extract_features(data: Any) -> Optional[List[Any]]:
        """载荷 → features 列表；非 FC 形状 → None。"""
        fc = data
        if isinstance(data, dict):
            nested = data.get("geojson")
            if isinstance(nested, dict):
                fc = nested
        if isinstance(fc, dict) and fc.get("type") == "FeatureCollection":
            features = fc.get("features")
            return features if isinstance(features, list) else []
        return None

    # ── 栅格文件剖析 ────────────────────────────────────────────────
    async def profile_raster_file(
        self,
        path: str,
        *,
        ref_id: str = "",
        sample_size: int = 512,
    ) -> DatasetProfileV3:
        """栅格文件 → 剖析（头部 + ≤sample_size 边降采样读；重活在 to_thread）。"""
        try:
            rp, read_diagnostics = await asyncio.to_thread(
                self._read_raster_profile, path, sample_size
            )
        except ImportError as e:
            return DatasetProfileV3(
                target_ref=ref_id or path,
                category="raster",
                profile_quality=ProfileQuality.FAILED,
                diagnostics=[f"rasterio_unavailable: {e}"],
            )
        except Exception as e:  # noqa: BLE001 — 文件损坏/不可读 → FAILED
            return DatasetProfileV3(
                target_ref=ref_id or path,
                category="raster",
                profile_quality=ProfileQuality.FAILED,
                diagnostics=[f"raster_unreadable: {e}"],
            )
        # estimated_bytes 对压缩栅格（COG）必然失真 → 诚实留空，
        # 字节规模以 size 侧车道（storage 层）为准。
        return DatasetProfileV3(
            target_ref=ref_id or path,
            category="raster",
            crs=rp.crs,
            extent=rp.extent,
            raster=rp,
            profile_quality=ProfileQuality.COMPLETE,
            diagnostics=[str(d) for d in read_diagnostics][:8],
        )

    @staticmethod
    def _read_raster_profile(path: str, sample_size: int) -> Tuple[RasterProfileData, List[str]]:
        import rasterio

        with rasterio.open(path) as ds:
            crs = str(ds.crs) if ds.crs else ""
            bounds = ds.bounds
            extent = [bounds.left, bounds.bottom, bounds.right, bounds.top]
            overviews = sum(len(ds.overviews(i)) for i in range(1, ds.count + 1))
            # rasterio ≥1.x：``nodata`` 是标量属性（首波段）而非方法 —— 此前
            # 的 ``ds.nodata(i)`` 在真实调用路径上必然 TypeError（本函数此前
            # 无生产调用方/成功路径测试，V4 接线时发现的潜在 bug）。
            # ``nodatavals`` 是按波段的元组，与 ``count`` 对齐。
            nodatavals = tuple(getattr(ds, "nodatavals", ()) or ())
            nodata: List[Optional[float]] = [
                nodatavals[i] if i < len(nodatavals) else None
                for i in range(ds.count)
            ]
            band_stats: List[RasterBandStats] = []
            # 降采样读：≤sample_size 边（与 raster_spec.raster_content_fingerprint
            # 同一「绝不整幅读」纪律）；统计是近似口径，忠实声明。
            import math as _math

            scale = max(
                1,
                _math.ceil(max(ds.width or 1, ds.height or 1) / sample_size),
            )
            out_w = max(1, (ds.width or 1) // scale)
            out_h = max(1, (ds.height or 1) // scale)
            data = ds.read(
                out_shape=(ds.count, out_h, out_w),
                boundless=False,
            )
            # 波段读取封顶（波段数是数据可控维度，1000 波段 × 512² × 8B ≈ 2GB）。
            bands_read = min(ds.count, 16)
            diagnostics: list = []
            if bands_read < ds.count:
                diagnostics.append(f"band_stats_truncated_to_{bands_read}")
            for b in range(bands_read):
                band = data[b].ravel()
                total = band.size
                nd = nodata[b]
                if nd is not None and isinstance(nd, float) and _math.isnan(nd):
                    # NaN nodata：band != nan 恒真 —— 必须按 isnan 掩膜
                    import numpy as _np

                    valid = band[~_np.isnan(band)]
                elif nd is not None:
                    valid = band[band != nd]
                else:
                    valid = band
                # 非有限值不进 min/max/mean（NaN/Inf 会毒化全部统计）
                if valid.size and valid.dtype.kind == "f":
                    import numpy as _np

                    valid = valid[_np.isfinite(valid)]
                valid_count = int(valid.size)
                if valid.size:
                    bmin = float(valid.min())
                    bmax = float(valid.max())
                    bmean = float(valid.mean())
                    bstd = float(valid.std())
                else:
                    bmin = bmax = bmean = bstd = None
                band_stats.append(
                    RasterBandStats(
                        band=b + 1,
                        dtype=str(ds.dtypes[b]),
                        min=bmin,
                        max=bmax,
                        mean=bmean,
                        std=bstd,
                        valid_pixel_ratio=(valid_count / total) if total else None,
                    )
                )
            profile = RasterProfileData(
                width=ds.width,
                height=ds.height,
                band_count=ds.count,
                dtypes=[str(d) for d in ds.dtypes],
                crs=crs,
                extent=extent,
                resolution_x=abs(ds.transform.a) if ds.transform else None,
                resolution_y=abs(ds.transform.e) if ds.transform else None,
                nodata=nodata[:8],
                overviews=overviews,
                compression=str(getattr(ds, "compression", "") or ""),
                band_stats=band_stats,
            )
            # 时间元数据（§二十八）：TIFF tag 约定（TIFFTAG_DATETIME 是
            # "YYYY:MM:DD HH:MM:SS" 冒号格式 / 自定义 acquisition_time 项）。
            tags = ds.tags()
            acq = str(tags.get("acquisition_time") or tags.get("TIFFTAG_DATETIME") or "")
            if acq:
                from datetime import datetime as _dt
                import re as _re

                parsed = None
                m = _re.match(r"(\d{4}):(\d{2}):(\d{2})[ T](\d{2}:\d{2}(?::\d{2})?)", acq)
                iso_candidate = (
                    f"{m.group(1)}-{m.group(2)}-{m.group(3)}T{m.group(4)}" if m else acq
                )
                try:
                    parsed = _dt.fromisoformat(iso_candidate.replace("Z", "+00:00"))
                except ValueError:
                    parsed = None
                if parsed is not None:
                    profile.acquisition_time = parsed
                else:
                    diagnostics.append(f"acquisition_time_unparsed: {acq[:32]}")
            profile.temporal_resolution = str(tags.get("temporal_resolution") or "")
            return profile, diagnostics


_profiler: Optional[DatasetProfiler] = None


def get_dataset_profiler() -> DatasetProfiler:
    global _profiler
    if _profiler is None:
        _profiler = DatasetProfiler()
    return _profiler


def reset_dataset_profiler() -> None:
    global _profiler
    _profiler = None
