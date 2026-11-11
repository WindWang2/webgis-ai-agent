"""selection_handles — 服务端不透明选择集句柄（G24 / P3）。

目的：框选/条件选择的**权威全集**描述活在服务端（源 ref + revision + 封闭
scope），客户端与下游消费者（属性表分页、标注、字段计算、导出、Pi 工具）
只持有不透明 handle —— 不把「前 50 个命中」当全集，也不把已加载瓦片当
全集。

安全与授权纪律（reviewer 重点）：
- handle 不是绕过授权的长期 capability：每次消费都重新走会话鉴权
  （路由层 require_owned_session）+ ref 归属校验（get_ref_data_shared），
  handle 本身只承载「寻址」，不承载任何权限；
- handle 绑定 (session_id, ref_id, content_revision)：数据刷新/编辑使
  revision 前进 → 旧 handle 消费得到 410 stale（绝不静默换语义）；
- 进程内 LRU + TTL（默认 10 分钟空闲）+ 单会话配额：重启即失效（诚实
  410），不能被长期囤积；
- scope 是封闭词表（bbox / where typed AST / 有界 ids），bbox 语义 =
  粗滤相交（保守超集，与 /features 的 bbox_coarse 同语义并披露），
  绝不把「bbox 内」升级为「授予这些对象修改权」—— 写通道（feature_edit）
  永远按显式稳定 id 逐对象走自己的授权。

计数诚实纪律：
- count 仅在可证时精确（n ≤ TOTAL_EXACT_LIMIT）；超限 count_exact=False
  + count=None —— 消费者不得把未知表述为全集；
- ids scope：创建时即解析 id→要素（缺失 id 明示 missing_ids），count 恒
  精确；id 解析链与 FEATURE_ID_KEYS 三侧同源（_wfid 链首）。
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from app.services.feature_pages import (
    FEATURE_ID_KEYS,
    TOTAL_EXACT_LIMIT,
    WINDOW_SCAN_BUDGET,
    FeaturePageError,
    _feature_intersects_bbox,
    _validate_ast,
    count_matching,
    decode_page_cursor,
    encode_page_cursor,
    evaluate_where,
    page_features,
    project_feature,
    where_digest,
)

logger = logging.getLogger(__name__)

# ─── 预算与配额 ─────────────────────────────────────────────────────────────
SELECTION_TTL_SECONDS = 600          # 空闲 TTL：超时未消费 → 410 expired
MAX_HANDLES_PER_SESSION = 64         # 单会话并存 handle 配额（LRU 逐出最旧）
MAX_SCOPE_IDS = 200                  # ids scope 显式 id 数上限（封闭有界）
MAX_MATERIALIZE_FEATURES = 10_000    # 物化成 ref 的要素数上限（typed 超限拒绝）


class SelectionHandleError(Exception):
    """typed 错误：code ∈ {not_found, stale, expired, bad_scope,
    bad_request, over_budget}；路由层映射 404/410/422/400。"""

    def __init__(self, code: str, message: str, **detail: Any):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


# ─── scope（封闭词表） ──────────────────────────────────────────────────────

def _normalise_bbox(bbox: Sequence[float]) -> List[Tuple[float, float, float, float]]:
    """单 bbox → 规范化 box 列表；跨反子午线（w > e）拆成两箱（A14）。

    返回 1 或 2 个 box；非法输入抛 bad_scope。
    """
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        raise SelectionHandleError("bad_scope", "bbox 必须是 [w,s,e,n] 四元组")
    try:
        w, s, e, n = (float(v) for v in bbox)
    except (TypeError, ValueError) as exc:
        raise SelectionHandleError("bad_scope", "bbox 坐标必须是数值") from exc
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in bbox):
        raise SelectionHandleError("bad_scope", "bbox 坐标必须是数值")
    if not (-180 <= w <= 180 and -180 <= e <= 180 and -90 <= s <= 90 and -90 <= n <= 90):
        raise SelectionHandleError("bad_scope", "bbox 超出 WGS84 值域")
    if s > n:
        s, n = n, s
    if w <= e:
        return [(w, s, e, n)]
    # w > e：跨反子午线 → 拆两箱（与 mvt 的 AM 拆分同语义；不发明 wrap 环）
    return [(w, s, 180.0, n), (-180.0, s, e, n)]


def parse_scope_payload(body: Dict[str, Any]) -> Dict[str, Any]:
    """请求体 scope → 内部规范 scope（校验封闭词表；抛 bad_scope）。"""
    if not isinstance(body, dict):
        raise SelectionHandleError("bad_scope", "scope 必须是对象")
    kind = body.get("kind")
    if kind == "bbox":
        boxes = _normalise_bbox(body.get("bbox"))
        return {"kind": "bbox", "boxes": boxes}
    if kind == "where":
        ast = body.get("where")
        if not isinstance(ast, dict):
            raise SelectionHandleError("bad_scope", "where 必须是 typed 谓词 AST 对象")
        try:
            validated = _validate_ast(ast)
        except FeaturePageError as exc:
            raise SelectionHandleError("bad_scope", f"where 谓词非法: {exc}") from exc
        return {"kind": "where", "where": validated}
    if kind == "ids":
        ids = body.get("ids")
        if not isinstance(ids, list) or not ids:
            raise SelectionHandleError("bad_scope", "ids 必须是非空数组")
        if len(ids) > MAX_SCOPE_IDS:
            raise SelectionHandleError(
                "bad_scope", f"ids 上限 {MAX_SCOPE_IDS}（框选大集合请用 bbox/where scope）",
                limit=MAX_SCOPE_IDS)
        norm: List[str] = []
        for raw in ids:
            v = str(raw).strip()
            if not v or len(v) > 128:
                raise SelectionHandleError("bad_scope", "id 必须是 1-128 字符字符串")
            norm.append(v)
        return {"kind": "ids", "ids": norm}
    raise SelectionHandleError(
        "bad_scope", "scope.kind 必须是 bbox | where | ids（封闭词表）")


def scope_summary(scope: Dict[str, Any]) -> Dict[str, Any]:
    """消费面可见的 scope 摘要（无凭据/路径；where 只给 digest）。"""
    if scope["kind"] == "bbox":
        return {"kind": "bbox", "boxes": [[round(v, 6) for v in b] for b in scope["boxes"]],
                "semantics": "bbox_intersects_coarse"}
    if scope["kind"] == "where":
        return {"kind": "where", "where_digest": where_digest(scope["where"]),
                "semantics": "typed_predicate_exact"}
    return {"kind": "ids", "count": len(scope["ids"]), "semantics": "explicit_ids_exact"}


# ─── handle 存储（进程内 LRU；非持久，重启失效是设计而非缺陷） ─────────────

class _HandleEntry:
    __slots__ = ("handle", "session_id", "ref_id", "revision", "scope",
                 "count", "count_exact", "created_at", "last_used", "missing_ids")

    def __init__(self, handle: str, session_id: str, ref_id: str, revision: Optional[int],
                 scope: Dict[str, Any], count: Optional[int], count_exact: bool,
                 missing_ids: Optional[List[str]]):
        self.handle = handle
        self.session_id = session_id
        self.ref_id = ref_id
        self.revision = revision
        self.scope = scope
        self.count = count
        self.count_exact = count_exact
        self.missing_ids = missing_ids
        now = time.time()
        self.created_at = now
        self.last_used = now


class SelectionHandleStore:
    """(session_id → OrderedDict[handle → entry]) 的线程安全 LRU。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_session: Dict[str, "OrderedDict[str, _HandleEntry]"] = {}

    def _sweep_locked(self, session_id: str, now: float) -> None:
        bucket = self._by_session.get(session_id)
        if not bucket:
            return
        stale = [h for h, e in bucket.items() if now - e.last_used > SELECTION_TTL_SECONDS]
        for h in stale:
            del bucket[h]
        if not bucket:
            self._by_session.pop(session_id, None)

    def create(self, session_id: str, ref_id: str, revision: Optional[int],
               scope: Dict[str, Any], count: Optional[int], count_exact: bool,
               missing_ids: Optional[List[str]] = None) -> _HandleEntry:
        now = time.time()
        with self._lock:
            self._sweep_locked(session_id, now)
            bucket = self._by_session.setdefault(session_id, OrderedDict())
            while len(bucket) >= MAX_HANDLES_PER_SESSION:
                bucket.popitem(last=False)  # LRU 逐出最旧（配额纪律，非静默数据丢失）
            entry = _HandleEntry(
                secrets.token_urlsafe(24), session_id, ref_id, revision,
                scope, count, count_exact, missing_ids)
            bucket[entry.handle] = entry
            return entry

    def get(self, session_id: str, ref_id: str, handle: str) -> _HandleEntry:
        """消费面取 handle：session/ref/handle 三重绑定 + TTL；失败 typed。"""
        now = time.time()
        with self._lock:
            bucket = self._by_session.get(session_id)
            entry = bucket.get(handle) if bucket else None
            if entry is None:
                # 不区分「不存在」与「别人的」—— 不泄露 handle 存在性
                raise SelectionHandleError("not_found", "selection handle 不存在或已失效")
            if entry.ref_id != ref_id:
                raise SelectionHandleError("not_found", "selection handle 不属于该 ref")
            if now - entry.last_used > SELECTION_TTL_SECONDS:
                del bucket[handle]
                raise SelectionHandleError("expired", "selection handle 已过期（空闲超时）")
            entry.last_used = now
            bucket.move_to_end(handle)
            return entry

    def release(self, session_id: str, ref_id: str, handle: str) -> None:
        with self._lock:
            bucket = self._by_session.get(session_id)
            entry = bucket.get(handle) if bucket else None
            if entry is not None and entry.ref_id == ref_id:
                del bucket[handle]

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {"sessions": len(self._by_session),
                    "handles": sum(len(b) for b in self._by_session.values())}


