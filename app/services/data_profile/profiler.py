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
            rp = await asyncio.to_thread(self._read_raster_profile, path, sample_size)
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
        )

    @staticmethod
    def _read_raster_profile(path: str, sample_size: int) -> RasterProfileData:
        import rasterio

        with rasterio.open(path) as ds:
            crs = str(ds.crs) if ds.crs else ""
            bounds = ds.bounds
            extent = [bounds.left, bounds.bottom, bounds.right, bounds.top]
            overviews = sum(len(ds.overviews(i)) for i in range(1, ds.count + 1))
            nodata: List[Optional[float]] = [ds.nodata(i) for i in range(1, ds.count + 1)]
            band_stats: List[RasterBandStats] = []
            # 降采样读：≤sample_size 边（与 raster_spec.raster_content_fingerprint
            # 同一「绝不整幅读」纪律）；统计是近似口径，忠实声明。
            scale = max(
                1,
                max((ds.width or 1) // sample_size, (ds.height or 1) // sample_size, 1),
            )
            out_w = max(1, (ds.width or 1) // scale)
            out_h = max(1, (ds.height or 1) // scale)
            data = ds.read(
                out_shape=(ds.count, out_h, out_w),
                boundless=False,
            )
            for b in range(ds.count):
                band = data[b]
                valid = band[band != nodata[b]] if nodata[b] is not None else band.ravel()
                total = band.size
                if nodata[b] is not None:
                    valid_count = int((band != nodata[b]).sum())
                else:
                    valid_count = total
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
            # 时间元数据（§二十八）：TIFF tag 约定（TIFFTAG_DATETIME / 自定义 acq 项）。
            tags = ds.tags()
            acq = tags.get("acquisition_time") or tags.get("TIFFTAG_DATETIME") or ""
            if acq:
                try:
                    from datetime import datetime as _dt

                    profile.acquisition_time = _dt.fromisoformat(str(acq).replace("Z", "+00:00").replace(" ", "T", 1) if " " in str(acq) and "T" not in str(acq) else str(acq))
                except ValueError:
                    pass
            profile.temporal_resolution = str(tags.get("temporal_resolution") or "")
            return profile


_profiler: Optional[DatasetProfiler] = None


def get_dataset_profiler() -> DatasetProfiler:
    global _profiler
    if _profiler is None:
        _profiler = DatasetProfiler()
    return _profiler


def reset_dataset_profiler() -> None:
    global _profiler
    _profiler = None
