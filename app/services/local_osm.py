"""本地 OSM 数据服务：一次性 PBF→GPKG ETL + 主题查询。

数据流：
- ``python manage.py osm-ingest`` 流式扫描 ``china-*.osm.pbf``（pyosmium，
  GDAL OSM 驱动 C++ 层过滤），写入
  ``<LOCAL_GEODATA_DIR>/osm_gpkg/<theme>.gpkg``（geopandas/pyogrio，GPKG
  自带 R-tree 空间索引），幂等（存在即跳过，--force 覆盖）。
- 查询（工具与 HTTP 路由共用）：``gpd.read_file(gpkg, bbox=...)`` 走 R-tree，
  内存做属性过滤，返回 GeoJSON FeatureCollection。

主题规则（v1 轻量四主题；建筑/面状地块需关系组装，留 --themes 扩展位）：
- pois: 带 amenity/shop/tourism/leisure/office/healthcare 的节点
- roads: highway 线 / railways: railway 线 / waterways: waterway 线
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

import pandas as pd

from app.core.config import settings

logger = logging.getLogger(__name__)

THEME_SPECS: Dict[str, Dict[str, Any]] = {
    "pois": {
        "kind": "node",
        "tag_keys": ("amenity", "shop", "tourism", "leisure", "office", "healthcare"),
        "description": "兴趣点（amenity/shop/tourism/leisure/office/healthcare 节点）",
    },
    "roads": {
        "kind": "way",
        "tag_keys": ("highway",),
        "description": "道路中心线（highway 线要素）",
    },
    "railways": {
        "kind": "way",
        "tag_keys": ("railway",),
        "description": "铁路线（railway 线要素）",
    },
    "waterways": {
        "kind": "way",
        "tag_keys": ("waterway",),
        "description": "水系线（waterway 河流/渠道）",
    },
}

_MAX_TAGS_JSON_CHARS = 512
_RESULT_COLUMNS = ["osm_id", "name", "category", "tags"]
# Layer names are THEME_SPECS keys (identifiers, not bindable). Static map
# keeps bandit B608 off execute() while still refusing unknown themes.
_THEME_COUNT_SQL = {name: 'SELECT COUNT(*) FROM "' + name + '"' for name in THEME_SPECS}


def osm_gpkg_dir() -> Path:
    root = (settings.LOCAL_GEODATA_DIR or "").strip()
    return (Path(root).expanduser() if root else Path("data")) / "osm_gpkg"


def theme_gpkg_path(theme: str) -> Path:
    if theme not in THEME_SPECS:
        raise KeyError(theme)
    return osm_gpkg_dir() / f"{theme}.gpkg"


def default_pbf_path() -> Optional[Path]:
    """在 LOCAL_GEODATA_DIR 下自动发现 china-*.osm.pbf。"""
    root = (settings.LOCAL_GEODATA_DIR or "").strip()
    if not root:
        return None
    candidates = sorted(Path(root).expanduser().glob("china-*.osm.pbf"))
    return candidates[-1] if candidates else None


# ── 查询面（工具 / 路由共用）───────────────────────────────────────────────


def _catalog_row(theme: str) -> Dict[str, Any]:
    path = theme_gpkg_path(theme)
    spec = THEME_SPECS[theme]
    row: Dict[str, Any] = {
        "theme": theme,
        "description": spec["description"],
        "available": path.exists(),
        "gpkg": str(path),
        "feature_count": None,
    }
    if path.exists():
        try:
            import sqlite3

            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                row["feature_count"] = conn.execute(
                    _THEME_COUNT_SQL[theme]
                ).fetchone()[0]
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 - 目录页必须比计数更可用
            row["feature_count_error"] = str(exc)[:200]
    return row


def catalog() -> Dict[str, Any]:
    return {
        "themes": [_catalog_row(t) for t in THEME_SPECS],
        "note": "查询用 query_local_osm(theme, bbox, ...)；bbox=[minx,miny,maxx,maxy] WGS84。",
    }


_BBOX_RE = re.compile(r"^\s*\[?(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\]?\s*$")


def _parse_bbox(bbox: Any) -> Optional[Tuple[float, float, float, float]]:
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        try:
            return tuple(float(v) for v in bbox)  # type: ignore[return-value]
        except (TypeError, ValueError):
            return None
    if isinstance(bbox, str):
        m = _BBOX_RE.match(bbox)
        if m:
            return tuple(float(g) for g in m.groups())  # type: ignore[return-value]
    return None


def _sql_literal(value: str) -> str:
    """SQLite 字符串字面量（单引号翻倍）。"""
    return "'" + str(value).replace("'", "''") + "'"


def _sql_like(value: str) -> str:
    """LIKE 模式串：转义 % _ \\ 通配符（配合 ESCAPE '\\'）。"""
    escaped = (
        str(value)
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )
    return "'" + escaped.replace("'", "''") + "'"


def _build_where(
    theme: str,
    name_like: Optional[str],
    tag: Optional[str],
) -> Optional[str]:
    """把 name/tag 过滤下推为 SQLite where 子句（在 C++/SQLite 层执行）。

    内存契约：所有过滤都在数据源侧完成，Python 侧只物化 max_features 行。
    """
    clauses = []
    if name_like:
        clauses.append(
            f"name LIKE ('%' || {_sql_like(name_like)} || '%') ESCAPE '\\'"
        )
    if tag:
        key, _, value = str(tag).partition("=")
        key = key.strip()
        value = value.strip()
        if value:
            # 主题键在 ingest 时折进 category 列；非主题键回退 tags 文本包含
            # （tags 形如 "key": "value"，见 _slim_theme_frame 的归一）。
            theme_keys = set(THEME_SPECS[theme]["tag_keys"])
            or_parts = []
            if key in theme_keys:
                or_parts.append(f"LOWER(category) = LOWER({_sql_literal(value)})")
            needle = f'"{key}": "{value}"'
            or_parts.append(
                f"tags LIKE ('%' || {_sql_like(needle)} || '%') ESCAPE '\\'"
            )
            clauses.append("(" + " OR ".join(or_parts) + ")")
    return " AND ".join(clauses) if clauses else None


def query_osm_features(
    theme: str,
    bbox: Any,
    name_like: Optional[str] = None,
    tag: Optional[str] = None,
    limit: int = 200,
) -> Dict[str, Any]:
    """按主题 + bbox（WGS84 [minx,miny,maxx,maxy]）查询。

    内存契约：bbox/名称/标签过滤全部下推到 GPKG 的 SQLite/R-tree 层，
    ``max_features=limit`` 保证 Python 侧最多物化 limit（≤2000）行——
    省级大 bbox 也不会把数百万行拉进内存（此前先全读后截断的实现在
    全国 roads 上会吃数 GB RSS）。
    """
    import pyogrio

    limit = max(1, min(int(limit), 2000))
    if theme not in THEME_SPECS:
        return {
            "error": f"未知主题 '{theme}'（可选: {', '.join(THEME_SPECS)}）",
            "correction_hint": "先用 get_local_osm_catalog 查看已 ingest 的主题。",
        }
    parsed = _parse_bbox(bbox)
    if parsed is None:
        return {
            "error": "bbox 必须是 [minx,miny,maxx,maxy]（WGS84 经纬度）",
            "correction_hint": "可先用 get_local_admin_boundary 拿行政区边界再 total_bounds 取 bbox。",
        }
    minx, miny, maxx, maxy = parsed
    if not (-180 <= minx < maxx <= 180 and -90 <= miny < maxy <= 90):
        return {"error": "bbox 数值不合法（需满足 -180≤minx<maxx≤180、-90≤miny<maxy≤90）"}

    path = theme_gpkg_path(theme)
    if not path.exists():
        return {
            "error": f"主题 '{theme}' 尚未导入（缺少 {path}）",
            "correction_hint": "运行 python manage.py osm-ingest 预处理后重试；"
            "或改用在线工具（search_poi / amap）。",
        }
    where = _build_where(theme, name_like, tag)
    try:
        gdf = pyogrio.read_dataframe(
            str(path),
            layer=theme,
            bbox=(minx, miny, maxx, maxy),
            columns=_RESULT_COLUMNS,
            where=where,
            max_features=limit,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("[local_osm] read %s failed (where=%r): %s", path, where, exc)
        return {"error": f"读取 {theme} 数据失败: {exc}"}

    if gdf is None or len(gdf) == 0:
        return {
            "type": "FeatureCollection",
            "features": [],
            "count": 0,
            "bbox": [minx, miny, maxx, maxy],
            "note": "无匹配要素——可扩大 bbox 或放宽过滤条件。",
        }

    try:
        # tags JSON 串可能较长，输出前截断（行数已被 max_features 界定）。
        if "tags" in gdf.columns:
            gdf = gdf.copy()
            gdf["tags"] = gdf["tags"].astype(str).str.slice(0, _MAX_TAGS_JSON_CHARS)
    except Exception:  # noqa: BLE001
        pass
    payload = json.loads(gdf.to_json())
    features = payload.get("features", [])
    truncated = len(features) >= limit
    return {
        "type": "FeatureCollection",
        "features": features,
        "count": len(features),
        "bbox": [minx, miny, maxx, maxy],
        **({"truncated": True, "note": f"结果被截断至 limit={limit}，可缩小 bbox 或加过滤。"} if truncated else {}),
    }


# ── ETL 面（manage.py osm-ingest）─────────────────────────────────────────

# GDAL OSM 驱动的自定义配置：把主题标签暴露为列，使 where 过滤发生在 C++ 层。
# （默认 osmconf 只暴露 name/barrier/highway 等少数列，amenity/shop 等都埋在
# other_tags 里，无法 SQL 过滤。）
_OSMCONF_INI = """[points]
attributes=amenity,shop,tourism,leisure,office,healthcare,name
other_tags=yes
[lines]
attributes=highway,railway,waterway,name
other_tags=yes
[multilinestrings]
attributes=name
other_tags=no
[multipolygons]
attributes=name
other_tags=no
[other_relations]
attributes=name
other_tags=no
"""

# theme -> (GDAL layer, where 子句, 分类键)。每个主题一次独立的 C++ 全文件扫描。
_THEME_QUERIES = {
    "pois": (
        "points",
        "amenity IS NOT NULL OR shop IS NOT NULL OR tourism IS NOT NULL"
        " OR leisure IS NOT NULL OR office IS NOT NULL OR healthcare IS NOT NULL",
        ("amenity", "shop", "tourism", "leisure", "office", "healthcare"),
    ),
    "roads": ("lines", "highway IS NOT NULL", ("highway",)),
    "railways": ("lines", "railway IS NOT NULL", ("railway",)),
    "waterways": ("lines", "waterway IS NOT NULL", ("waterway",)),
}


def _ensure_osmconf() -> Path:
    out_dir = osm_gpkg_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    conf = out_dir / "osmconf.ini"
    if not conf.exists():
        conf.write_text(_OSMCONF_INI, encoding="utf-8")
    return conf


def _available_memory_gb() -> Optional[float]:
    """读 /proc/meminfo 的 MemAvailable（GB）；非 Linux 返回 None。"""
    try:
        with open("/proc/meminfo", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return float(line.split()[1]) / (1024 * 1024)
    except OSError:
        pass
    return None


# 全国级 lines 主题（roads 850 万行）实测峰值 ~6-10GB GeoDataFrame RSS；
# 低于此余量直接拒绝，避免重演 pyosmium 时代把机器 OOM 的教训。
_MIN_FREE_GB = 8.0


def ingest_pbf(
    pbf_path: Path,
    themes: Iterable[str],
    *,
    force: bool = False,
    limit_objects: int = 0,
    flush_rows: int = 0,  # 兼容旧签名，GDAL 实现按主题整体写库
    progress_cb: Optional[Any] = None,
    idx: str = "",  # 兼容旧签名，pyosmium 实现已弃用
) -> Dict[str, Any]:
    """PBF → 主题 GPKG（pyogrio/GDAL OSM 驱动，C++ 层过滤）。

    实现演进：pyosmium 路线在真实全国数据上不可用——Python 回调对 10 亿
    节点过慢，内存索引 OOM（实测 39GB+ RSS），磁盘索引在本环境段错误
    （SIGBUS）。GDAL OSM 驱动单次全文件扫描 ~12s，where 过滤在 C++ 层，
    每主题一次扫描、整块写库。

    内存契约：每主题一次全量读（lines 主题峰值 ~6-10GB RSS），写库后立即
    释放再进下一主题。启动前检查 MemAvailable ≥ 8GB，不足则拒绝（--force
    可强行，自担风险）。
    """
    import os

    import pyogrio

    themes = [t for t in themes if t in THEME_SPECS]
    if not themes:
        raise ValueError(f"无有效主题（可选: {', '.join(THEME_SPECS)}）")
    if limit_objects:
        # GDAL 路线没有对象级 limit；保留参数兼容 manage.py 调试入口。
        logger.warning("[local_osm] limit_objects 在 GDAL 实现下被忽略")

    free_gb = _available_memory_gb()
    if free_gb is not None and free_gb < _MIN_FREE_GB and not force:
        raise MemoryError(
            f"可用内存 {free_gb:.1f}GB 低于全国级导入的安全阈值 {_MIN_FREE_GB}GB"
            "（lines 主题峰值 ~6-10GB RSS）。清理内存后重试，或 --force 强行。"
        )

    conf = _ensure_osmconf()
    os.environ["OSM_CONFIG_FILE"] = str(conf)

    out_dir = osm_gpkg_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs = {t: theme_gpkg_path(t) for t in themes}
    existing = [t for t in themes if outputs[t].exists()]
    if existing and not force:
        return {"skipped": existing, "note": "已存在的主题默认跳过；--force 覆盖重导。"}
    for t in existing:
        outputs[t].unlink()

    counts: Dict[str, int] = {}
    import gc

    for theme in themes:
        layer, where, cat_keys = _THEME_QUERIES[theme]
        raw = pyogrio.read_dataframe(str(pbf_path), layer=layer, where=where)
        out = _slim_theme_frame(raw, cat_keys)
        pyogrio.write_dataframe(out, outputs[theme], layer=theme, driver="GPKG")
        counts[theme] = len(out)
        del raw, out
        gc.collect()  # 立即把 GeoDataFrame 的几何缓冲归还给 OS
        if progress_cb is not None:
            try:
                progress_cb(theme, counts[theme])
            except Exception:  # noqa: BLE001 - 进度回调绝不影响导入
                pass

    return {
        "pbf": str(pbf_path),
        "themes": {
            t: {"rows": counts[t], "gpkg": str(outputs[t])} for t in themes
        },
    }


def _slim_theme_frame(raw: Any, cat_keys: Tuple[str, ...]) -> Any:
    """收敛为查询契约列：osm_id/name/category/tags + geometry。"""
    import geopandas as _gpd

    if raw is None or len(raw) == 0:
        return _gpd.GeoDataFrame(
            {
                "osm_id": pd.Series(dtype="object"),
                "name": pd.Series(dtype="object"),
                "category": pd.Series(dtype="object"),
                "tags": pd.Series(dtype="object"),
            },
            geometry=_gpd.GeoSeries(dtype="geometry"),
            crs="EPSG:4326",
        )
    frame = raw.copy()
    # category：主题键里第一个非空值（如 pois 的 amenity/shop...）。
    category = None
    for key in cat_keys:
        if key in frame.columns:
            col = frame[key]
            category = col if category is None else category.fillna(col)
    if category is None:
        category = pd.Series([""] * len(frame), index=frame.index)
    # tags：GDAL other_tags 形如 "k"=>"v" 的成对串，归一为查询侧的
    # "key": "value" 形态（值内含 => 的边缘情形忽略，v1 取舍）。
    tags = ""
    if "other_tags" in frame.columns:
        tags = (
            frame["other_tags"]
            .astype("string")
            .fillna("")
            .str.replace('=>"', '": "', regex=False)
            .str.slice(0, _MAX_TAGS_JSON_CHARS)
        )
    out = _gpd.GeoDataFrame(
        {
            "osm_id": frame["osm_id"].astype("string") if "osm_id" in frame.columns else "",
            "name": frame["name"].astype("string").fillna("") if "name" in frame.columns else "",
            "category": category.astype("string").fillna(""),
            "tags": tags,
        },
        geometry=frame.geometry,
        crs=frame.crs if frame.crs else "EPSG:4326",
    )
    return out
