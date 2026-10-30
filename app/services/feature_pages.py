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
- **order**（vNext）：单字段排序，稳定序 = 排序序（v2 cursor 绑定 order
  摘要，混用/换序 → 400）；缺失值恒排最后。
- **where**（vNext）：typed 谓词 AST（allowlist op，深度/项数/值数有界，
  无任意 SQL/JS）。
- **total_matching**（vNext）：filter/bbox 存在且 n ≤ TOTAL_EXACT_LIMIT 时
  精确全扫（按 ref+revision+谓词缓存）；超限 → None（诚实未知）。
- **位置 id 投影**（vNext）：无链内稳定 id 的要素在响应中投影顶层
  ``id``=自然索引+1（仅 revision 内稳定，与 #1873 WFID 写通道无关）。
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import threading
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: 每请求 bbox 前向扫描预算（要素数）—— 有界 CPU，页密度与正确性解耦。
WINDOW_SCAN_BUDGET = 10_000

#: fields 投影白名单上限。
MAX_FIELDS = 64

#: filter/bbox 精确 total_matching 的全扫上限 —— 超限返回 None（total_unknown），
#: 绝不用 returned/已扫描数假冒全量。
TOTAL_EXACT_LIMIT = 250_000

#: order（排序序）的 FC 大小上限 —— 排序需要 O(n log n) 全量排列，超限
#: typed 400 拒绝（与 count/aggregate 的有界纪律一致），不做慢扫。
ORDER_SCAN_LIMIT = 500_000

#: typed 谓词 AST 约束（见 INTERFACE_CONTRACTS §1）。
FILTER_MAX_JSON_BYTES = 2048
FILTER_MAX_TERMS = 16
FILTER_MAX_DEPTH = 4
FILTER_MAX_VALUES = 64
FILTER_MAX_FIELD_LEN = 64

#: 排序/total/id_mode 派生结构的进程内缓存：真 LRU（命中即续期）+
#: 权重预算（Σ排列长度 ≤ 8M int ≈ 64MB 驻留帽）+ 线程锁（asyncio.to_thread
#: 走线程池，非单线程 —— R2 评审修正）。键 = (ref, revision, spec)；值只存
#: 派生数组/计数，绝不持 FC 强引用。
_DERIVED_CACHE_MAX_ENTRIES = 32
_DERIVED_CACHE_MAX_WEIGHT = 8_000_000

#: 稳定 id 解析链 —— 与 #1873 feature_edit.identity / 前端 FEATURE_ID_KEYS
#: 三方同源（layer.py::_find_feature_by_id 同链）。
FEATURE_ID_KEYS: Tuple[str, ...] = (
    "id", "OBJECTID", "fid", "osm_id", "@id", "featureId", "feature_id",
)

_CURSOR_VERSION = 1
_CURSOR_VERSION_SORTED = 2

_derived_cache: "OrderedDict[Tuple[str, str, str], Any]" = OrderedDict()
_derived_cache_weight = 0
_derived_cache_lock = threading.Lock()


def _derived_cached(key: Tuple[str, str, str], build, weight: int = 1):
    """(ref, revision, spec) → 派生值（真 LRU + 权重帽；命中续期）。

    权重 = 派生结构的元素量级（排序排列 = n），全局预算
    ``_DERIVED_CACHE_MAX_WEIGHT`` —— 防止 16+ 个 200k-500k 排列的百 MB 级
    驻留（R2 P1-1）。逐出从 LRU 尾部开始直至回到预算内。
    """
    global _derived_cache_weight
    with _derived_cache_lock:
        hit = _derived_cache.get(key)
        if hit is not None:
            _derived_cache.move_to_end(key)
            return hit
    value = build()
    with _derived_cache_lock:
        if key in _derived_cache:
            _derived_cache.move_to_end(key)
            return _derived_cache[key]
        _derived_cache[key] = value
        _derived_cache_weight += max(1, weight)
        while _derived_cache and (
            len(_derived_cache) > _DERIVED_CACHE_MAX_ENTRIES
            or _derived_cache_weight > _DERIVED_CACHE_MAX_WEIGHT
        ):
            _, evicted = _derived_cache.popitem(last=False)
            _derived_cache_weight -= max(1, _weight_of(evicted))
    return value


