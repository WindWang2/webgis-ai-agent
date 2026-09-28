"""会话 ref 窗口/分页读（W12 vNext）—— 属性与小批 feature 浏览的事实源。

ADR-0047 契约的浏览半边补全：显示走 MVT（additive），但**浏览**（属性
表/检查器/选择详情）此前只有「整包 FC」一条路 —— 2 万～10 万要素的
inline-only ref 在 ref-source-resolver 直接拒绝挂载，属性浏览无从谈起。
本模块给 ``GET /layers/data/{ref}/features`` 提供窗口语义（route 只做
协议适配）：

- **稳定序** = FC 数组自然序。会话 ref 载荷在单个 revision 内不可变
  （#1112 修复后 overwrite 必须推 content_revision）—— 序以 revision
  为锚，revision 变化即换新序。
- **cursor** = base64({"v":1,"i":下一起始下标})，不透明、单调；服务端
  不持有任何翻页状态（断线/取消零清理负担）。
- **revision guard**：客户端带 ``v=<revision>``；与服务端当前 revision
  不符 → :class:`FeatureRevisionConflict`（route → 409 + 当前 revision）
  —— 翻页中途 ref 被覆写时，拼接页是不诚实的数据集，绝不静默跨版。
- **bbox 窗口**：从 cursor 前向扫描、预算内收集匹配。扫描有界（
  WINDOW_SCAN_BUDGET 要素/请求）且**不漏**：扫描停止点即下一页 cursor
  —— 稀疏 bbox 只影响页密度，不影响正确性。
- **fields 投影**：properties 白名单（id/geometry 保留）；字段数上限
  MAX_FIELDS。
"""
from __future__ import annotations

import base64
import binascii
import json
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: 每请求 bbox 前向扫描预算（要素数）—— 有界 CPU，页密度与正确性解耦。
WINDOW_SCAN_BUDGET = 10_000

#: fields 投影白名单上限。
MAX_FIELDS = 64

_CURSOR_VERSION = 1


class FeaturePageError(ValueError):
    """非法分页参数（cursor/bbox/fields）→ route 映射 400。"""


class FeatureRevisionConflict(Exception):
    """revision guard 命中：翻页起点所属版本已不是当前版本（→ 409）。"""

    def __init__(self, current_revision: Optional[int]):
        self.current_revision = current_revision
        super().__init__(f"ref revision advanced (current={current_revision!r})")


