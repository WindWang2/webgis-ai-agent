"""空间分析 Celery 任务定义 - 逻辑与任务解耦版"""
import json
import logging
import math
import os
import time
import base64
import asyncio
from typing import List, Dict, Optional

import numpy as np

from app.core.config import settings
from app.services.task_queue import celery_app
from app.services.nature_resource_analyzer import NatureResourceAnalyzer
from app.services.rs.spectral_engine import SpectralRasterEngine
from app.models.upload import UploadRecord
from app.tools._utils import db_session
from app.services.jobs.worker import AlreadyFinished, durable_job, finish_job
from app.services.jobs.cancellation import OperationCancelled
from app.services.jobs.store import DurableJobStore
from celery.exceptions import SoftTimeLimitExceeded
# ADR-0052: 协作式取消检查点。cancellable() 在 chunk 边界读一次 contextvar，
# 未绑定 token 时开销为零；用户取消后长循环立即抛 OperationCancelled 退出，
# 真正释放 CPU 而不是只改 UI 状态。

logger = logging.getLogger(__name__)

# --- 共享热力图核心逻辑 (expand-contract: 消除 spatial.py 重复) ---
# 注：原 _do_buffer_analysis / _do_spatial_stats 包装器已删除——agent 工具直接调用
# SpatialAnalyzer.buffer / .statistics（ADR-0013 删除了会路由到它们的 dispatch seam）。
# 保留下列热力图辅助函数：spatial.py 的进程内回退路径直接 import 它们。


from app.lib.geo_analysis.heatmap_grid import (  # noqa: F401
    _extract_heatmap_points,
    _build_heatmap_grid,
    _build_grid_features,
)



def _do_heatmap_generation(features: List[Dict], cell_size: int = 500, radius: int = 1000, render_type: str = "raster", palette: str = "classic"):
    """Celery 路径的热力图入口：委托 density.generate_heatmap_raster。

    审计发现此前这里硬编码 ``plt.get_cmap('YlOrRd')``，palette 参数被忽略且
    不写 metadata（前端 legend 恒 0/1），与进程内回退路径（density.py 的
    ``_HEATMAP_PALETTES`` 契约）不一致。复用同一实现后 palette 渲染与 metadata
    输出天然一致；原先传入却从未被调用的 ``callback`` 参数已删除。
    """
    # lazy import：density 反向 lazy import 本模块的网格辅助函数，避免 import 环
    from app.lib.geo_analysis.density import generate_heatmap_raster

    result = generate_heatmap_raster(features, cell_size, radius, render_type, palette)
    if "error" in result and not result.get("success"):
        # density 的空点集分支只返回 {"error": ...}，补上 Celery 结果消费方
        # 依赖的 success=False 语义
        result["success"] = False
    return result

# --- Celery 任务包装器 ---
# 注：原 run_buffer_analysis / run_spatial_stats / run_nearest_neighbor /
# run_overlay_analysis / run_attribute_filter / run_path_analysis 已删除--agent
# 工具直接调 SpatialAnalyzer 的对应方法（ADR-0013 删除了路由到它们的 dispatch seam，
# 这些 task 包装器失去生产调用方）。保留的 3 个 task 由工具调用：
#   - run_heatmap_generation  (spatial.py:274)
#   - run_ndvi_analysis       (nature_resources.py 经 submit_durable_job)
#   - run_change_detection    (change_detection.py 经 submit_durable_job)

@celery_app.task(name="app.services.spatial_tasks.run_heatmap_generation", bind=True)
def run_heatmap_generation(self, features: List[Dict], cell_size: int = 500, radius: int = 1000, render_type: str = "raster", palette: str = "classic"):
    return _do_heatmap_generation(features, cell_size, radius, render_type, palette)

# --- 自然资源与遥感任务 ---

