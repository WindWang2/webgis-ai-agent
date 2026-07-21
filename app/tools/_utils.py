"""
工具层共享工具函数
提供 bbox 解析、数据库会话上下文、STAC Asset 提取等基础能力
"""
import logging
from contextlib import contextmanager, asynccontextmanager
from typing import Optional, List

# 缓存装饰器从 lib 单点导出。新工具 from app.tools._utils import cached_tool, trim_features
from app.lib.tool_cache import cached_tool  # noqa: F401

logger = logging.getLogger(__name__)


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
    except Exception as e:
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
    except Exception as e:
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


def _round_coords(coords, precision: int):
    """递归 round。Point→[x,y]，LineString→[[x,y],...]，Polygon→[[[x,y],...]] 等。"""
    if isinstance(coords, (int, float)):
        return round(coords, precision)
    if isinstance(coords, list):
        return [_round_coords(c, precision) for c in coords]
    return coords
