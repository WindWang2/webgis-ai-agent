"""MapSpec source rebinding transaction（ADR-0220 §4）。

把冻结的 MapSpec 文档重绑定到新 session 的 ref 集合：

- **指纹先行**：嵌入文档的 canonical 指纹必须与冻结指纹一致（integrity
  before anything）；
- **语义不漂移守卫（双层）**：
  1. 路径感知遮蔽比对 —— 已知 ref 键位上的字符串值全部遮蔽后，两文档
     canonical 相等 ⇔ ref 键位之外的任何内容都没有变；
  2. 逐键位许可断言 —— 每个 ref 键位的新值只允许三种：原值（binary 磁盘
     lane 同 id 复位）、``ref_map[旧值]``（JSON lane 新 id）、空串（旧值
     不可恢复，诚实置空）。其它任何变化（含 ``"" → ref:…`` 无中生有）
     中止整个事务；
- **不做静默替换**：无法恢复的 ref 置空 + dangling 披露
  （``resume_from_anchor._rewrite_refs`` 同款先例），绝不指向不同内容；
- **原子提交**：经 store 层 rollback 同款路径（``save_mapspec`` 携带
  mutation_revision + layer_op replace 单事务落地），绝不裸两步写。
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Tuple

from app.services.mapspec_source import (
    is_data_fabric_entry,
    is_raster_entry,
    ref as source_ref,
)

logger = logging.getLogger(__name__)

#: source 条目中可能承载 ref 值的键（shape 真相归 mapspec_source；此处只
#: 做「值等于旧 ref 才替换」的保守写回 —— 不发明新键，不猜测 shape）。
_GEOJSON_REF_KEYS = ("ref_id", "ref", "url", "dataPath")
_FABRIC_REF_KEYS = ("ref_id", "url", "dataPath")
_RASTER_REF_KEYS = ("imageRef",)
#: layout 组件 options 的绑定键（workspace snapshot _extract_layer_refs 同面）。
_COMPONENT_REF_KEYS = ("chartRef", "tableRef")

_ALL_REF_KEYS = frozenset(
    _GEOJSON_REF_KEYS + _FABRIC_REF_KEYS + _RASTER_REF_KEYS + _COMPONENT_REF_KEYS
)

#: 遮蔽比对用的占位符（任何合法 ref 值都不会等于它）。
_MASK_PLACEHOLDER = "\x00masked-ref\x00"


def canonical_fingerprint(mapspec: Dict[str, Any]) -> str:
    """spec 文档的 canonical-JSON SHA256（与 store._fingerprint_sync 同式，
    独立实现以免依赖私有符号；freeze/validate/rebind 三处共用此单点）。"""
    blob = json.dumps(mapspec, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _entry_ref_keys(entry: Dict[str, Any]) -> Tuple[str, ...]:
    if is_raster_entry(entry):
        return _RASTER_REF_KEYS
    if is_data_fabric_entry(entry):
        return _FABRIC_REF_KEYS
    return _GEOJSON_REF_KEYS


def _component_entries(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    layout = spec.get("layout")
    if not isinstance(layout, dict):
        return []
    components = layout.get("components")
    if isinstance(components, list):
        return [c for c in components if isinstance(c, dict)]
    if isinstance(components, dict):
        return [v for v in components.values() if isinstance(v, dict)]
    return []


def _source_entries(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    sources = spec.get("sources")
    if isinstance(sources, dict):
        return [v for v in sources.values() if isinstance(v, dict)]
    if isinstance(sources, list):
        return [v for v in sources if isinstance(v, dict)]
    return []


def _iter_ref_positions(spec: Dict[str, Any]) -> Iterator[Tuple[Dict[str, Any], str]]:
    """产出全部 ref 键位 ``(容器, 键)``：source 条目 + layout 组件 options。"""
    for entry in _source_entries(spec):
        for key in _entry_ref_keys(entry):
            if isinstance(entry.get(key), str):
                yield entry, key
    for comp in _component_entries(spec):
        opts = comp.get("options")
        if isinstance(opts, dict):
            for key in _COMPONENT_REF_KEYS:
                if isinstance(opts.get(key), str):
                    yield opts, key


def _mask_spec(spec: Dict[str, Any]) -> Dict[str, Any]:
    """深拷贝并把**已知 ref 键位**上的字符串值替换为占位符（路径感知遮蔽）。"""
    masked = json.loads(json.dumps(spec, ensure_ascii=False))
    for container, key in _iter_ref_positions(masked):
        container[key] = _MASK_PLACEHOLDER
    return masked


def semantics_preserved(frozen: Dict[str, Any], rebound: Dict[str, Any]) -> bool:
    """ref 键位遮蔽后 canonical 相等 ⇔ ref 键位之外无任何漂移。"""
    try:
        a = json.dumps(_mask_spec(frozen), ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"))
        b = json.dumps(_mask_spec(rebound), ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"))
        return a == b
    except (TypeError, ValueError):
        return False


def _sanctioned_ref_changes(
    frozen: Dict[str, Any], rebound: Dict[str, Any], ref_map: Dict[str, str]
) -> bool:
    """逐键位断言：新值 ∈ {原值, ref_map[旧值], ""（旧值不可恢复）}。

    键位集合以冻结文档为准 —— 重绑定绝不增删 ref 键位（增删即漂移，已被
    遮蔽比对兜住；此处再按值断言，双层守卫）。
    """
    frozen_positions = list(_iter_ref_positions(frozen))
    rebound_positions = list(_iter_ref_positions(rebound))
    if len(frozen_positions) != len(rebound_positions):
        return False
    for (frozen_container, frozen_key), (rebound_container, rebound_key) in zip(
        frozen_positions, rebound_positions
    ):
        if frozen_key != rebound_key:
            return False
        old_value = frozen_container[frozen_key]
        new_value = rebound_container[rebound_key]
        if old_value == new_value:
            continue
        if old_value in ref_map and new_value == ref_map[old_value]:
            continue
        if old_value not in ref_map and new_value == "":
            continue
        return False
    return True


def rewrite_spec_refs(
    mapspec: Dict[str, Any],
    ref_map: Dict[str, str],
    missing: List[str],
) -> Dict[str, Any]:
    """纯函数：返回重绑定后的 spec 副本（sources + layout 组件绑定面）。

    无法恢复的 ref（不在 ref_map）→ 对应键置空并记入 missing（诚实披露，
    绝不悬空谎报绑定）。``missing`` 由调用方传入并就地追加。
    """
    out: Dict[str, Any] = json.loads(json.dumps(mapspec, ensure_ascii=False))

    def _apply(entry: Dict[str, Any], keys: Tuple[str, ...]) -> None:
        for key in keys:
            value = entry.get(key)
            if not (isinstance(value, str) and value.startswith("ref:")):
                continue
            new_ref = ref_map.get(value)
            if new_ref:
                if new_ref != value:
                    entry[key] = new_ref
            else:
                entry[key] = ""
                if value not in missing:
                    missing.append(value)

    for entry in _source_entries(out):
        _apply(entry, _entry_ref_keys(entry))
    for comp in _component_entries(out):
        opts = comp.get("options")
        if isinstance(opts, dict):
            _apply(opts, _COMPONENT_REF_KEYS)
    return out


@dataclass
class RebindResult:
    """重绑定事务结果（receipt 面）。"""

    committed: bool = False
    rebound_fingerprint: str = ""
    frozen_fingerprint: str = ""
    rebound_refs: List[str] = field(default_factory=list)
    dangling_refs: List[str] = field(default_factory=list)
    error: str = ""


async def rebind_mapspec_into_session(
    session_id: str,
    frozen_mapspec: Dict[str, Any],
    frozen_fingerprint: str,
    ref_map: Dict[str, str],
) -> RebindResult:
    """把冻结 spec 重绑定到新 session 并原子提交（store 层 rollback 同款路径）。

    事务序：integrity 校验 → 纯函数改写 → 双层语义守卫 → 单事务提交
    （spec + revision + runtime layers replace 同 MULTI）。任何一步失败都不
    写入（新 session 保持无 spec 状态 —— 恢复编排层决定降级披露）。
    """
    from app.services.mapspec.store import MapSpecStore
    from app.services.session_data import session_data_manager

    result = RebindResult(frozen_fingerprint=frozen_fingerprint)
    if not isinstance(frozen_mapspec, dict) or not frozen_mapspec:
        result.error = "frozen mapspec doc empty"
        return result
    if canonical_fingerprint(frozen_mapspec) != frozen_fingerprint:
        result.error = "fingerprint_mismatch"
        return result

    missing: List[str] = []
    rebound = rewrite_spec_refs(frozen_mapspec, ref_map, missing)
    if not semantics_preserved(frozen_mapspec, rebound):
        result.error = "semantics_drift"
        return result
    if not _sanctioned_ref_changes(frozen_mapspec, rebound, ref_map):
        result.error = "unsanctioned_ref_change"
        return result

    result.rebound_fingerprint = canonical_fingerprint(rebound)
    result.rebound_refs = sorted(v for v in ref_map.values() if v)[:64]
    result.dangling_refs = sorted(set(missing))[:64]

    store = MapSpecStore()
    # 新 session 首 spec：revision 从 1 起（新会话新世代；冻结 revision 仅作
    # provenance 记录，不跨 session 延续 CAS 令牌 —— 恢复不是 mutation
    # 重放）。layer_op replace 与 engine rollback 提交面同款：spec/layers/
    # revision 单事务原子落地。
    try:
        save_res = await store.save_mapspec(
            session_id,
            rebound,
            mutation_revision=1,
            layer_op=("replace", "", list(rebound.get("layers", []) or [])),
        )
    except Exception as e:  # noqa: BLE001 — 提交失败 = 事务失败（不写半截）
        result.error = f"commit_failed: {e}"
        return result
    revision_persisted = bool(
        save_res.get("revision_persisted")
    ) if isinstance(save_res, dict) else False
    if not revision_persisted:
        persisted = await session_data_manager.set_map_state(
            session_id, "_cartographic_mutation_revision", 1
        )
        if persisted is False:
            result.error = "revision persistence rejected"
            return result
    result.committed = True
    return result


def extract_spec_refs(mapspec: Dict[str, Any]) -> List[Tuple[str, str, str]]:
    """从 spec 提取 ``(source_id, kind, ref)`` 三元组（sources + 组件面）。

    与 workspace snapshot 的 ``_extract_layer_refs`` 同面（含 chart/table
    绑定 —— GC 活引用盲区修复的同款契约）。inline/外部 URL 源 ref 为空串
    （分类面归 promotion planner）。
    """
    out: List[Tuple[str, str, str]] = []
    if not isinstance(mapspec, dict):
        return out
    sources = mapspec.get("sources")
    if isinstance(sources, dict):
        source_defs = [
            {**(v if isinstance(v, dict) else {}), "id": k}
            for k, v in sources.items()
        ]
    else:
        source_defs = [s for s in (sources or []) if isinstance(s, dict)]
    for src in source_defs:
        ref_val = source_ref(src)
        out.append((
            str(src.get("id") or src.get("layer_id") or "")[:96],
            "source",
            str(ref_val)[:160] if isinstance(ref_val, str) else "",
        ))
    for comp in _component_entries(mapspec):
        opts = comp.get("options")
        if not isinstance(opts, dict):
            continue
        for key, kind in (("chartRef", "chart"), ("tableRef", "table")):
            value = opts.get(key)
            if isinstance(value, str) and value.startswith("ref:"):
                out.append((
                    str(comp.get("id") or comp.get("type") or "")[:96],
                    kind,
                    value[:160],
                ))
    return out[:64]