@celery_app.task(name="app.services.spatial_tasks.run_ndvi_analysis", bind=True)
def run_ndvi_analysis(
    self,
    raster_path: str,
    nir_band: Optional[int] = None,
    red_band: Optional[int] = None,
    session_id: Optional[str] = None,
    job_id: Optional[int] = None,
):
    """从本地 GeoTIFF 计算 NDVI 并持久化为资产。

    薄包装层，所有计算下沉到 NatureResourceAnalyzer.calculate_ndvi。
    NDVI 是 CPU 密集型操作（大栅格 reproject + 数组运算），
    严格走 Celery worker 隔离，遵循 V2.0 计算隔离不变式。

    ADR-0052：``job_id`` 存在时走 durable job 运行时 —— 进度落库（节流）、取消从
    DB 读取并在 checkpoint 处生效、终态由状态机守卫（cancelling 期间的 late
    success 收敛为 cancelled）、重复投递被入口守卫拒绝。``job_id=None`` 保留旧
    行为，兼容未迁移的调用方。
    """
    if job_id is None:
        return _run_ndvi_legacy(self, raster_path, nir_band, red_band, session_id)

    try:
        with durable_job(job_id, celery_task=self) as job:
            job.progress(10, "校验路径并读取影像元信息", phase="read")
            job.checkpoint()
            result = NatureResourceAnalyzer.calculate_ndvi(
                tif_path=raster_path,
                red_band=red_band,
                nir_band=nir_band,
            )
            if not result.get("success"):
                raise RuntimeError(result.get("error", "NDVI calculation failed"))

            # 取消可能在计算期间到达。注册资产是不可逆副作用，所以这里用
            # ensure_not_cancelled（强制读一次 DB）而不是 checkpoint（只读内存
            # token）—— 否则「取消已落库但看门狗还没轮询到」的瞬间会给一个已取消
            # 的 job 留下资产记录（规范 §23）。
            job.ensure_not_cancelled()
            job.progress(80, "入库登记分析资产", phase="persist")
            # job_id 传进去，让「取消检查」与「资产 INSERT」落在同一个事务里 ——
            # 否则取消可能挤在检查与 commit 之间，给已取消的 job 留下一条 ready 资产。
            asset_id = _persist_ndvi_asset(result, session_id, job_id=job_id)
            payload = {
                "asset_id": asset_id,
                "result_path": result["result_path"],
                "filename": result["filename"],
                "stats": result.get("stats", {}),
                "bbox": result.get("bbox"),
            }

        status = finish_job(job_id, result=payload, result_ref=result["result_path"])
        return {
            "success": status.value == "completed",
            "job_id": str(job_id),
            "status": status.value,
            "data": payload,
            "status_desc": "NDVI 分析完成，结果已入库。可通过 list_analysis_assets 查询。",
        }
    except AlreadyFinished as e:
        # 重复投递（acks_late 下 broker 可能重投）或提交前已被取消
        logger.info(f"run_ndvi_analysis skipped: {e}")
        return {"success": False, "skipped": True, "job_id": str(job_id), "error": str(e)}
    except OperationCancelled:
        logger.info(f"run_ndvi_analysis cancelled job_id={job_id}")
        return {"success": False, "cancelled": True, "job_id": str(job_id)}
    except SoftTimeLimitExceeded:
        # durable_job 已在 finally 里清掉临时产物并把 job 标成 failed。这里必须
        # 重新抛出：SoftTimeLimitExceeded 是 Exception 的子类，被下面的通用分支
        # 吞掉会让 Celery 以为任务正常返回，超时语义与统计全部失效。
        logger.warning(f"run_ndvi_analysis soft time limit exceeded job_id={job_id}")
        raise
    except Exception as e:
        logger.error(f"run_ndvi_analysis failed: {e}", exc_info=True)
        return {"success": False, "job_id": str(job_id), "error": str(e)}


def _persist_ndvi_asset(
    result: dict, session_id: Optional[str], job_id: Optional[int] = None
) -> Optional[int]:
    """把 NDVI 产物登记进 UploadRecord 资产表。失败不阻断任务（file-only 降级）。

    ``job_id`` 给定时，在**同一个事务内**先确认没有取消请求再插入 —— 资产登记是
    不可逆副作用，规范 §23 要求已取消的任务不得留下 status=ready 的资产。
    """
    try:
        with db_session() as db:
            if job_id is not None and DurableJobStore.is_cancel_requested_sync(db, job_id):
                raise OperationCancelled("cancelled before asset registration", job_id=job_id)
            record = UploadRecord(
                filename=result["result_path"],
                original_name=result["filename"],
                file_type="raster",
                format="geotiff",  # ck_upload_format 不接受 "tif"
                crs=result.get("crs", "EPSG:4326"),
                geometry_type="raster_analysis",
                feature_count=0,
                bbox=result.get("bbox"),
                file_size=os.path.getsize(result["result_path"]) if os.path.exists(result["result_path"]) else 0,
                session_id=session_id,
            )
            db.add(record)
            db.flush()
            return record.id
    except OperationCancelled:
        raise  # 取消不是「登记失败」—— 必须上抛让 durable_job 收敛为 cancelled
    except Exception as db_err:
        logger.warning(f"NDVI asset persist failed (continuing with file-only): {db_err}")
        return None