def encode_page_cursor(index: int) -> str:
    raw = json.dumps({"v": _CURSOR_VERSION, "i": int(index)}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_page_cursor(token: str) -> int:
    """不透明 cursor → 下一起始下标。任何畸形输入都是 typed 400，绝不静默当第 0 页。"""
    try:
        padded = token + "=" * (-len(token) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        idx = payload["i"]
        if not isinstance(idx, int) or idx < 0:
            raise FeaturePageError("cursor index must be a non-negative integer")
        return idx
    except FeaturePageError:
        raise
    except (binascii.Error, ValueError, KeyError, TypeError, UnicodeDecodeError) as e:
        raise FeaturePageError(f"malformed cursor: {type(e).__name__}") from e


def parse_bbox_param(raw: Optional[str]) -> Optional[Tuple[float, float, float, float]]:
    """``"w,s,e,n"`` CSV → (w, s, e, n)；None 透传；畸形 → typed 400。"""
    if raw is None or raw == "":
        return None
    parts = raw.split(",")
    if len(parts) != 4:
        raise FeaturePageError("bbox must be 'w,s,e,n' (4 comma-separated numbers)")
    try:
        w, s, e, n = (float(p) for p in parts)
    except ValueError as e:
        raise FeaturePageError(f"bbox values must be numeric: {e}") from e
    if not all(math.isfinite(v) for v in (w, s, e, n)):
        raise FeaturePageError("bbox values must be finite")
    if w > e or s > n:
        raise FeaturePageError("bbox must satisfy w<=e and s<=n")
    return (w, s, e, n)


def parse_fields_param(raw: Optional[str]) -> Optional[List[str]]:
    """CSV 字段投影白名单；去空、去重保序、上限 MAX_FIELDS。"""
    if raw is None or raw == "":
        return None
    fields = [f.strip() for f in raw.split(",") if f.strip()]
    if not fields:
        return None
    if len(fields) > MAX_FIELDS:
        raise FeaturePageError(f"too many fields (max {MAX_FIELDS})")
    seen: set = set()
    ordered = []
    for f in fields:
        if f not in seen:
            seen.add(f)
            ordered.append(f)
    return ordered


def _geometry_bbox(coords: Any) -> Optional[Tuple[float, float, float, float]]:
    """递归求任意 GeoJSON 坐标结构的 (minx, miny, maxx, maxy)。"""
    if isinstance(coords, (list, tuple)) and coords and isinstance(coords[0], (int, float)):
        if len(coords) < 2:
            return None
        x, y = float(coords[0]), float(coords[1])
        if not (math.isfinite(x) and math.isfinite(y)):
            return None
        return (x, y, x, y)
    if isinstance(coords, (list, tuple)):
        best = None
        for part in coords:
            b = _geometry_bbox(part)
            if b is None:
                continue
            best = b if best is None else (
                min(best[0], b[0]), min(best[1], b[1]), max(best[2], b[2]), max(best[3], b[3]),
            )
        return best
    return None


def _feature_intersects_bbox(feature: Any, bbox: Tuple[float, float, float, float]) -> bool:
    """窗口粗滤（要素包络 vs 窗口相交）。诚实语义：粗滤是保守超集 ——
    可能包含边界外要素（显示层本就再裁剪），绝不漏。"""
    if not isinstance(feature, dict):
        return False
    geom = feature.get("geometry")
    if not isinstance(geom, dict):
        return False
    b = _geometry_bbox(geom.get("coordinates"))
    if b is None:
        return False
    w, s, e, n = bbox
    return not (b[2] < w or b[0] > e or b[3] < s or b[1] > n)


def project_feature(feature: Any, fields: Optional[Sequence[str]]) -> Any:
    """properties 白名单投影（id/geometry/其它顶层键保留）。"""
    if not fields or not isinstance(feature, dict):
        return feature
    props = feature.get("properties")
    projected = feature
    if isinstance(props, dict):
        projected = dict(feature)
        projected["properties"] = {k: v for k, v in props.items() if k in fields}
    return projected


def page_features(
    fc: Any,
    *,
    limit: int = 200,
    cursor: Optional[str] = None,
    bbox: Optional[Tuple[float, float, float, float]] = None,
    fields: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """FC → 稳定序窗口页。

    返回::

        {"features": [...], "next_cursor": str|None, "has_more": bool,
         "returned": int, "scanned": int}

    扫描语义：从 cursor 前向收集 bbox 匹配至 ``limit`` 或扫描预算耗尽；
    停止点即下一页 cursor（不漏要素，CPU 有界）。无 bbox 时即纯切片。
    """
    features = fc.get("features") if isinstance(fc, dict) else None
    if not isinstance(features, list):
        raise FeaturePageError("payload is not a FeatureCollection")
    n = len(features)
    start = decode_page_cursor(cursor) if cursor else 0
    if start >= n:
        return {"features": [], "next_cursor": None, "has_more": False, "returned": 0, "scanned": 0}
    out: List[Any] = []
    i = start
    scan_cap = min(n, start + WINDOW_SCAN_BUDGET)
    while i < scan_cap and len(out) < limit:
        f = features[i]
        i += 1
        if bbox is not None and not _feature_intersects_bbox(f, bbox):
            continue
        out.append(project_feature(f, fields))
    has_more = i < n
    return {
        "features": out,
        "next_cursor": encode_page_cursor(i) if has_more else None,
        "has_more": has_more,
        "returned": len(out),
        "scanned": i - start,
    }


__all__ = [
    "WINDOW_SCAN_BUDGET",
    "MAX_FIELDS",
    "FeaturePageError",
    "FeatureRevisionConflict",
    "encode_page_cursor",
    "decode_page_cursor",
    "parse_bbox_param",
    "parse_fields_param",
    "page_features",
    "project_feature",
]
