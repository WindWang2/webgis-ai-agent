"""
工具层共享工具函数
提供 bbox 解析、数据库会话上下文、STAC Asset 提取等基础能力
"""
import logging
from contextlib import contextmanager, asynccontextmanager
from typing import Optional, List

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

def validate_data_path(path: str, data_dir: str = "./data") -> str:
    """
    验证并规范化用户传入的文件路径，防止目录遍历攻击。

    Args:
        path: 用户传入的路径（可为相对路径或绝对路径）
        data_dir: 允许的基础目录

    Returns:
        规范化的绝对路径

    Raises:
        ValueError: 路径包含 .. 组件或解析后超出 data_dir 范围
    """
    import os
    resolved = os.path.abspath(os.path.join(data_dir, path))
    data_dir_abs = os.path.abspath(data_dir)

    # 确保解析后的路径在 data_dir 之下
    if not resolved.startswith(data_dir_abs + os.sep) and resolved != data_dir_abs:
        raise ValueError(f"非法路径: '{path}' 超出允许目录范围")

    return resolved


def std_error_response(message: str, code: str = "TOOL_ERROR", error_type: str = "") -> dict:
    """
    标准化错误响应格式，与全局异常处理器对齐。

    Args:
        message: 用户可读的错误信息
        code: 错误代码（如 VALIDATION_ERROR, NETWORK_ERROR, TOOL_ERROR）
        error_type: 异常类型名称（可选）

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