def _run_ndvi_legacy(
    task,
    raster_path: str,
    nir_band: Optional[int],
    red_band: Optional[int],
    session_id: Optional[str],
):
    """ADR-0052 之前的 NDVI 路径。无 durable job 时的兼容分支。"""
    try:
        # 直接调用（测试/shell）时 request.id 可能为 None（Celery 的 request 栈合并
        # 行为），update_state 会抛 "task_id must not be empty"。显式回退到稳定 id，
        # 真实 worker 路径不受影响。
        _task_id = task.request.id or f"legacy-{os.getpid()}"
        task.update_state(
            task_id=_task_id, state='PROGRESS',
            meta={'progress': 10, 'message': '校验路径并读取影像元信息'},
        )
        result = NatureResourceAnalyzer.calculate_ndvi(
            tif_path=raster_path,
            red_band=red_band,
            nir_band=nir_band,
        )

        if not result.get("success"):
            return {"success": False, "error": result.get("error", "NDVI calculation failed")}

        task.update_state(
            task_id=_task_id, state='PROGRESS',
            meta={'progress': 80, 'message': '入库登记分析资产'},
        )

        # 将分析结果落入 UploadRecord 资产表，便于 list_analysis_assets 查询
        try:
            with db_session() as db:
                record = UploadRecord(
                    filename=result["result_path"],
                    original_name=result["filename"],
                    file_type="raster",
                    format="geotiff",  # ck_upload_format 不接受 "tif"
                    crs=result.get("crs", "EPSG:4326"),
                    geometry_type="raster_analysis",
                    feature_count=0,
                    bbox=result.get("bbox"),
                    file_size=os.path.getsize(result["result_path"]) if os.path.exists(result["result_path"]) else 0,
                    session_id=session_id,
                )
                db.add(record)
                db.flush()
                asset_id = record.id
        except Exception as db_err:
            logger.warning(f"NDVI asset persist failed (continuing with file-only): {db_err}")
            asset_id = None

        return {
            "success": True,
            "data": {
                "asset_id": asset_id,
                "result_path": result["result_path"],
                "filename": result["filename"],
                "stats": result.get("stats", {}),
                "bbox": result.get("bbox"),
            },
            "status_desc": "NDVI 分析完成，结果已入库。可通过 list_analysis_assets 查询。",
        }
    except Exception as e:
        logger.error(f"run_ndvi_analysis failed: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


_CHANGE_CLASSES = (
    "significant_improvement",
    "slight_improvement",
    "no_change",
    "slight_degradation",
    "significant_degradation",
)


def _classify_delta(delta: float, thr: float) -> str:
    """5-way change category for a single delta (T2 − T1) at threshold thr."""
    if delta >= 2 * thr:
        return "significant_improvement"  # 显著改善
    if delta >= thr:
        return "slight_improvement"  # 轻微改善
    if delta > -thr:
        return "no_change"  # 无变化
    if delta > -2 * thr:
        return "slight_degradation"  # 轻微退化
    return "significant_degradation"  # 显著退化


def _array_window_for_bounds(arr: np.ndarray, arr_bounds, common_bounds):
    """Row/col slice of `arr` (gridded over `arr_bounds`) covering `common_bounds`.

    Returns (row0, col0, row1, col1) or None when the intersection holds no
    complete pixel. Assumes a north-up WGS84 grid, matching the #381 window
    bounds returned by the STAC band reader.
    """
    w, s, e, n = arr_bounds
    cw, cs, ce, cn = common_bounds
    res_x = (e - w) / arr.shape[1]
    res_y = (n - s) / arr.shape[0]
    if res_x <= 0 or res_y <= 0:
        return None
    col0 = max(0, int(math.floor((cw - w) / res_x)))
    col1 = min(arr.shape[1], int(math.ceil((ce - w) / res_x)))
    row0 = max(0, int(math.floor((n - cn) / res_y)))
    row1 = min(arr.shape[0], int(math.ceil((n - cs) / res_y)))
    if col1 <= col0 or row1 <= row0:
        return None
    return row0, col0, row1, col1


def _pixel_change_classification(t1: Dict, t2: Dict, change_threshold: float) -> Optional[Dict]:
    """Pixel-level change classification on the two epochs' COMMON footprint.

    #445: the two acquisitions come from independent STAC searches whose
    #381 read windows (raster_source.bounds) can cover different footprints —
    scene-level means are then not comparable. This computes the per-pixel
    difference on the intersection grid, classifies every valid pixel into
    the 5-way scheme, and renders a compact preview PNG. Returns None when a
    pixel comparison is impossible (no arrays / no common footprint).
    """
    rs1 = t1.get("raster_source") or {}
    rs2 = t2.get("raster_source") or {}
    a1, b1 = rs1.get("array"), rs1.get("bounds")
    a2, b2 = rs2.get("array"), rs2.get("bounds")
    if not (
        isinstance(a1, np.ndarray) and isinstance(a2, np.ndarray)
        and isinstance(b1, (list, tuple)) and len(b1) == 4
        and isinstance(b2, (list, tuple)) and len(b2) == 4
    ):
        return None

    common = [max(b1[0], b2[0]), max(b1[1], b2[1]), min(b1[2], b2[2]), min(b1[3], b2[3])]
    if common[2] <= common[0] or common[3] <= common[1]:
        return None  # disjoint footprints

    w1 = _array_window_for_bounds(a1, b1, common)
    w2 = _array_window_for_bounds(a2, b2, common)
    if w1 is None or w2 is None:
        return None

    sub1 = a1[w1[0]:w1[2], w1[1]:w1[3]].astype(float)
    sub2 = a2[w2[0]:w2[2], w2[1]:w2[3]].astype(float)

    if sub1.shape != sub2.shape:
        # Different resolutions → resample T2 onto T1's common-window grid.
        try:
            from rasterio.transform import from_origin
            from rasterio.warp import Resampling, reproject

            def _window_transform(arr, bounds, win):
                bw, bs_, be, bn = bounds
                res_x = (be - bw) / arr.shape[1]
                res_y = (bn - bs_) / arr.shape[0]
                return from_origin(
                    bw + res_x * win[1], bn - res_y * win[0], res_x, res_y
                )

            dst = np.full(sub1.shape, np.nan)
            reproject(
                source=sub2,
                destination=dst,
                src_transform=_window_transform(a2, b2, w2),
                src_crs="EPSG:4326",
                dst_transform=_window_transform(a1, b1, w1),
                dst_crs="EPSG:4326",
                src_nodata=float("nan"),
                dst_nodata=float("nan"),
                resampling=Resampling.bilinear,
            )
            sub2 = dst
        except Exception as e:
            logger.warning(f"change-detection grid alignment failed: {e}")
            return None

    diff = sub2 - sub1
    valid = np.isfinite(diff)
    valid_pixels = int(valid.sum())
    vals = diff[valid]

    class_counts = {c: 0 for c in _CHANGE_CLASSES}
    if vals.size:
        cls_arr = np.select(
            [vals >= 2 * change_threshold, vals >= change_threshold,
             vals > -change_threshold, vals > -2 * change_threshold],
            ["significant_improvement", "slight_improvement",
             "no_change", "slight_degradation"],
            default="significant_degradation",
        )
        names, counts = np.unique(cls_arr, return_counts=True)
        class_counts.update(dict(zip(names.tolist(), (int(c) for c in counts))))

    class_percentages = {
        c: (round(v / valid_pixels * 100, 1) if valid_pixels else 0.0)
        for c, v in class_counts.items()
    }
    delta_mean = float(np.mean(vals)) if vals.size else None
    category = _classify_delta(delta_mean, change_threshold) if delta_mean is not None else "unknown"

    # Compact preview PNG of the difference field (NaN → transparent, #480).
    preview_png_b64 = None
    try:
        from app.services.raster_cartography_converter import render_array_to_png

        step = max(1, math.ceil(max(diff.shape) / 256))
        png = render_array_to_png(diff[::step, ::step], palette="Viridis")
        preview_png_b64 = base64.b64encode(png).decode("ascii")
    except Exception as e:
        logger.warning(f"change-detection preview render failed: {e}")

    return {
        "method": "pixel_common_footprint",
        "common_bounds": [round(c, 6) for c in common],
        "valid_pixels": valid_pixels,
        "total_pixels": int(diff.size),
        "class_counts": class_counts,
        "class_percentages": class_percentages,
        "delta_mean": delta_mean,
        "category": category,
        "preview_png_b64": preview_png_b64,
    }


class _ChangeDetectionError(RuntimeError):
    """业务性失败（T1/T2 计算失败等）-- 保留 dict 返回语义，不算 Celery FAILURE。"""


def _compute_change_indices(
    bbox: List[float], t1_from: str, t1_to: str, t2_from: str, t2_to: str, index_type: str
):
    """顺序执行两次 async 指数计算（新建事件循环，兼容 prefork / gevent 池）。"""
    svc = SpectralRasterEngine()

    async def compute_both():
        t1 = await svc.compute_vegetation_index(bbox, t1_from, t1_to, index_type=index_type)
        t2 = await svc.compute_vegetation_index(bbox, t2_from, t2_to, index_type=index_type)
        return t1, t2

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(compute_both())
    finally:
        loop.close()
        asyncio.set_event_loop(None)


def _build_change_data(
    bbox: List[float],
    t1_from: str, t1_to: str, t2_from: str, t2_to: str,
    t1: Dict, t2: Dict, index_type: str, change_threshold: float,
) -> Dict:
    """汇总两期指数统计并产出变化判定（像元级分类优先，#445）。

    两期影像来自独立 STAC 检索，#381 读取窗口可能覆盖不同足迹，场景均值
    不可直接比较 -- 优先在两期足迹的公共栅格上做像元级差值分类
    （``_pixel_change_classification``：class_counts/class_percentages/
    preview_png_b64），仅当无法像元比较（无数组/足迹不相交）时显式回退到
    场景均值（method=scene_mean_fallback）。
    """
    delta_mean = None
    category = "unknown"
    method = "scene_mean_fallback"
    classification: Optional[Dict] = None

    pixel = _pixel_change_classification(t1, t2, change_threshold)
    if pixel is not None:
        method = pixel["method"]
        delta_mean = pixel["delta_mean"]
        category = pixel["category"]
        classification = pixel
    else:
        mean_t1 = t1.get("stats", {}).get("mean")
        mean_t2 = t2.get("stats", {}).get("mean")
        if mean_t1 is not None and mean_t2 is not None:
            delta_mean = round(mean_t2 - mean_t1, 4)
            category = _classify_delta(delta_mean, change_threshold)

    return {
        "index_type": index_type.upper(),
        "bbox": bbox,
        "t1": {
            "period": f"{t1_from} ~ {t1_to}",
            "stats": t1.get("stats", {}),
            "cloud_cover": t1.get("cloud_cover"),
        },
        "t2": {
            "period": f"{t2_from} ~ {t2_to}",
            "stats": t2.get("stats", {}),
            "cloud_cover": t2.get("cloud_cover"),
        },
        "change": {
            "delta_mean": delta_mean,
            "category": category,
            "threshold": change_threshold,
            "method": method,
            "classification": classification,
        },
    }


def _change_result_geojson(data: Dict, bbox: List[float]) -> Dict:
    """把统计结果包装成合法 GeoJSON（bbox 多边形携带统计属性）。

    UploadRecord.format 受 ck_upload_format 约束（geojson/shapefile/geotiff/
    csv/gpkg/kml），统计 JSON 包装成 FeatureCollection 后按 'geojson' 落库，
    资产可被地图端直接加载。

    #445 的 ``classification.preview_png_b64`` 是 base64 PNG 大对象：留在任务
    结果里（任务中心/LLM 可读）即可，不进 GeoJSON 资产属性 -- 资产整份下发给
    地图端，KB 级 blob 只会拖累加载；method/class_counts/class_percentages
    等轻量统计照常保留。
    """
    w, s, e, n = bbox
    change_props = dict(data["change"])
    classification = change_props.get("classification")
    if isinstance(classification, dict):
        change_props["classification"] = {
            k: v for k, v in classification.items() if k != "preview_png_b64"
        }
    props = {
        "analysis": "change_detection",
        "index_type": data["index_type"],
        "t1_period": data["t1"]["period"],
        "t1_stats": data["t1"]["stats"],
        "t1_cloud_cover": data["t1"].get("cloud_cover"),
        "t2_period": data["t2"]["period"],
        "t2_stats": data["t2"]["stats"],
        "t2_cloud_cover": data["t2"].get("cloud_cover"),
        **change_props,
    }
    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]],
            },
            "properties": props,
        }],
    }


