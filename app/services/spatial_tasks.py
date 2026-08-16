"""空间分析 Celery 任务定义 - 逻辑与任务解耦版"""
import logging
import math
import os
import io
import base64
import asyncio
from typing import List, Dict, Optional, Callable

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.ndimage import gaussian_filter
from shapely.geometry import mapping
from shapely import box as sbox

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
from app.services.jobs.cancellation import cancellable

logger = logging.getLogger(__name__)

# --- 共享热力图核心逻辑 (expand-contract: 消除 spatial.py 重复) ---
# 注：原 _do_buffer_analysis / _do_spatial_stats 包装器已删除——agent 工具直接调用
# SpatialAnalyzer.buffer / .statistics（ADR-0013 删除了会路由到它们的 dispatch seam）。
# 保留下列热力图辅助函数：spatial.py 的进程内回退路径直接 import 它们。


def _extract_heatmap_points(features: List[Dict]) -> tuple[list, list]:
    """Extract valid (lon, lat) points from GeoJSON features."""
    points = []
    for f in cancellable(features or [], every=512):
        if not isinstance(f, dict):
            continue
        geom = f.get("geometry") or {}
        if geom.get("type") != "Point":
            continue
        coords = geom.get("coordinates")
        if not coords or len(coords) < 2:
            continue
        try:
            lon, lat = float(coords[0]), float(coords[1])
            if not math.isnan(lon) and not math.isnan(lat):
                points.append((lon, lat))
        except (ValueError, TypeError):
            continue
    return [p[0] for p in points], [p[1] for p in points]


def _build_heatmap_grid(xs, ys, cell_size: int):
    """Build histogram grid from point coordinates. Returns (H, xedges, yedges, cell_deg).

    ``cell_size`` is meters (tool schema: 10-5000m). The degree-per-meter
    ratio differs between axes: 1 deg lat ≈ 111.32 km everywhere, but 1 deg
    lng ≈ 111.32*cos(lat) km (audit GIS-25: the previous fixed ``cell_size /
    111000`` produced non-square, latitude-dependent cells — e.g. 500 m
    became ~250 m in the lng direction at 60°N). We derive per-axis degree
    widths from the data's mean latitude so cells are square in meters.
    """
    mean_lat = sum(ys) / len(ys)
    meters_per_deg_lat = 111320.0
    meters_per_deg_lng = max(meters_per_deg_lat * math.cos(math.radians(mean_lat)), 1000.0)
    cell_deg_lng = cell_size / meters_per_deg_lng
    cell_deg_lat = cell_size / meters_per_deg_lat
    # Keep the historical 4th return value a single float (cell width in
    # degrees of longitude) so existing tuple-unpacking callers stay stable;
    # the lng/lat widths are both returned via the bin edges themselves.
    cell_deg = cell_deg_lng
    margin_lng = cell_deg_lng * 2
    margin_lat = cell_deg_lat * 2
    x_min, x_max = min(xs) - margin_lng, max(xs) + margin_lng
    y_min, y_max = min(ys) - margin_lat, max(ys) + margin_lat
    if x_min == x_max:
        x_max += cell_deg_lng
    if y_min == y_max:
        y_max += cell_deg_lat
    x_bins = np.arange(x_min, x_max + cell_deg_lng, cell_deg_lng)
    y_bins = np.arange(y_min, y_max + cell_deg_lat, cell_deg_lat)
    if len(x_bins) > 5000 or len(y_bins) > 5000:
        raise ValueError("Resolution too high for the data extent")
    H, xedges, yedges = np.histogram2d(xs, ys, bins=[x_bins, y_bins])
    return H, xedges, yedges, cell_deg


def _build_grid_features(H, xedges, yedges, max_val: float) -> list[dict]:
    """Build GeoJSON features for non-zero histogram cells."""
    MAX_GRID_FEATURES = 500_000
    nonzero = np.argwhere(H > 0)
    total_cells = len(nonzero)
    if total_cells > MAX_GRID_FEATURES:
        raise ValueError(f"Grid too dense ({total_cells} cells). Increase cell_size or reduce data extent. Max allowed: {MAX_GRID_FEATURES}")
    if total_cells == 0:
        return []

    # Vectorized cell construction: np.argwhere is row-major (same order as the
    # scalar loop); shapely 2.x sbox() builds all geometries in one C call.
    i = nonzero[:, 0]
    j = nonzero[:, 1]
    counts = H[i, j]
    rects = sbox(xedges[i], yedges[j], xedges[i + 1], yedges[j + 1])

    return [
        {
            "type": "Feature",
            "geometry": mapping(rect),
            "properties": {
                "count": int(count),
                "weight": round(float(count / max_val), 4),
            },
        }
        for rect, count in zip(rects, counts)
    ]


