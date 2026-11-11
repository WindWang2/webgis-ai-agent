"""Data Delivery Scope — 范围协商与选择完整性合同（G18 WP04）。

三种范围（任务书不变量 2）：
- ``full``：当前快照全量 —— 标签只代表快照证据（行数/truncated/sample），
  绝不冒充原数据全集；
- ``ids``：稳定 ID 显式列表（feature.id → ``_wfid`` 自动探测；缺稳定 ID
  时拒绝，不用数组下标伪造身份）；缺失 ID 逐项披露；
- ``filter``：typed predicate（``feature_pages.evaluate_where`` 语义）+
  bbox —— 在冻结快照上精确重放 → 完整选择（框选 >50 ID 且谓词可重放
  的场景走这里，而不是运输截断后的 ID 前缀）。

选择完整性闸（场景 25/26/27）：
- ``selection_matched_count > len(ids)`` 或 ``ids_truncated`` 且无 predicate
  → 阻断「完整选择」交付；仅 ``subset_ack`` 后按「所列子集」交付并把
  ``subset_of_selection`` 写入 receipt；
- 有 predicate 且与 ids 同时给出 → 以 predicate 重放为准（更完整），ids
  仅作交叉披露。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

from app.schemas.data_delivery_schema import DeliveryScope
from app.services.data_delivery import contracts as dl_contracts
from app.services.data_delivery.source_resolver import (
    SourceResolutionError,
    SourceSnapshot,
)

logger = logging.getLogger(__name__)

#: 稳定 ID 的属性后备字段（与仓库既有要素身份约定一致）。
WFID_FIELD = "_wfid"

#: feature.id（顶层）在 ScopePlan/detect 中的哨兵载体名（不是属性键）。
FEATURE_ID_FIELD = "__feature_id__"


class ScopeError(SourceResolutionError):
    """范围协商失败（error_code + reason）。"""


@dataclass
class ScopePlan:
    """预览阶段协商出的范围计划（执行阶段逐批应用）。"""

    type: str
    id_field: Optional[str] = None
    """显式稳定 ID 字段（scope.id_field；缺省自动探测 feature.id→_wfid）。"""
    requested_ids: Optional[List[str]] = None
    predicate: Optional[Dict[str, Any]] = None
    bbox: Optional[List[float]] = None
    subset_of_selection: bool = False
    note: str = ""
    blocking: List[Dict[str, str]] = field(default_factory=list)


@dataclass
class ScopeStats:
    """执行阶段实际范围证据（进 receipt）。"""

    type: str
    requested: int = 0
    delivered: int = 0
    missing_ids: List[str] = field(default_factory=list)
    subset_of_selection: bool = False
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "requested": self.requested,
            "delivered": self.delivered,
            "missing_ids": self.missing_ids[:50],
            "missing_ids_count": len(self.missing_ids),
            "subset_of_selection": self.subset_of_selection,
            "note": self.note,
        }


def extract_stable_id(feature: Dict[str, Any], id_field: Optional[str]) -> Optional[str]:
    """稳定 ID 提取：显式字段（含 FEATURE_ID_FIELD 哨兵）→ feature.id → _wfid。"""
    if id_field == FEATURE_ID_FIELD:
        fid = feature.get("id")
        return str(fid) if fid is not None else None
    if id_field:
        props = feature.get("properties") or {}
        v = props.get(id_field)
        return str(v) if v is not None else None
    fid = feature.get("id")
    if fid is not None:
        return str(fid)
    props = feature.get("properties") or {}
    v = props.get(WFID_FIELD)
    return str(v) if v is not None else None


def detect_id_field(sample: List[Dict[str, Any]]) -> Optional[str]:
    """从样本探测稳定 ID 载体（feature.id → _wfid；都没有 → None）。"""
    for f in sample[:50]:
        if f.get("id") is not None:
            return FEATURE_ID_FIELD
    for f in sample[:50]:
        if (f.get("properties") or {}).get(WFID_FIELD) is not None:
            return WFID_FIELD
    return None


def _validate_predicate(predicate: Dict[str, Any]) -> Dict[str, Any]:
    """typed 谓词校验 —— 复用 feature_pages 公共入口（JSON → 已验证 AST）。

    纪律：不写第二套自由 SQL/表达式引擎；畸形谓词一律 typed 拒绝。
    """
    import json as _json

    from app.services.feature_pages import FeaturePageError, parse_where_param

    try:
        validated = parse_where_param(_json.dumps(predicate, ensure_ascii=False))
    except FeaturePageError as e:
        raise ScopeError(dl_contracts.ERR_INVALID_SPEC, f"predicate 非法: {e}") from e
    if validated is None:
        raise ScopeError(dl_contracts.ERR_INVALID_SPEC, "predicate 非法")
    return validated


def negotiate_scope(snapshot: SourceSnapshot, scope: DeliveryScope) -> ScopePlan:
    """范围协商：校验 + 完整性闸（预览/提交共用；阻断项以 blocking 返回，
    由服务层决定是 preview 披露还是 submit 拒绝）。"""
    if scope.type == "full":
        plan = ScopePlan(type="full", note=_full_scope_note(snapshot))
        return plan

    if scope.type == "ids":
        ids = scope.ids or []
        if not ids:
            raise ScopeError(dl_contracts.ERR_INVALID_SPEC, "ids 范围需要非空 ids 列表")
        if len(ids) > dl_contracts.MAX_IDS:
            raise ScopeError(
                dl_contracts.ERR_INVALID_SPEC,
                f"ids 列表超过上限 {dl_contracts.MAX_IDS}；更大范围请用 typed predicate")
        if snapshot.is_table:
            raise ScopeError(
                dl_contracts.ERR_SELECTION_INCOMPLETE,
                "表格源没有稳定要素 ID —— 请用 filter 范围或全量交付")
        plan = ScopePlan(type="ids",
                         requested_ids=list(dict.fromkeys(ids)),
                         id_field=scope.id_field)
        # 截断证据：matched_count > 携带数，或显式 ids_truncated —— 只有
        # 同快照可精确重放的 typed predicate 才能升级为完整选择。
        truncated = bool(scope.ids_truncated) or (
            isinstance(scope.selection_matched_count, int)
            and scope.selection_matched_count > len(plan.requested_ids)
        )
        if truncated and scope.predicate:
            # 谓词重放优先：完整选择语义（场景 25）。
            plan.type = "filter"
            plan.predicate = _validate_predicate(scope.predicate)
            plan.bbox = scope.bbox
            plan.note = (
                f"选择命中 {scope.selection_matched_count or '?'} 条、运输仅 "
                f"{len(plan.requested_ids)} ID —— 已按 typed predicate 在同快照"
                "精确重放为完整选择")
            return plan
        if truncated and not scope.subset_ack:
            plan.blocking.append({
                "code": dl_contracts.ERR_SELECTION_INCOMPLETE,
                "reason": (
                    f"选择实际命中 {scope.selection_matched_count or '?'} 条，但只携带 "
                    f"{len(plan.requested_ids)} 个 ID（ids_truncated="
                    f"{bool(scope.ids_truncated)}）且无可重放谓词 —— 不能按完整选择"
                    "交付。请提供 typed predicate 重放完整范围，或显式确认仅交付所列"
                    "子集（subset_ack=true）"),
            })
            return plan
        if truncated and scope.subset_ack:
            plan.subset_of_selection = True
            plan.note = (
                "交付范围为用户确认的所列 ID 子集（原选择更大；subset_of_selection=true）")
            return plan
        plan.note = f"按 {len(plan.requested_ids)} 个稳定 ID 精确交付"
        return plan

    if scope.type == "filter":
        if not scope.predicate and not scope.bbox:
            raise ScopeError(
                dl_contracts.ERR_INVALID_SPEC, "filter 范围需要 predicate 或 bbox")
        plan = ScopePlan(
            type="filter",
            predicate=_validate_predicate(scope.predicate) if scope.predicate else None,
            bbox=scope.bbox,
            note="typed 谓词/bbox 在冻结快照上精确重放（完整选择语义）",
        )
        return plan

    raise ScopeError(dl_contracts.ERR_INVALID_SPEC, f"未知范围类型: {scope.type}")


def _full_scope_note(snapshot: SourceSnapshot) -> str:
    comp = snapshot.completeness or {}
    if comp.get("truncated"):
        return (
            f"当前快照含 {snapshot.row_count} 行（物化时被截断；total="
            f"{comp.get('total_count') or comp.get('total_matching') or '?'}）—— "
            "不代表原数据全集")
    if snapshot.kind == "session_ref" and not comp.get("is_complete", True):
        return f"当前快照含 {snapshot.row_count} 行（来源标注非完整结果）"
    if snapshot.kind == "project_artifact":
        return f"项目产物修订内容（{snapshot.row_count} 行，durable 完整内容）"
    return f"当前快照全部 {snapshot.row_count} 行"


def make_batch_filter(
    snapshot: SourceSnapshot, plan: ScopePlan
) -> Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]]:
    """执行阶段的逐批过滤器（ids 集合命中 / typed 谓词 / bbox 相交）。

    返回的闭包同时维护 ScopeStats（经 ``stats()`` 取走）。
    """
    stats = ScopeStats(type=plan.type, subset_of_selection=plan.subset_of_selection,
                       note=plan.note)
    if plan.type == "full":
        stats.delivered = snapshot.row_count
        stats.requested = snapshot.row_count

        def _all(batch: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            return batch
        _all.stats = stats  # type: ignore[attr-defined]
        return _all

    if plan.type == "ids":
        id_set: Set[str] = set(plan.requested_ids or [])
        seen: Set[str] = set()
        id_field = plan.id_field
        if not snapshot.is_table and id_field is None:
            sample = snapshot.sample_features(50)  # 有界样本（评审 P1）
            id_field = detect_id_field(sample)
            if id_field is None:
                raise ScopeError(
                    dl_contracts.ERR_SELECTION_INCOMPLETE,
                    "源数据没有稳定 ID（feature.id/_wfid 均缺）—— 无法按 ID 选择交付")

        def _ids(batch: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            out = []
            for f in batch:
                fid = extract_stable_id(f, id_field)
                if fid is not None and fid in id_set:
                    seen.add(fid)
                    out.append(f)
            return out
        _ids.stats = stats  # type: ignore[attr-defined]
        _ids.finalize = lambda: (  # type: ignore[attr-defined]
            setattr(stats, "requested", len(id_set)),
            setattr(stats, "missing_ids", sorted(id_set - seen)),
            setattr(stats, "delivered", len(seen)),
        )
        return _ids

    # filter：typed 谓词 + bbox（同快照重放）
    from app.services.feature_pages import evaluate_where

    bbox = plan.bbox

    def _filter(batch: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out = []
        for f in batch:
            if plan.predicate is not None and not evaluate_where(plan.predicate, f):
                continue
            if bbox is not None and snapshot.is_table is False:
                fb = _feature_bbox(f.get("geometry"))
                if fb is None or not _bbox_intersects(fb, bbox):
                    continue
            out.append(f)
        return out

    _filter.stats = stats  # type: ignore[attr-defined]
    return _filter


def finalize_stats(filt: Callable[..., Any], snapshot: SourceSnapshot) -> ScopeStats:
    """批过滤结束后回收统计（ids 缺失集等）。"""
    stats: Optional[ScopeStats] = getattr(filt, "stats", None)
    finalize = getattr(filt, "finalize", None)
    if finalize is not None:
        finalize()
    if stats is not None and stats.type == "filter":
        pass  # delivered 由执行体累计
    return stats  # type: ignore[return-value]


def _feature_bbox(geometry: Optional[Dict[str, Any]]) -> Optional[List[float]]:
    """GeoJSON 几何 → [minx,miny,maxx,maxy]（纯坐标遍历，无 shapely 依赖）。"""
    if not isinstance(geometry, dict):
        return None
    minx = miny = float("inf")
    maxx = maxy = float("-inf")
    found = False

    def _walk(node: Any) -> None:
        nonlocal minx, miny, maxx, maxy, found
        if isinstance(node, (int, float)):
            return
        if isinstance(node, list) and node and isinstance(node[0], (int, float)):
            for i in range(0, len(node) - 1, 2):
                x, y = node[i], node[i + 1]
                if x < minx:
                    minx = x
                if y < miny:
                    miny = y
                if x > maxx:
                    maxx = x
                if y > maxy:
                    maxy = y
                found = True
            return
        if isinstance(node, list):
            for child in node:
                _walk(child)

    _walk(geometry.get("coordinates"))
    return [minx, miny, maxx, maxy] if found else None


def _bbox_intersects(a: List[float], b: List[float]) -> bool:
    return not (a[0] > b[2] or a[2] < b[0] or a[1] > b[3] or a[3] < b[1])