handle_store = SelectionHandleStore()


# ─── scope 求值（粗滤相交 / 精确谓词 / 显式 id 解析） ──────────────────────

def _chain_id_of(feature: Any) -> Optional[str]:
    """与 _find_feature_by_id / _chain_stable_id 同链（properties._wfid 优先 →
    顶层 id → FEATURE_ID_KEYS）→ str；无 → None。"""
    if not isinstance(feature, dict):
        return None
    props = feature.get("properties")
    if isinstance(props, dict):
        wfid = props.get("_wfid")
        if isinstance(wfid, (str, int)) and not isinstance(wfid, bool):
            return str(wfid)
    top = feature.get("id")
    if isinstance(top, (str, int)) and not isinstance(top, bool):
        return str(top)
    if isinstance(props, dict):
        for key in FEATURE_ID_KEYS:
            v = props.get(key)
            if isinstance(v, (str, int)) and not isinstance(v, bool):
                return str(v)
    return None


def _resolve_ids(features: List[Any], ids: List[str]) -> Tuple[List[int], List[str]]:
    """显式 id 集 → 自然索引列表（FC 序）+ 未命中 id 列表。

    重复 id（数据侧碰撞）：first-wins（与 feature_id_index 同语义），
    duplicate 已由 /features 的 duplicate_id_count 披露，这里不再重复投影。
    """
    want = set(ids)
    found: Dict[str, int] = {}
    for i, f in enumerate(features):
        sid = _chain_id_of(f)
        if sid is not None and sid in want and sid not in found:
            found[sid] = i
    resolved = sorted(found.values())
    missing = [i for i in ids if i not in found]
    return resolved, missing