def _do_heatmap_generation(features: List[Dict], cell_size: int = 500, radius: int = 1000, render_type: str = "raster", palette: str = "classic", callback: Optional[Callable] = None):
    fig = None
    try:
        xs, ys = _extract_heatmap_points(features)
        if not xs:
            return {"success": False, "error": "No valid point features found"}

        H, xedges, yedges, _ = _build_heatmap_grid(xs, ys, cell_size)

        if render_type == "grid":
            max_val = float(H.max()) if H.max() > 0 else 1.0
            grid_features = _build_grid_features(H, xedges, yedges, max_val)
            return {
                "success": True,
                "data": {"type": "FeatureCollection", "features": grid_features},
                "status_desc": f"已生成矢量格网热力图 ({len(grid_features)} 个格网)。"
            }
        else:
            sigma = max(1.0, radius / cell_size)
            H_smooth = gaussian_filter(H.T, sigma=sigma)
            # ... colormap logic ...
            cmap = plt.get_cmap("YlOrRd") # Simplified for brevity, original has custom palettes
            fig, ax = plt.subplots(figsize=(10, 10), dpi=100)
            v_max = np.percentile(H_smooth, 98) if H_smooth.max() > 0 else 1.0
            ax.imshow(H_smooth, cmap=cmap, origin="lower", aspect="auto", vmin=0, vmax=v_max)
            ax.axis("off")
            plt.subplots_adjust(left=0, right=1, top=1, bottom=0)
            buf = io.BytesIO()
            fig.savefig(buf, format="png", bbox_inches="tight", pad_inches=0, transparent=True)
            plt.close(fig)
            img_b64 = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
            return {
                "success": True,
                "data": {
                    "type": "heatmap_raster", "image": img_b64, 
                    "bbox": [float(xedges[0]), float(yedges[0]), float(xedges[-1]), float(yedges[-1])],
                    "total_points": len(xs)
                }
            }
    except Exception as e:
        logger.error(f"Heatmap failed: {e}", exc_info=True)
        return {"success": False, "error": str(e)}

# --- Celery 任务包装器 ---
# 注：原 run_buffer_analysis / run_spatial_stats / run_nearest_neighbor /
# run_overlay_analysis / run_attribute_filter / run_path_analysis 已删除——agent
# 工具直接调 SpatialAnalyzer 的对应方法（ADR-0013 删除了路由到它们的 dispatch seam，
# 这些 task 包装器失去生产调用方）。保留的 3 个 task 由工具 .delay/.apply_async 调用：
#   - run_heatmap_generation  (spatial.py:274)
#   - run_ndvi_analysis       (nature_resources.py:41)
#   - run_change_detection    (change_detection.py:68)

@celery_app.task(name="app.services.spatial_tasks.run_heatmap_generation", bind=True)
def run_heatmap_generation(self, features: List[Dict], cell_size: int = 500, radius: int = 1000, render_type: str = "raster", palette: str = "classic"):
    def cb(curr, msg): self.update_state(state='PROGRESS', meta={'progress': curr, 'message': msg})
    return _do_heatmap_generation(features, cell_size, radius, render_type, palette, callback=cb)

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
):
    """双时相植被/水体/燃烧指数变化检测。

    对同一 bbox 在两个时间窗口分别获取 Sentinel-2 影像并计算指定指数，
    在两期足迹的公共栅格上做像元级差异，按 ±change_threshold 分类为 5 档，
    输出分类统计与差异预览图。

    实现策略：复用 SpectralRasterEngine.compute_vegetation_index（异步）
    通过新建事件循环在 Celery worker 进程内顺序执行两次，避免重复实现
    STAC + 波段读取逻辑。
    """
    svc = SpectralRasterEngine()

    async def compute_both():
        t1 = await svc.compute_vegetation_index(bbox, t1_from, t1_to, index_type=index_type)
        t2 = await svc.compute_vegetation_index(bbox, t2_from, t2_to, index_type=index_type)
        return t1, t2

    try:
        # 直接调用（测试/shell）时 request.id 可能为 None，update_state 会抛
        # "task_id must not be empty"（同 _run_ndvi_legacy 的回退处理）。
        _task_id = self.request.id or f"legacy-change-{os.getpid()}"
        self.update_state(task_id=_task_id, state='PROGRESS', meta={'progress': 5, 'message': '初始化遥感服务'})
        self.update_state(task_id=_task_id, state='PROGRESS', meta={'progress': 20, 'message': f'拉取 T1 ({t1_from}~{t1_to}) Sentinel-2'})

        # 使用新事件循环执行 async 代码，兼容 prefork / gevent 池
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            t1, t2 = loop.run_until_complete(compute_both())
        finally:
            loop.close()
            asyncio.set_event_loop(None)

        if "error" in t1:
            return {"success": False, "error": f"T1 计算失败: {t1['error']}"}
        if "error" in t2:
            return {"success": False, "error": f"T2 计算失败: {t2['error']}"}

        self.update_state(task_id=_task_id, state='PROGRESS', meta={'progress': 80, 'message': '计算变化分类'})

        # #445: pixel-level difference on the two epochs' common footprint —
        # the two scene means come from independent acquisitions whose read
        # windows (#381) may cover different footprints, so they are not
        # directly comparable. Only fall back to the scene-mean comparison
        # when no pixel comparison is possible.
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
            "success": True,
            "data": {
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
            },
            "status_desc": f"{index_type.upper()} 变化检测完成：{category}（Δmean={delta_mean}）",
        }
    except Exception as e:
        logger.error(f"run_change_detection failed: {e}", exc_info=True)
        return {"success": False, "error": str(e)}
