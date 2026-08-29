"""JSON 字节量估算器（E-3 / #894 分层收口：私有成员公有化）。

`_estimate_json_bytes` / `_ESTIMATE_*` / `_arg_size_hint_var` 原先定义在
app/tools/registry.py 的私有命名空间，被 app/lib/tool_cache.py 跨模块消费
——私有成员被外部消费即成事实公共契约，registry 内部重构会无告警破坏
cache 的 oversized 门。上移到 lib（叶子层），registry 与 tool_cache 共同
依赖（依赖方向 tools→lib 正确）。实现原样搬移，公有命名不带下划线。
"""
from __future__ import annotations

import contextvars
from typing import Any

ESTIMATE_MAX_NODES = 20_000
ESTIMATE_SIZE_LIMIT = 262_144  # 256 KB — the cache/validation gate threshold

# ContextVar to share the single args-size probe between registry and the
# cached_tool wrapper (tool_cache.make_cache_key), so the same large args
# dict is not walked 2-3 times per dispatch. Set in dispatch(), read in
# make_cache_key(); fallback to a fresh walk when not set.
arg_size_hint_var: contextvars.ContextVar[tuple[int, bool] | None] = (
    contextvars.ContextVar("arg_size_hint", default=None)
)


def estimate_json_bytes(
    obj: Any, _depth: int = 0, _budget: list[int] | None = None
) -> int:
    """Cheap structural estimate of the JSON byte length of ``obj``.

    PERF-01: ``json.dumps`` of a large tool result (e.g. a 10k-feature
    GeoJSON FeatureCollection) just to record a byte metric duplicates the
    serialization the dispatch path already performs. This walker estimates
    the serialized size without materializing the full string — accurate to
    within a few percent for typical JSON and bounded by ``_depth`` to avoid
    pathological cycles. Used only for metrics; never for correctness.

    #677: additionally bounded by a total node budget (default
    ``ESTIMATE_MAX_NODES``). When the budget is exhausted the walker stops
    visiting new nodes and extrapolates from the sampled average, so a
    100k-feature payload costs O(budget) rather than O(features). If the
    caller passes an explicit ``_budget`` list, ``_budget[0] <= 0`` after
    the call indicates the result is an approximation (budget hit).
    """
    if _budget is None:
        _budget = [ESTIMATE_MAX_NODES]
    if _budget[0] <= 0:
        return 0
    _budget[0] -= 1
    if _depth > 12:
        return 64  # deep nested: stop walking, small placeholder
    if obj is None:
        return 4
    if isinstance(obj, bool):
        return 4 if obj else 5
    if isinstance(obj, (int, float)):
        return len(str(obj))
    if isinstance(obj, str):
        # +2 for the quotes; escape overhead is minor for typical strings.
        return len(obj) + 2
    if isinstance(obj, dict):
        # {"k":v,...} → 2 braces + per-entry overhead (4: `","` and `:`).
        total = 2
        first = True
        items = list(obj.items())
        sampled = 0
        for k, v in items:
            if _budget[0] <= 0:
                remaining = len(items) - sampled
                if sampled > 0:
                    avg = (total - 2) / sampled
                    total += int(avg * remaining)
                else:
                    total += remaining * 16
                break
            if not first:
                total += 1  # comma
            first = False
            total += len(str(k)) + 4 + estimate_json_bytes(v, _depth + 1, _budget)
            sampled += 1
        return total
    if isinstance(obj, (list, tuple)):
        total = 2
        first = True
        n = len(obj)
        if n == 0:
            return total
        sampled = 0
        # For large lists (features), sample until budget exhausted then
        # extrapolate via average per-item cost.
        for item in obj:
            if _budget[0] <= 0:
                remaining = n - sampled
                if sampled > 0:
                    # average cost per sampled item (including comma)
                    avg = (total - 2) / sampled if sampled else 8
                    total += int(avg * remaining) + remaining  # commas for remainder
                else:
                    total += remaining * 8
                break
            if not first:
                total += 1
            first = False
            total += estimate_json_bytes(item, _depth + 1, _budget)
            sampled += 1
        return total
    # Fallback: stringify (rare; non-JSON-native types default-str in dumps).
    try:
        return len(str(obj))
    except Exception:
        return 32


# audit #824: 别名批量查表的去重后字段上限 —— 超限（或 oversized 载荷）降级为
# 仅解析显式 ref: 前缀，避免把内联大 GeoJSON 的海量字符串叶塞进一条 HMGET。