def _weight_of(value: Any) -> int:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        for v in value.values():
            if isinstance(v, list):
                return len(v)
        return 1
    return 1


class FeaturePageError(ValueError):
    """非法分页参数（cursor/bbox/fields）→ route 映射 400。"""


class FeatureRevisionConflict(Exception):
    """revision guard 命中：翻页起点所属版本已不是当前版本（→ 409）。"""

    def __init__(self, current_revision: Optional[int]):
        self.current_revision = current_revision
        super().__init__(f"ref revision advanced (current={current_revision!r})")


def encode_page_cursor(index: int, order: Optional[Tuple[str, int]] = None) -> str:
    if order is None:
        raw = json.dumps({"v": _CURSOR_VERSION, "i": int(index)}, separators=(",", ":"))
    else:
        raw = json.dumps(
            {"v": _CURSOR_VERSION_SORTED, "o": order_digest(order), "i": int(index)},
            separators=(",", ":"),
        )
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_page_cursor(
    token: str, order: Optional[Tuple[str, int]] = None
) -> int:
    """不透明 cursor → 下一起始位置。任何畸形输入都是 typed 400，绝不静默当第 0 页。

    v1 = 自然序索引；v2 = 排序序位置（绑定 order 摘要 —— 换排序字段/方向后
    旧 v2 cursor 的位置语义失效，显式 400 而不是静默错位页）。
    """
    try:
        padded = token + "=" * (-len(token) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        version = payload.get("v")
        idx = payload["i"]
        if isinstance(idx, bool) or not isinstance(idx, int) or idx < 0:
            raise FeaturePageError("cursor index must be a non-negative integer")
        if version == _CURSOR_VERSION:
            if order is not None:
                raise FeaturePageError("natural-order cursor used with order param")
            return idx
        if version == _CURSOR_VERSION_SORTED:
            if order is None:
                raise FeaturePageError("sorted cursor requires the same order param")
            if payload.get("o") != order_digest(order):
                raise FeaturePageError("cursor order digest mismatch")
            return idx
        raise FeaturePageError(f"unsupported cursor version: {version!r}")
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


# ─── order（单字段排序，v2 cursor） ────────────────────────────────────────


def parse_order_param(raw: Optional[str]) -> Optional[Tuple[str, int]]:
    """``"<field>"`` 升序 / ``"-<field>"`` 降序 → (field, +1|-1)。

    排序键 = properties[field]；全序分桶：数值桶(0) < 字符串桶(1) < 缺失桶(2)，
    桶内按值比较；缺失值（无 properties/无字段/bool/NaN/容器）恒排最后，
    与方向无关；并列按自然索引稳定。
    """
    if raw is None or raw == "":
        return None
    field = raw[1:] if raw.startswith("-") else raw
    if not field or len(field) > FILTER_MAX_FIELD_LEN:
        raise FeaturePageError("order field must be 1-64 chars")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in field):
        raise FeaturePageError("order field contains control characters")
    return (field, -1 if raw.startswith("-") else 1)


def order_digest(order: Tuple[str, int]) -> str:
    return hashlib.sha256(f"{order[0]}|{order[1]}".encode()).hexdigest()[:8]


def _sort_bucket(value: Any) -> Optional[Tuple[int, Any]]:
    """值 → (桶号, 可比值)；None = 缺失桶（不可比：bool/NaN/容器/null/缺席）。"""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return (0, value) if math.isfinite(value) else None
    if isinstance(value, str):
        return (1, value)
    return None