def _register_change_asset(
    result_path: str, bbox: List[float], session_id: Optional[str], job_id: Optional[int] = None
) -> Optional[int]:
    """把变化检测 GeoJSON 统计产物登记进 UploadRecord 资产表。

    失败不阻断任务（file-only 降级）。``job_id`` 给定时，在**同一个事务内**先确认
    没有取消请求再插入 -- 资产登记是不可逆副作用，规范 §23 要求已取消的任务
    不得留下 status=ready 的资产。
    """
    try:
        with db_session() as db:
            if job_id is not None and DurableJobStore.is_cancel_requested_sync(db, job_id):
                raise OperationCancelled("cancelled before asset registration", job_id=job_id)
            record = UploadRecord(
                filename=result_path,
                original_name=os.path.basename(result_path),
                file_type="vector",
                format="geojson",
                crs="EPSG:4326",
                geometry_type="change_detection",
                feature_count=1,
                bbox=bbox,
                file_size=os.path.getsize(result_path) if os.path.exists(result_path) else 0,
                session_id=session_id,
            )
            db.add(record)
            db.flush()
            return record.id
    except OperationCancelled:
        raise  # 取消不是「登记失败」-- 必须上抛让 durable_job 收敛为 cancelled
    except Exception as db_err:
        logger.warning(f"change detection asset persist failed (continuing with file-only): {db_err}")
        return None


