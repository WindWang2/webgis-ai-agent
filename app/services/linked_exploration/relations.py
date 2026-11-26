"""多源关系运行时（WP1/WP3）：传播拓扑、键规范化收集、键集约束投影。

职责边界：
- **不发明第二套 join 语言**：键族/变换语义直接复用 G38
  ``data_join_authoring.keys``（canonical_key_value / KeyFieldSpec），
  保证联动探索与已批准 join 计划对同一对键给出同一匹配结论。
- **不扩 feature_pages.evaluate_where**：跨源传播约束以「键集」表达
  （下游源保留 = 其键字段值 ∈ 上游匹配行的键集合），是半连接
  （semi-join）语义 —— 只过滤、不物化行乘积，many-to-many 行数膨胀
  在结构上不可能发生。
- 传播沿 plan.links 声明方向单遍 DAG 求值（contract 层已拒绝环）；
  键集超 ``MAX_PROPAGATED_KEYS`` typed 拒绝（绝不静默截断 —— 截断会让
  下游范围无声变小）。
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from app.services.data_join_authoring.keys import (
    KeyFieldSpec,
    canonical_key_value,
)
from app.services.feature_pages import evaluate_where
from app.services.linked_exploration import contract
from app.services.linked_exploration.errors import LinkedExplorationError

#: 键族标记（与 G38 canonical_key_value 一致："s"/"n"/"b"）
KeyTuple = Tuple[str, Any]


def _err(code: str, message: str, **kw: Any) -> LinkedExplorationError:
    kw.setdefault("status_code", 422)
    return LinkedExplorationError(code, message, **kw)


# ─── 传播拓扑 ────────────────────────────────────────────────────────────

def propagation_edges(plan: contract.ExplorationPlan) -> Dict[str, List[str]]:
    """传播方向边表：``{from_source: [to_source, ...]}``（仅 propagate
    非 none 的关系产生边；平行关系保留重复边 —— 拓扑序不受影响）。"""
    edges: Dict[str, List[str]] = {b.source_id: [] for b in plan.sources}
    for link in plan.links:
        if link.propagate == "left_to_right":
            edges[link.left_source_id].append(link.right_source_id)
        elif link.propagate == "right_to_left":
            edges[link.right_source_id].append(link.left_source_id)
    return edges


def propagation_order(plan: contract.ExplorationPlan) -> List[str]:
    """单遍求值顺序：上游源先于下游源（ Kahn；无环由计划校验保证，
    此处防御再查一次 —— 服务层直接消费，二次失守给 typed 错误）。"""
    edges = propagation_edges(plan)
    indegree: Dict[str, int] = {k: 0 for k in edges}
    for outs in edges.values():
        for dst in outs:
            indegree[dst] += 1
    # Kahn；同层按声明顺序出列（primary 先声明不保证，稳定即可）
    order: List[str] = []
    ready = [k for k, d in indegree.items() if d == 0]
    while ready:
        node = ready.pop(0)
        order.append(node)
        for dst in edges.get(node, ()):
            indegree[dst] -= 1
            if indegree[dst] == 0:
                ready.append(dst)
    if len(order) != len(edges):
        raise _err("LINK_CYCLE", "传播关系存在环（防御校验）")
    return order


# ─── 键规范化与收集 ──────────────────────────────────────────────────────

def key_spec_for(link: contract.LinkSpec, pair: contract.LinkKeyPair,
                 side: str) -> KeyFieldSpec:
    """键对某侧的 G38 KeyFieldSpec（transform 只作用该侧字符串键值）。"""
    field = pair.left_field if side == "left" else pair.right_field
    other = pair.right_field if side == "left" else pair.left_field
    return KeyFieldSpec(
        left_field=field, right_field=other,
        transform=pair.transform, zfill_width=pair.zfill_width)


def normalize_link_key(value: Any, spec: KeyFieldSpec) -> Optional[KeyTuple]:
    """单值 → G38 规范键（族标记 + 规范值）；null/缺失 → None。
    类型不可比（list/dict）→ typed 拒绝（不静默字符串化）。逐行容错
    场景用 ``normalize_link_key_safe``。"""
    try:
        return canonical_key_value(value, spec)
    except Exception as e:  # G38 DataJoinError → 域错误
        raise _err(
            "LINK_KEY_INVALID",
            f"关系键值类型不可比：{e}") from e


#: safe 变体的哨兵：类型不可比（非缺失；调用方按 invalid 披露）
KEY_INVALID = ("invalid", None)


def normalize_link_key_safe(value: Any, spec: KeyFieldSpec) -> Tuple[
        Optional[KeyTuple], bool]:
    """逐行容错版：返回 ``(key|None, invalid)``。
    missing/null → ``(None, False)``；类型不可比 → ``(KEY_INVALID, True)``；
    其余 → ``(key, False)``。"""
    if value is None:
        return None, False
    try:
        key = canonical_key_value(value, spec)
    except Exception:
        return KEY_INVALID, True
    if key is None:
        return None, False
    return key, False


@dataclass
class PropagatedKeySet:
    """上游匹配行的键集合（规范化后）；附诚实统计。"""

    field: str
    keys: Set[KeyTuple] = dc_field(default_factory=set)
    scanned: int = 0
    matched: int = 0
    skipped_missing: int = 0
    skipped_invalid: int = 0
    #: skipped_invalid 的原始样本（≤8 个，typed 披露；不给全量）
    invalid_samples: List[Any] = dc_field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.keys


def collect_propagated_keys(
    features: Sequence[Dict[str, Any]],
    where: Optional[Dict[str, Any]],
    *,
    field: str,
    spec: KeyFieldSpec,
) -> PropagatedKeySet:
    """上游源匹配行的键集合收集（纯函数；不做帽检查 —— 帽在
    ``require_key_set_within_cap`` 由调用方按场景施加，收集器本身是
    无损的，统计也如实返回）。"""
    out = PropagatedKeySet(field=field)
    for feat in features:
        props = feat.get("properties") if isinstance(feat, dict) else None
        value = props.get(field) if isinstance(props, dict) else None
        out.scanned += 1
        if where is not None and not evaluate_where(where, feat):
            continue
        out.matched += 1
        key, invalid = normalize_link_key_safe(value, spec)
        if invalid:
            out.skipped_invalid += 1
            if len(out.invalid_samples) < 8:
                out.invalid_samples.append(value)
            continue
        if key is None:
            out.skipped_missing += 1
            continue
        out.keys.add(key)
    return out


def require_key_set_within_cap(keyset: PropagatedKeySet) -> None:
    """键集超帽 typed 拒绝（不截断 —— 截断会让下游范围无声变小）。"""
    if len(keyset.keys) > contract.MAX_PROPAGATED_KEYS:
        raise _err(
            "PROPAGATION_KEYSET_OVER_LIMIT",
            f"传播键集 {len(keyset.keys)} 个超过上限 "
            f"{contract.MAX_PROPAGATED_KEYS}：请收窄上游选择或改用"
            "反向传播（键少的侧作上游）",
            field=keyset.field,
            correction_hint="缩小上游条件后再预览，或把传播方向反转。",
            detail={"key_count": len(keyset.keys),
                    "cap": contract.MAX_PROPAGATED_KEYS})


# ─── 键集约束：内存谓词 + MapLibre 投影 + digest ─────────────────────────

def key_set_predicate(field: str, keys: Set[KeyTuple],
                      spec: KeyFieldSpec) -> Callable[[Dict[str, Any]], bool]:
    """下游源行谓词：键字段值 ∈ 传播键集（G38 规范化语义，与收集侧
    同族可比）。键集为空 = 无任何传播约束时调用方不应构造谓词；空集
    语义由调用方声明（上游零匹配 → 下游约束为「假」）。逐行容错：
    脏键值不命中（统计由收集侧/预览披露承担）。"""
    def _pred(feature: Dict[str, Any]) -> bool:
        props = feature.get("properties") if isinstance(feature, dict) else None
        value = props.get(field) if isinstance(props, dict) else None
        if value is None:
            return False
        key, invalid = normalize_link_key_safe(value, spec)
        if invalid or key is None:
            return False
        return key in keys
    return _pred


def _ml_scalar(v: Any) -> Any:
    if isinstance(v, float) and v.is_integer() and abs(v) < 2**53:
        return int(v)
    return v


def key_set_to_maplibre(field: str, keys: Set[KeyTuple]) -> List[Any]:
    """键集 → MapLibre ``in`` 表达式。族语义与 evaluate_where/_num_eq 的
    typed 纪律一致：字符串族原样；数值族统一数值字面量（1 与 1.0 归一
    int，与 contract._ml_scalar 同规则）；bool 族字面量。MapLibre ``in``
    严格相等：1/"1"/true 互不混同 —— 跨族键集（同时含 "1" 与 1）按
    typed 各自保留，这正是语义（不归一）。字面量排序按族 s<n<b、族内
    按值（确定性序列化；跨进程/前后端一致）。"""
    family_rank = {"s": 0, "n": 1, "b": 2}
    literals: List[Any] = []
    for family, value in sorted(
            keys, key=lambda kv: (family_rank.get(kv[0], 9), str(kv[1]))):
        if family == "s":
            literals.append(value)
        elif family == "n":
            literals.append(_ml_scalar(value))
        else:  # "b"
            literals.append(value)
    return ["in", ["get", field], ["literal", literals]]


def key_set_digest_fragment(keyset: PropagatedKeySet) -> Dict[str, Any]:
    """入 digest/披露的键集规范片段（排序后可序列化；不含原始行）。"""
    return {
        "field": keyset.field,
        "keys": sorted(
            ([f, v if isinstance(v, (int, float, bool, str)) else str(v)]
             for f, v in keyset.keys), key=lambda kv: (kv[0], str(kv[1]))),
        "scanned": keyset.scanned,
        "matched": keyset.matched,
        "skipped_missing": keyset.skipped_missing,
        "skipped_invalid": keyset.skipped_invalid,
    }


__all__ = [
    "KeyTuple", "PropagatedKeySet", "KEY_INVALID",
    "propagation_edges", "propagation_order",
    "key_spec_for", "normalize_link_key", "normalize_link_key_safe",
    "collect_propagated_keys", "require_key_set_within_cap",
    "key_set_predicate", "key_set_to_maplibre", "key_set_digest_fragment",
]