def sorted_permutation(
    features: List[Any], order: Tuple[str, int]
) -> List[int]:
    """FC 自然序 → 排序后自然索引序列（确定性、稳定、缺失恒最后）。"""
    field, direction = order
    present: List[Tuple[Tuple[int, Any], int]] = []
    missing: List[int] = []
    for i, f in enumerate(features):
        props = f.get("properties") if isinstance(f, dict) else None
        value = props.get(field) if isinstance(props, dict) else None
        bucketed = _sort_bucket(value)
        if bucketed is None:
            missing.append(i)
        else:
            present.append((bucketed, i))
    present.sort(key=lambda item: item[0], reverse=(direction < 0))
    return [i for _, i in present] + missing


def sorted_permutation_cached(
    features: List[Any], order: Tuple[str, int], cache_key: Optional[Tuple[str, str]]
) -> List[int]:
    if cache_key is None:
        return sorted_permutation(features, order)
    ref, revision = cache_key
    return _derived_cached(
        (ref, str(revision), f"order:{order_digest(order)}"),
        lambda: sorted_permutation(features, order),
        weight=len(features),
    )


# ─── where（typed 谓词 AST —— allowlist，无任意表达式） ────────────────────

_LEAF_OPS = ("eq", "ne", "in", "between", "contains", "startswith", "gte", "gt", "lte", "lt")
_RANGE_OPS = ("between", "gte", "gt", "lte", "lt")
_COMPOSITE_OPS = ("and", "or", "not")


def _validate_leaf(node: Dict[str, Any], depth: int) -> Dict[str, Any]:
    op = node.get("op")
    if op not in _LEAF_OPS:
        raise FeaturePageError(f"unknown filter op: {op!r}")
    if op in ("and", "or", "not"):  # pragma: no cover - guarded by caller
        raise FeaturePageError("composite op reached leaf validation")
    field = node.get("field")
    if not isinstance(field, str) or not field or len(field) > FILTER_MAX_FIELD_LEN:
        raise FeaturePageError("filter field must be a 1-64 char string")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in field):
        raise FeaturePageError("filter field contains control characters")
    out: Dict[str, Any] = {"op": op, "field": field}
    if op == "eq" or op == "ne":
        if "value" not in node:
            raise FeaturePageError(f"{op} requires 'value'")
        out["value"] = _check_scalar(node["value"], op)
    elif op == "in":
        values = node.get("values")
        if not isinstance(values, list) or not values or len(values) > FILTER_MAX_VALUES:
            raise FeaturePageError(f"in requires 1-{FILTER_MAX_VALUES} 'values'")
        out["values"] = [_check_scalar(v, op) for v in values]
    elif op == "between":
        bounds = []
        for key in ("gte", "lte"):
            if key not in node:
                raise FeaturePageError("between requires 'gte' and 'lte'")
            v = _check_scalar(node[key], op)
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                raise FeaturePageError("between bounds must be numeric")
            bounds.append(v)
        if bounds[0] > bounds[1]:
            raise FeaturePageError("between requires gte <= lte")
        out["gte"], out["lte"] = bounds
    elif op in _RANGE_OPS:
        # 数值范围单界算子：{"field":F,"op":gte|gt|lte|lt,"value":N}
        v = _check_scalar(node.get("value"), op)
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            raise FeaturePageError(f"{op} requires a numeric 'value'")
        out["value"] = v
    else:  # contains / startswith
        v = _check_scalar(node.get("value"), op)
        if not isinstance(v, str):
            raise FeaturePageError(f"{op} requires a string 'value'")
        out["value"] = v
    return out


def _check_scalar(value: Any, op: str) -> Any:
    ok = value is None or isinstance(value, (bool, int, float, str))
    if not ok:
        raise FeaturePageError(f"{op} values must be scalar (str/number/bool/null)")
    return value