@celery_app.task(name="app.services.spatial_tasks.run_change_detection", bind=True)
def run_change_detection(
    self,
    bbox: List[float],
    t1_from: str,
    t1_to: str,
    t2_from: str,
    t2_to: str,
    index_type: str = "ndvi",
    change_threshold: float = 0.1,
    session_id: Optional[str] = None,
    job_id: Optional[int] = None,
):
    """双时相植被/水体/燃烧指数变化检测。

    对同一 bbox 在两个时间窗口分别获取 Sentinel-2 影像并计算指定指数，
    在两期足迹的公共栅格上做像元级差异，按 ±change_threshold 分类为 5 档，
    输出分类统计与差异预览图（#445）。

    实现策略：复用 SpectralRasterEngine.compute_vegetation_index（异步）
    通过新建事件循环在 Celery worker 进程内顺序执行两次，避免重复实现
    STAC + 波段读取逻辑。

    ADR-0052：``job_id`` 存在时走 durable job 运行时 -- 进度落库、可
    取消、结果落 ``analysis_results`` 并登记为 GeoJSON 统计资产（此前 .delay 火
    忘投递：任务不注册资产、/tasks/status/glm-5.3_common 404）。``job_id=None`` 保留旧行为，
    兼容未迁移的调用方。
    """
    if job_id is None:
        return _run_change_detection_legacy(
            self, bbox, t1_from, t1_to, t2_from, t2_to, index_type, change_threshold, session_id
        )

    try:
        with durable_job(job_id, celery_task=self) as job:
            job.progress(5, "初始化遥感服务", phase="init")
            job.checkpoint()
            job.progress(20, f"拉取 T1 ({t1_from}~{t1_to}) Sentinel-2", phase="fetch")
            t1, t2 = _compute_change_indices(bbox, t1_from, t1_to, t2_from, t2_to, index_type)

            if "error" in t1:
                raise _ChangeDetectionError(f"T1 计算失败: {t1['error']}")
            if "error" in t2:
                raise _ChangeDetectionError(f"T2 计算失败: {t2['error']}")

            job.progress(80, "计算变化分类", phase="classify")
            data = _build_change_data(
                bbox, t1_from, t1_to, t2_from, t2_to, t1, t2, index_type, change_threshold
            )

            # 统计结果是 JSON 而非栅格：推图不适用，改为落 GeoJSON 统计资产
            # （bbox 多边形 + 统计属性），经 UploadRecord 挂到会话
            job.progress(85, "落盘统计结果并登记资产", phase="persist")
            out_dir = os.path.join(settings.DATA_DIR, "analysis_results")
            os.makedirs(out_dir, exist_ok=True)
            final_path = os.path.join(out_dir, f"change_detection_{job_id}_{int(time.time())}.geojson")
            with job.artifact(final_path) as tmp_path:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(_change_result_geojson(data, bbox), f, ensure_ascii=False)

            # 资产登记是不可逆副作用：强制读一次持久取消事实（规范 §23）
            job.ensure_not_cancelled()
            asset_id = _register_change_asset(final_path, bbox, session_id, job_id=job_id)
            payload = {
                "asset_id": asset_id,
                "result_path": final_path,
                "data": data,
            }
            result_data = {**data, "asset_id": asset_id, "result_path": final_path}

        status = finish_job(job_id, result=payload, result_ref=final_path)
        return {
            "success": status.value == "completed",
            "job_id": str(job_id),
            "status": status.value,
            "data": result_data,
            "status_desc": (
                f"{index_type.upper()} 变化检测完成：{data['change']['category']}"
                f"（Δmean={data['change']['delta_mean']}），统计结果已保存为 GeoJSON 资产。"
            ),
        }
    except AlreadyFinished as e:
        # 重复投递（acks_late 下 broker 可能重投）或提交前已被取消
        logger.info(f"run_change_detection skipped: {e}")
        return {"success": False, "skipped": True, "job_id": str(job_id), "error": str(e)}
    except OperationCancelled:
        logger.info(f"run_change_detection cancelled job_id={job_id}")
        return {"success": False, "cancelled": True, "job_id": str(job_id)}
    except _ChangeDetectionError as e:
        # 业务性失败（T1/T2 计算失败）：durable_job 已把 job 落成 failed，这里
        # 保留 dict 返回语义（仅意外异常才让 Celery 记 FAILURE）
        logger.warning(f"run_change_detection business failure job_id={job_id}: {e}")
        return {"success": False, "job_id": str(job_id), "error": str(e)}
    except Exception as e:
        # 意外异常：上抛让 Celery 记录 FAILURE（此前吞掉返回 dict 会被
        # 视为 SUCCESS，超时/崩溃语义与统计全部失效）；durable_job 已落 failed 终态
        logger.error(f"run_change_detection failed: {e}", exc_info=True)
        raise


