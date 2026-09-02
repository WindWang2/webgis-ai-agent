"""
工具层共享工具函数
提供 bbox 解析、数据库会话上下文、STAC Asset 提取等基础能力
"""
import logging
from contextlib import contextmanager, asynccontextmanager
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

from app.lib.tool_cache import cached_tool  # noqa: F401


def sanitize_json_obj(obj: Any) -> Any:
    """Recursively replace NaN and Inf values in dict/list/floats with None to prevent JSON serialization crashes."""
    import math
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    elif isinstance(obj, dict):
        return {k: sanitize_json_obj(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [sanitize_json_obj(v) for v in obj]
    return obj


# ============================================================================
# BBox 解析
# ============================================================================

def parse_bbox(bbox_str: str) -> List[float]:
    """
    解析边界框字符串为 [west, south, east, north] 浮点列表。

    支持格式:
        "[116.2, 39.7, 116.6, 40.1]"
        "(116.2, 39.7, 116.6, 40.1)"
        "116.2, 39.7, 116.6, 40.1"

    Raises:
        ValueError: 格式错误或数值非法
    """
    try:
        cleaned = bbox_str.strip().strip("[]()")
        parts = [float(x.strip()) for x in cleaned.split(",")]
    except (ValueError, AttributeError) as e:
        raise ValueError(f"bbox 格式错误: '{bbox_str}' 无法解析为数值列表") from e

    if len(parts) != 4:
        raise ValueError(f"bbox 需要 4 个值 [west, south, east, north]，得到 {len(parts)} 个")

    west, south, east, north = parts
    if west >= east:
        raise ValueError(f"bbox 经度范围无效: west ({west}) >= east ({east})")
    if south >= north:
        raise ValueError(f"bbox 纬度范围无效: south ({south}) >= north ({north})")
    if not (-180 <= west <= 180 and -180 <= east <= 180):
        raise ValueError(f"bbox 经度超出有效范围 [-180, 180]: {west}, {east}")
    if not (-90 <= south <= 90 and -90 <= north <= 90):
        raise ValueError(f"bbox 纬度超出有效范围 [-90, 90]: {south}, {north}")

    return parts


# ============================================================================
# 数据库会话上下文管理器
# ============================================================================

@contextmanager
def db_session():
    """
    数据库会话上下文管理器，自动处理 commit/rollback/close。

    Usage:
        with db_session() as db:
            record = db.query(...).first()
            ...
    """
    from app.core.database import SessionLocal
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@asynccontextmanager
async def async_db_session():
    """
    Async database session context manager, auto commit/rollback/close.

    Usage:
        async with async_db_session() as db:
            record = await db.get(Model, id)
            ...
    """
    from app.core.database import AsyncSessionLocal
    if AsyncSessionLocal is None:
        raise RuntimeError("Async DB support not available (missing asyncpg or aiosqlite)")
    db = AsyncSessionLocal()
    try:
        yield db
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()


# ============================================================================
# STAC Asset Href 提取
# ============================================================================

from app.utils.path import validate_data_path


def std_error_response(message: str, code: str = "TOOL_ERROR", error_type: str = "", correction_hint: str = "") -> dict:
    """
    标准化错误响应格式，与全局异常处理器对齐。

    Args:
        message: 用户可读的错误信息
        code: 错误代码（如 VALIDATION_ERROR, NETWORK_ERROR, TOOL_ERROR）
        error_type: 异常类型名称（可选）
        correction_hint: 给 LLM 的修复建议（可选）

    Returns:
        标准化的错误响应字典
    """
    resp = {
        "success": False,
        "code": code,
        "message": message,
        "data": None,
    }
    if error_type:
        resp["error_type"] = error_type
    if correction_hint:
        resp["correction_hint"] = correction_hint
    return resp


def asset_href(assets: dict, key: str) -> str:
    """
    兼容 pystac Asset 对象和旧版 dict 两种格式取 href。
    同时兼容 Element84 STAC 的波段 key 命名（如 'red' 或 'B04'）。
    """
    # 直接匹配
    asset = assets.get(key)
    if asset is not None:
        if hasattr(asset, "href"):
            return asset.href or ""
        if isinstance(asset, dict):
            return asset.get("href", "")
        return ""

    # 别名映射 (Element84 常用)
    aliases = {
        "red": ["B04", "red"],
        "green": ["B03", "green"],
        "blue": ["B02", "blue"],
        "nir": ["B08", "nir"],
        "swir11": ["B11", "swir16"],
        "swir12": ["B12", "swir22"],
    }
    for alias in aliases.get(key, []):
        asset = assets.get(alias)
        if asset is not None:
            if hasattr(asset, "href"):
                return asset.href or ""
            if isinstance(asset, dict):
                return asset.get("href", "")

    return ""


# ============================================================================
# Payload trim — 重 GeoJSON 返回的统一裁剪
# ============================================================================

def trim_features(fc: dict, max_features: int = 5000, precision: int = 6) -> dict:
    """裁剪 FeatureCollection 的载荷：保留前 N 条 + 几何坐标四舍五入。

    Args:
        fc: 输入字典。非 FeatureCollection 时原样返回 + warning。
        max_features: 超过则截断保留前 N。默认 5000。
        precision: 坐标小数位。默认 6（赤道 ≈ 10cm，肉眼无差）。

    Returns:
        裁剪后的 FeatureCollection。仅在实际发生裁剪时多一个顶层 "_trim" 键。
    """
    if not isinstance(fc, dict) or fc.get("type") != "FeatureCollection":
        logger.warning(
            f"[trim_features] non-FeatureCollection input (type={fc.get('type') if isinstance(fc, dict) else type(fc).__name__}); returning unchanged"
        )
        return fc

    features = fc.get("features", []) or []
    original_count = len(features)
    trimmed = original_count > max_features
    kept = features[:max_features] if trimmed else features

    # 几何坐标四舍五入到 precision 位。pure-data 转换，不改 type/properties。
    rounded = [_round_feature(f, precision) for f in kept]

    out = dict(fc)
    out["features"] = rounded
    if trimmed:
        out["_trim"] = {
            "original_count": original_count,
            "kept_count": len(rounded),
            "precision": precision,
            "reason": "max_features",
        }
    return out


def _round_feature(feature: dict, precision: int) -> dict:
    geom = feature.get("geometry")
    if not isinstance(geom, dict):
        return feature
    new_geom = dict(geom)
    new_geom["coordinates"] = _round_coords(geom.get("coordinates"), precision)
    new_feat = dict(feature)
    new_feat["geometry"] = new_geom
    return new_feat


def _round_coords(coords: list, precision: int) -> list:
    """递归 round。Point→[x,y]，LineString→[[x,y],...]，Polygon→[[[x,y],...]] 等。"""
    if isinstance(coords, (int, float)):
        return round(coords, precision)
    if isinstance(coords, list):
        return [_round_coords(c, precision) for c in coords]
    return coords


def _feature_collection_bbox(fc: dict, max_features: int = 5000) -> Optional[list]:
    """Compute a GeoJSON bbox [west, south, east, north] for a FeatureCollection.

    Scans at most ``max_features`` features (a safety cap for huge collections)
    and returns ``None`` if no usable coordinates are found. Used by the
    Fetch-on-Demand descriptors so the LLM gets the spatial extent without the
    full geometry payload.
    """
    if not isinstance(fc, dict):
        return None
    feats = fc.get("features", []) or []
    min_x = min_y = float("inf")
    max_x = max_y = float("-inf")
    found = False
    for feat in feats[:max_features]:
        if not isinstance(feat, dict):
            continue
        geom = feat.get("geometry")
        if not isinstance(geom, dict):
            continue
        for x, y in _iter_coords(geom.get("coordinates")):
            found = True
            if x < min_x:
                min_x = x
            if x > max_x:
                max_x = x
            if y < min_y:
                min_y = y
            if y > max_y:
                max_y = y
    if not found:
        return None
    return [round(min_x, 6), round(min_y, 6), round(max_x, 6), round(max_y, 6)]


def _iter_coords(coordinates):
    """Yield (x, y) leaf positions from a nested GeoJSON coordinate array."""
    if not isinstance(coordinates, list):
        return
    if coordinates and isinstance(coordinates[0], (int, float)):
        # Leaf: [x, y] (or [x, y, z])
        if len(coordinates) >= 2:
            yield coordinates[0], coordinates[1]
        return
    for child in coordinates:
        yield from _iter_coords(child)


async def resolve_ref_payload(session_id: str, ref_or_alias: str) -> Any:
    """Session ref/alias → stored payload (single shared resolution path).

    Alias resolution falls back to the raw value when the store cannot
    resolve it (a bare ``ref:...`` id is already a valid key). Fetch failures
    return None — callers disclose, they don't crash.
    """
    from app.services.session_data import session_data_manager

    if not session_id or not ref_or_alias:
        return None
    ref_id = ref_or_alias
    try:
        alias_ref = await session_data_manager.resolve_alias(session_id, ref_or_alias)
        if alias_ref and alias_ref != ref_or_alias:
            ref_id = alias_ref
    except Exception:  # noqa: BLE001 — unresolved alias: treat as raw ref id
        pass
    try:
        return await session_data_manager.get(session_id, ref_id)
    except Exception as e:  # noqa: BLE001 — store outage is disclosed upstream
        logger.warning("[resolve_ref_payload] get failed for %s: %s", ref_id, e)
        return None