def _validate_ast(node: Any, depth: int = 0) -> Dict[str, Any]:
    if depth > FILTER_MAX_DEPTH:
        raise FeaturePageError(f"filter nesting exceeds depth {FILTER_MAX_DEPTH}")
    if not isinstance(node, dict):
        raise FeaturePageError("filter node must be an object")
    op = node.get("op")
    if op in _COMPOSITE_OPS:
        if op == "not":
            if "term" not in node:
                raise FeaturePageError("not requires 'term'")
            return {"op": "not", "term": _validate_ast(node["term"], depth + 1)}
        terms = node.get("terms")
        if not isinstance(terms, list) or not terms or len(terms) > FILTER_MAX_TERMS:
            raise FeaturePageError(f"{op} requires 1-{FILTER_MAX_TERMS} 'terms'")
        return {"op": op, "terms": [_validate_ast(t, depth + 1) for t in terms]}
    return _validate_leaf(node, depth)


def parse_where_param(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    """JSON 字符串 → 已校验 typed 谓词 AST；畸形一律 typed 400。"""
    if raw is None or raw == "":
        return None
    if len(raw) > FILTER_MAX_JSON_BYTES:
        raise FeaturePageError(f"filter exceeds {FILTER_MAX_JSON_BYTES} bytes")
    try:
        node = json.loads(raw)
    except ValueError as e:
        raise FeaturePageError(f"filter is not valid JSON: {e}") from e
    return _validate_ast(node)


def where_digest(where: Dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(where, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:10]


def _num_eq(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return float(a) == float(b)
    return type(a) is type(b) and a == b


def evaluate_where(where: Dict[str, Any], feature: Any) -> bool:
    """已校验 AST × 单要素 → bool。缺席/null 语义：eq null 命中缺席或 null；
    其余叶算子对缺席值不命中（不抛错）。"""
    op = where["op"]
    if op == "and":
        return all(evaluate_where(t, feature) for t in where["terms"])
    if op == "or":
        return any(evaluate_where(t, feature) for t in where["terms"])
    if op == "not":
        return not evaluate_where(where["term"], feature)
    props = feature.get("properties") if isinstance(feature, dict) else None
    value = props.get(where["field"]) if isinstance(props, dict) else None
    if op == "eq":
        target = where["value"]
        if target is None:
            return value is None
        return value is not None and _num_eq(value, target)
    if op == "ne":
        target = where["value"]
        if target is None:
            return value is not None
        return value is None or not _num_eq(value, target)
    if op == "in":
        for t in where["values"]:
            if t is None:
                if value is None:
                    return True
            elif value is not None and _num_eq(value, t):
                return True
        return False
    if op == "between":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        if not math.isfinite(value):
            return False
        return where["gte"] <= value <= where["lte"]
    if op in _RANGE_OPS:
        # 单界数值范围：非数值/缺失不命中（诚实：不猜类型）
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        if not math.isfinite(value):
            return False
        bound = where["value"]
        if op == "gte":
            return value >= bound
        if op == "gt":
            return value > bound
        if op == "lte":
            return value <= bound
        return value < bound
    # contains / startswith —— 仅字符串字段（大小写不敏感）
    if not isinstance(value, str):
        return False
    needle = where["value"].lower()
    haystack = value.lower()
    return haystack.startswith(needle) if op == "startswith" else needle in haystack


def count_matching(
    features: List[Any],
    *,
    where: Optional[Dict[str, Any]],
    bbox: Optional[Tuple[float, float, float, float]],
    cache_key: Optional[Tuple[str, str]] = None,
) -> Optional[int]:
    """filter×bbox 精确匹配总数；n > TOTAL_EXACT_LIMIT → None（诚实未知）。"""
    n = len(features)
    if n > TOTAL_EXACT_LIMIT:
        return None
    if cache_key is not None and (where is not None or bbox is not None):
        ref, revision = cache_key
        cached = _derived_cached(
            (ref, str(revision), f"total:{where_digest(where) if where else '-'}:{bbox!r}"),
            lambda: _count_scan(features, where, bbox),
        )
        return cached
    return _count_scan(features, where, bbox)


def _count_scan(
    features: List[Any],
    where: Optional[Dict[str, Any]],
    bbox: Optional[Tuple[float, float, float, float]],
) -> int:
    total = 0
    for f in features:
        if where is not None and not evaluate_where(where, f):
            continue
        if bbox is not None and not _feature_intersects_bbox(f, bbox):
            continue
        total += 1
    return total


# ─── 稳定 id 投影（M3 前半：浏览通道的位置 id 披露） ───────────────────────


def resolve_feature_stable_id(feature: Any) -> Optional[str]:
    """FEATURE_ID_KEYS 链（顶层 id 优先）→ 稳定 id；无 → None。

    与 #1873 feature_edit.identity.resolve_feature_id 同语义（本模块不依赖
    feature_edit，避免服务方向反转）。
    """
    if not isinstance(feature, dict):
        return None
    top = feature.get("id")
    if isinstance(top, (str, int)) and not isinstance(top, bool):
        return str(top)
    props = feature.get("properties")
    if isinstance(props, dict):
        for key in FEATURE_ID_KEYS:
            value = props.get(key)
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                return str(value)
    return None


def id_mode_of(
    features: List[Any], cache_key: Optional[Tuple[str, str]] = None
) -> Dict[str, Any]:
    """{"id_mode": native|positional|mixed, "positional_count": int, "duplicate_id_count": int}。

    duplicate_id_count = 链内 id 重复的要素数（首个持有者之外）——重复 id 的
    寻址语义是"首中即返"（layer.py::_find_feature_by_id 现状），必须披露。
    """
    def scan() -> Dict[str, Any]:
        seen: set = set()
        with_id = 0
        duplicates = 0
        for f in features:
            sid = resolve_feature_stable_id(f)
            if sid is None:
                continue
            with_id += 1
            if sid in seen:
                duplicates += 1
            else:
                seen.add(sid)
        n = len(features)
        mode = "native" if with_id == n and n > 0 else (
            "positional" if with_id == 0 else "mixed"
        )
        if n == 0:
            mode = "native"
        return {"id_mode": mode, "positional_count": n - with_id, "duplicate_id_count": duplicates}

    if cache_key is None:
        return scan()
    ref, revision = cache_key
    return _derived_cached((ref, str(revision), "id_mode"), scan)


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


def project_feature(
    feature: Any,
    fields: Optional[Sequence[str]],
    positional_id: Optional[int] = None,
) -> Any:
    """properties 白名单投影（id/geometry/其它顶层键保留）。

    positional_id：要素无链内稳定 id 时投影顶层 ``id``（= FC 自然索引+1），
    供表格行/选择过滤以 ``$id`` 寻址 —— 仅 revision 内稳定（reason_code
    ``positional_id_projected`` 披露）；有链内 id 的要素绝不覆盖其 id。
    """
    if not fields and positional_id is None:
        return feature
    if not isinstance(feature, dict):
        return feature
    projected = dict(feature)
    if fields:
        props = feature.get("properties")
        if isinstance(props, dict):
            projected["properties"] = {k: v for k, v in props.items() if k in fields}
    if positional_id is not None and projected.get("id") is None:
        projected["id"] = positional_id
    return projected


def page_features(
    fc: Any,
    *,
    limit: int = 200,
    cursor: Optional[str] = None,
    bbox: Optional[Tuple[float, float, float, float]] = None,
    fields: Optional[Sequence[str]] = None,
    order: Optional[Tuple[str, int]] = None,
    where: Optional[Dict[str, Any]] = None,
    cache_key: Optional[Tuple[str, str]] = None,
) -> Dict[str, Any]:
    """FC → 稳定序窗口页。

    返回::

        {"features": [...], "next_cursor": str|None, "has_more": bool,
         "returned": int, "scanned": int, "truncated": bool,
         "positional_ids": int}

    扫描语义：从 cursor 前向收集 bbox/where 匹配至 ``limit`` 或扫描预算耗尽；
    停止点即下一页 cursor（不漏要素，CPU 有界）。无 bbox/where 时即纯切片。
    ``truncated`` = 因 WINDOW_SCAN_BUDGET 停止且该页未集满（页密度被预算
    削减的显式信号，与 has_more 正交）。``order`` 时稳定序 = 排序序（v2
    cursor 绑定 order 摘要）。位置 id 只投影给无链内 id 的要素。
    """
    features = fc.get("features") if isinstance(fc, dict) else None
    if not isinstance(features, list):
        raise FeaturePageError("payload is not a FeatureCollection")
    n = len(features)
    if order is not None:
        if n > ORDER_SCAN_LIMIT:
            raise FeaturePageError(
                f"order unsupported for feature_count > {ORDER_SCAN_LIMIT} (use natural order)")
        perm = sorted_permutation_cached(features, order, cache_key)
        position = decode_page_cursor(cursor, order) if cursor else 0
        if position >= n:
            return {"features": [], "next_cursor": None, "has_more": False,
                    "returned": 0, "scanned": 0, "truncated": False, "positional_ids": 0}
        out: List[Any] = []
        positional_ids = 0
        i = position
        scan_cap = min(n, position + WINDOW_SCAN_BUDGET)
        while i < scan_cap and len(out) < limit:
            natural = perm[i]
            f = features[natural]
            i += 1
            if where is not None and not evaluate_where(where, f):
                continue
            if bbox is not None and not _feature_intersects_bbox(f, bbox):
                continue
            pid: Optional[int] = None
            if resolve_feature_stable_id(f) is None:
                pid = natural + 1
                positional_ids += 1
            out.append(project_feature(f, fields, pid))
        has_more = i < n
        truncated = len(out) < limit and i >= scan_cap and i < n
        return {
            "features": out,
            "next_cursor": encode_page_cursor(i, order) if has_more else None,
            "has_more": has_more,
            "returned": len(out),
            "scanned": i - position,
            "truncated": truncated,
            "positional_ids": positional_ids,
        }
    start = decode_page_cursor(cursor) if cursor else 0
    if start >= n:
        return {"features": [], "next_cursor": None, "has_more": False,
                "returned": 0, "scanned": 0, "truncated": False, "positional_ids": 0}
    out = []
    positional_ids = 0
    i = start
    scan_cap = min(n, start + WINDOW_SCAN_BUDGET)
    while i < scan_cap and len(out) < limit:
        f = features[i]
        i += 1
        if where is not None and not evaluate_where(where, f):
            continue
        if bbox is not None and not _feature_intersects_bbox(f, bbox):
            continue
        pid = None
        if resolve_feature_stable_id(f) is None:
            pid = i  # i 已自增 = 自然索引+1
            positional_ids += 1
        out.append(project_feature(f, fields, pid))
    has_more = i < n
    truncated = len(out) < limit and i >= scan_cap and i < n
    return {
        "features": out,
        "next_cursor": encode_page_cursor(i) if has_more else None,
        "has_more": has_more,
        "returned": len(out),
        "scanned": i - start,
        "truncated": truncated,
        "positional_ids": positional_ids,
    }


__all__ = [
    "WINDOW_SCAN_BUDGET",
    "MAX_FIELDS",
    "TOTAL_EXACT_LIMIT",
    "FILTER_MAX_JSON_BYTES",
    "FILTER_MAX_TERMS",
    "FILTER_MAX_DEPTH",
    "FILTER_MAX_VALUES",
    "FEATURE_ID_KEYS",
    "FeaturePageError",
    "FeatureRevisionConflict",
    "encode_page_cursor",
    "decode_page_cursor",
    "parse_bbox_param",
    "parse_fields_param",
    "parse_order_param",
    "parse_where_param",
    "evaluate_where",
    "order_digest",
    "where_digest",
    "sorted_permutation",
    "count_matching",
    "resolve_feature_stable_id",
    "id_mode_of",
    "page_features",
    "project_feature",
]
