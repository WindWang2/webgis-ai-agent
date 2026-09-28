"""统一瓦片/数据面缓存身份语义（W12 vNext）。

数据平面的缓存身份只有一个纪律来源：**数据版本 + 表示字节**，而非任何
route/请求生命周期。本模块是目录 MVT 身份的唯一推导点，并集中文档化全部
四条数据面身份方案的关系（各方案键结构不同是历史与语义使然，但推导纪律
——内容寻址、确定性、版本可见——必须一致）：

┌────────────────────┬───────────────────────────────────────────────┐
│ 面                 │ 身份语义                                      │
├────────────────────┼───────────────────────────────────────────────┤
│ 目录 MVT（本域）   │ 键 = (item_id, tenant_scope, fingerprint,     │
│                    │      z, x, y)；ETag = sha256(gz+fingerprint)  │
│                    │ 失效跟数据版本：catalog sync 变更 fingerprint │
│                    │ → 新键 + invalidate_item，与 route 无关。     │
│ 会话 ref MVT/PNG   │ 键 = (session_id, ref_id, z, x, y) + epoch    │
│ (routes/layer.py)  │ 代际守卫；URL v=<content_revision> 使同 ref   │
│                    │ 不同修订 = 不同条目（ADR-0214 D4 / #1112）。  │
│ 会话 FC 整包       │ ETag = 内容寻址 sha256(body)[:16]，gzip/plain │
│ (routes/layer.py)  │ 各表示可再验证（gzip 必须 mtime=0）。         │
│ 栅格窗口           │ 缓存键含 data identity + style（样式≠数据，   │
│ (routes/layer.py)  │ ADR-0075：换样式只换缓存条目，不重算）。      │
└────────────────────┴───────────────────────────────────────────────┘

共享不变量（违背即 review 阻断）：
1. ETag 一律内容寻址 sha256 截断 16 hex + 双引号；参与版本语义的字节
   （fingerprint/revision）必须进摘要输入。
2. gzip 只允许确定性压缩（mtime=0）——嵌入时间戳的 ETag 逐秒漂移，
   304 在生产永不命中。
3. 鉴权先于任何缓存查找（API-01，跨租户字节泄漏面）。
4. 数据版本变化必须对外可见（新键或新 ETag），绝不服务陈旧瓦片。
"""
import hashlib
import math
from typing import Union

#: Web Mercator 纬度裁剪（与 mvt.py 的投影钳制同值）。
WEB_MERCATOR_MAX_LAT = 85.05112878


def tile_cache_key(
    item_id: str,
    tenant_scope: str,
    fingerprint: str,
    z: int,
    x: int,
    y: int,
) -> tuple:
    """目录 MVT 缓存键（tile_cache.py 键契约的唯一构造点）。

    (item_id, tenant_scope, data_fingerprint, z, x, y)：tenant scope 防跨租户
    共享条目；fingerprint 是数据版本语义 —— 变化即切换新键，旧键由 LRU
    逐出或 sync 主动失效，绝不复用陈旧瓦片。
    """
    return (item_id, tenant_scope, fingerprint, z, x, y)


def tenant_scope_of(org_id, owner_id) -> str:
    """目录条目归属域的规范字符串（键语义的一半）。"""
    return "org:%s|owner:%s" % (org_id, owner_id)


def compute_tile_etag(body: bytes, *version_parts: Union[str, bytes]) -> str:
    """内容寻址 ETag：``"sha256(body + version_parts)[:16]"``（带双引号）。

    ``version_parts`` 携带数据版本语义（如 dataset fingerprint）：同字节体 +
    不同版本 = 不同 ETag（revision-aware，绝不让旧版本的条件请求命中新
    内容）。目录瓦片 = ``compute_tile_etag(gz, fingerprint)``，与既有
    ``_df_tile_response`` 的摘要输入逐字节一致。
    """
    h = hashlib.sha256(body)
    for part in version_parts:
        h.update(part.encode() if isinstance(part, str) else part)
    return '"%s"' % h.hexdigest()[:16]


def tile_bounds_lonlat(z: int, x: int, y: int) -> tuple:
    """Web Mercator 瓦片 (z/x/y) 的经纬度包络 (min_lon, min_lat, max_lon, max_lat)。

    纯 Python fallback 的 bbox 查询输入：把「视口切片」翻译成数据源可下推
    的经纬度窗口（QuerySpec.bbox 契约 [w, s, e, n]）。
    """
    n = 1 << z
    min_lon = x / n * 360.0 - 180.0
    max_lon = (x + 1) / n * 360.0 - 180.0

    def _lat(tile_y: float) -> float:
        latin = math.pi * (1.0 - 2.0 * tile_y / n)
        return max(-WEB_MERCATOR_MAX_LAT, min(WEB_MERCATOR_MAX_LAT, math.degrees(math.atan(math.sinh(latin)))))

    max_lat = _lat(y)
    min_lat = _lat(y + 1)
    return (min_lon, min_lat, max_lon, max_lat)


__all__ = [
    "WEB_MERCATOR_MAX_LAT",
    "tile_cache_key",
    "tenant_scope_of",
    "compute_tile_etag",
    "tile_bounds_lonlat",
]