def _run_change_detection_legacy(
    task,
    bbox: List[float],
    t1_from: str,
    t1_to: str,
    t2_from: str,
    t2_to: str,
    index_type: str,
    change_threshold: float,
    session_id: Optional[str],
):
    """ADR-0052 之前的变化检测路径。无 durable job 时的兼容分支。"""
    try:
        # 直接调用（测试/shell）时 request.id 可能为 None，update_state 会抛
        # "task_id must not be empty"（同 _run_ndvi_legacy 的回退处理）。
        _task_id = task.request.id or f"legacy-change-{os.getpid()}"
        task.update_state(
            task_id=_task_id, state='PROGRESS',
            meta={'progress': 5, 'message': '初始化遥感服务'},
        )
        task.update_state(
            task_id=_task_id, state='PROGRESS',
            meta={'progress': 20, 'message': f'拉取 T1 ({t1_from}~{t1_to}) Sentinel-2'},
        )
        t1, t2 = _compute_change_indices(bbox, t1_from, t1_to, t2_from, t2_to, index_type)

        if "error" in t1:
            return {"success": False, "error": f"T1 计算失败: {t1['error']}"}
        if "error" in t2:
            return {"success": False, "error": f"T2 计算失败: {t2['error']}"}

        task.update_state(
            task_id=_task_id, state='PROGRESS',
            meta={'progress': 80, 'message': '计算变化分类'},
        )
        data = _build_change_data(
            bbox, t1_from, t1_to, t2_from, t2_to, t1, t2, index_type, change_threshold
        )
        return {
            "success": True,
            "data": data,
            "status_desc": f"{index_type.upper()} 变化检测完成：{data['change']['category']}（Δmean={data['change']['delta_mean']}）",
        }
    except Exception as e:
        # 意外异常上抛让 Celery 记录 FAILURE（此前吞掉返回 dict 会被视为 SUCCESS）
        logger.error(f"run_change_detection failed: {e}", exc_info=True)
        raise