def scope_matches(scope: Dict[str, Any], feature: Any) -> bool:
    """单要素 scope 命中（bbox=包络粗滤相交；where=typed 谓词；ids 不走此路）。"""
    if scope["kind"] == "bbox":
        return any(_feature_intersects_bbox(feature, b) for b in scope["boxes"])
    if scope["kind"] == "where":
        return evaluate_where(scope["where"], feature)
    return False


# ─── 创建/消费（features 列表由调用方经授权通道取到后传入） ────────────────

async def create_selection(
    session_id: str, ref_id: str, revision: Optional[int],
    scope_body: Dict[str, Any], features: List[Any],
) -> _HandleEntry:
    scope = parse_scope_payload(scope_body)
    if scope["kind"] == "ids":
        resolved, missing = await asyncio.to_thread(_resolve_ids, features, scope["ids"])
        return handle_store.create(
            session_id, ref_id, revision, scope, len(resolved), True, missing)
    # bbox / where：count 用既有精确计数（≤ TOTAL_EXACT_LIMIT 可证）
    if scope["kind"] == "bbox":
        boxes = scope["boxes"]
        if len(boxes) == 1:
            count = await asyncio.to_thread(
                count_matching, features, where=None, bbox=boxes[0])
        elif len(features) <= TOTAL_EXACT_LIMIT:
            def _count_multi(fs: List[Any], bs: List[Tuple[float, float, float, float]]) -> int:
                n = 0
                for f in fs:
                    if any(_feature_intersects_bbox(f, b) for b in bs):
                        n += 1
                return n
            count = await asyncio.to_thread(_count_multi, features, boxes)
        else:
            count = None  # 超精确计数上限：诚实未知（count_exact=False）
    else:
        count = await asyncio.to_thread(
            count_matching, features, where=scope["where"], bbox=None)
    return handle_store.create(session_id, ref_id, revision, scope, count, count is not None)


def _page_generic(
    features: List[Any], scope: Dict[str, Any], *,
    limit: int, cursor: Optional[str], fields: Optional[Sequence[str]],
) -> Dict[str, Any]:
    """自然序分页（bbox 多箱 / ids 通用路径；order 不支持 → 调用方披露）。"""
    start = decode_page_cursor(cursor, None) if cursor else 0
    n = len(features)
    ids_mode = scope["kind"] == "ids"
    resolved: Optional[List[int]] = None
    total = n
    if ids_mode:
        resolved, _ = _resolve_ids(features, scope["ids"])
        total = len(resolved)
        start = min(start, total)
    if start >= total:
        return {"features": [], "next_cursor": None, "has_more": False,
                "returned": 0, "truncated": False}
    out: List[Any] = []
    scanned = 0
    i = start
    while i < total and scanned < WINDOW_SCAN_BUDGET and len(out) < limit:
        idx = resolved[i] if ids_mode else i
        f = features[idx]
        i += 1
        scanned += 1
        if not ids_mode and not scope_matches(scope, f):
            continue
        pid: Optional[int] = None
        if _chain_id_of(f) is None:
            pid = idx + 1
        out.append(project_feature(f, fields, pid))
    has_more = i < total
    return {
        "features": out,
        "next_cursor": encode_page_cursor(i, None) if has_more else None,
        "has_more": has_more,
        "returned": len(out),
        "truncated": len(out) < limit and scanned >= WINDOW_SCAN_BUDGET and has_more,
    }


async def selection_features_page(
    entry: _HandleEntry, fc: Dict[str, Any], *,
    limit: int, cursor: Optional[str], fields: Optional[Sequence[str]],
    order: Optional[Tuple[str, int]],
) -> Dict[str, Any]:
    """handle → 稳定序窗口页（委托 page_features 于单箱/where；多箱与 ids
    走自然序通用路径）。返回结构与 /features 页一致 + reason_codes 披露。"""
    features = fc.get("features") if isinstance(fc, dict) else None
    if not isinstance(features, list):
        raise SelectionHandleError("bad_request", "payload is not a FeatureCollection")
    scope = entry.scope
    reason: List[str] = []
    if scope["kind"] == "bbox":
        reason.append("bbox_coarse")
        if len(scope["boxes"]) > 1:
            reason.append("am_split_natural_order")
    elif scope["kind"] == "where":
        reason.append("filter_applied")
    else:
        reason.append("ids_resolved")

    if scope["kind"] == "ids":
        if order is not None:
            reason.append("order_unsupported_ids_scope")
        page = await asyncio.to_thread(
            _page_generic, features, scope, limit=limit, cursor=cursor, fields=fields)
        page["reason_codes"] = reason
        return page
    if scope["kind"] == "bbox" and len(scope["boxes"]) > 1:
        if order is not None:
            reason.append("order_unsupported_am_split")
        page = await asyncio.to_thread(
            _page_generic, features, scope, limit=limit, cursor=cursor, fields=fields)
        page["reason_codes"] = reason
        return page
    # 单箱 bbox / where：直接复用 page_features（order 支持、缓存一致）
    page = await asyncio.to_thread(
        page_features, fc, limit=limit, cursor=cursor,
        bbox=scope["boxes"][0] if scope["kind"] == "bbox" else None,
        where=scope.get("where"), fields=fields, order=order)
    page["reason_codes"] = reason + list(page.get("reason_codes") or [])
    return page


async def materialize_selection(
    entry: _HandleEntry, fc: Dict[str, Any], *, store_call: Callable[[Dict[str, Any]], Any],
) -> Dict[str, Any]:
    """handle → 会话数据 ref（FeatureCollection 子集）—— 供只认 selection_ref
    的既有消费者（field_compute 等）使用的可靠物化方案。

    预算：要素数 ≤ MAX_MATERIALIZE_FEATURES；超限 typed 拒绝（over_budget），
    绝不静默截断「全部选中」。bbox 语义仍是粗滤相交（coarse_bbox 披露）。
    """
    features = fc.get("features") if isinstance(fc, dict) else None
    if not isinstance(features, list):
        raise SelectionHandleError("bad_request", "payload is not a FeatureCollection")
    scope = entry.scope
    if scope["kind"] == "ids":
        resolved, _ = await asyncio.to_thread(_resolve_ids, features, scope["ids"])
        subset = [features[i] for i in resolved]
        coarse = False
    else:
        subset = await asyncio.to_thread(
            lambda: [f for f in features if scope_matches(scope, f)])
        coarse = scope["kind"] == "bbox"
    if len(subset) > MAX_MATERIALIZE_FEATURES:
        raise SelectionHandleError(
            "over_budget",
            f"selection 物化超预算：{len(subset)} > {MAX_MATERIALIZE_FEATURES}（请缩小范围或分页导出）",
            limit=MAX_MATERIALIZE_FEATURES, actual=len(subset))
    out_fc = {"type": "FeatureCollection", "features": subset}
    ref_id = await store_call(out_fc)
    return {
        "ref_id": ref_id,
        "feature_count": len(subset),
        "coarse_bbox": coarse,
        "revision": entry.revision,
    }
