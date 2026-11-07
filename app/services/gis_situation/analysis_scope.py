"""AnalysisScope —— selection → 受控分析输入（ADR-0285 D1）。

任务书 E1「地图情境作为受控 Agent 输入」的执行面：把用户当前的地图选择
（IR ledger 投影 + pre-turn 快照 + canvas affordance 环）转成**经验证的**
分析 scope，供 agent 显式传参给真实分析工具。

两段分离（红线：前端上报不是可信事实本身，服务端验证 ref 所有权与 revision）：

- :func:`derive_claimed_scope` —— **纯函数**（零 IO）：合并 IR 投影 selection、
  pre-turn selected_feature、canvas affordance 环的最新几何动作，产出
  ClaimedScope（声称面，编译期可进 situation fact）；
- :func:`validate_analysis_scope` —— **IO 壳**（session store 只读）：五链验证
  （ref 所有权 → 宿主层在架 → revision 一致 → 要素存活 → 有界披露），全
  reason 封闭词表，失败不猜默认。

纪律：本模块不写任何权威状态；不向 ToolDispatchService 注入隐式 scope；
绝不内联 payload 进返回值（ids/摘要有界）。
"""
from __future__ import annotations

import logging
import math
from typing import Any, List, Mapping, Optional
from typing_extensions import TypedDict


class ClaimedScope(TypedDict, total=False):
    kind: str
    layer_id: str | None
    dataset_ref: str | None
    feature_ids: list[str] | None
    claimed_at_seq: int | None
    sources: list[str]
    id_field: str
    bbox: list[float]
    geometry: Mapping[str, object]
    # SW-02（#2004）additive：范围披露面 —— matched_count=真实命中数
    # （UI 同源），ids_truncated=True 表示 feature_ids 是有界样本而非全量。
    matched_count: int
    ids_truncated: bool


class ValidatedScope(TypedDict, total=False):
    kind: str
    status: str
    reason: str
    dataset_ref: str | None
    layer_id: str | None
    feature_ids: list[str] | None
    missing_ids: list[str]
    content_revision: int | None
    crs: str | None
    bbox: list[float] | None
    geometry: Mapping[str, object]
    checks: dict[str, str]
    # SW-02：透传披露面（truncated_sample 进 checks.features —— 消费方
    # 必须能对用户诚实说"分析的是前 N 个/共 M 个"）。
    matched_count: int
    ids_truncated: bool
    # 复审 P1-2：框选 bbox 谓词（用户框选范围）独立键 —— 与 bbox（数据集
    # 描述符范围）分义；空间工具 extent 兜底必须用本键，不得把数据集
    # 全量范围当框选范围（#2004 族"范围静默改变"的放大方向重现）。
    selection_bbox: list[float]


def record_fields(value: object) -> Mapping[str, object]:
    """Read an untrusted record only after checking its string-key boundary."""
    if isinstance(value, Mapping) and all(isinstance(key, str) for key in value):
        return value
    return {}

logger = logging.getLogger(__name__)

__all__ = [
    "derive_claimed_scope",
    "validate_analysis_scope",
    "MAX_LIVENESS_FEATURES",
    "MAX_SCOPE_FEATURE_IDS",
    "SCOPE_REASONS",
]

# ── 封闭 reason 词表（服务端验证结论；新增须同步 ADR-0285）──────────────

SCOPE_REASON_OK = "ok"
SCOPE_REASON_NO_SELECTION = "no_selection"
SCOPE_REASON_NO_DATASET_REF = "no_dataset_ref"
SCOPE_REASON_REF_UNRESOLVABLE = "ref_unresolvable"
SCOPE_REASON_LAYER_REMOVED = "layer_removed"
SCOPE_REASON_REVISION_DRIFTED = "revision_drifted"
SCOPE_REASON_FEATURES_MISSING = "features_missing"
SCOPE_REASON_LAYER_UNAVAILABLE = "layer_unavailable"
SCOPE_REASON_REF_MISMATCH = "source_ref_mismatch"
SCOPE_REASONS = frozenset({
    SCOPE_REASON_LAYER_UNAVAILABLE, SCOPE_REASON_REF_MISMATCH,
    SCOPE_REASON_OK, SCOPE_REASON_NO_SELECTION, SCOPE_REASON_NO_DATASET_REF,
    SCOPE_REASON_REF_UNRESOLVABLE, SCOPE_REASON_LAYER_REMOVED,
    SCOPE_REASON_REVISION_DRIFTED, SCOPE_REASON_FEATURES_MISSING,
})

#: 声称面 feature_ids 有界（与 selection-store MAX_SELECTED_IDS 对齐）。
MAX_SCOPE_FEATURE_IDS = 50
#: 要素存活逐点核对的数据集规模上限（超过即跳过并披露——不做全量扫描）。
MAX_LIVENESS_FEATURES = 20_000

_STATUS_USABLE = "usable"
_STATUS_STALE = "stale"
_STATUS_UNUSABLE = "unusable"


def _clip_ids(raw: Any) -> List[str]:
    if not isinstance(raw, list):
        return []
    return [str(v)[:128] for v in raw[:MAX_SCOPE_FEATURE_IDS] if isinstance(v, (str, int))]


def _bbox4(value: object) -> Optional[List[float]]:
    if not isinstance(value, list) or len(value) != 4:
        return None
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in value):
        return None
    try:
        coordinates = [float(v) for v in value]
    except (ValueError, OverflowError):
        return None
    return coordinates if all(math.isfinite(v) for v in coordinates) else None


def derive_claimed_scope(
    interaction_snapshot: Optional[Mapping[str, object]],
    pre_turn_selected: Optional[Mapping[str, object]],
    *,
    affordance_ring: Optional[List[Any]] = None,
) -> ClaimedScope:
    """声称面（纯函数，零 IO）：合并三源 → ClaimedScope dict。

    优先级：IR ledger 投影 selection（server_seq 驱动、有 dataset_ref 语义）
    > pre-turn 前端快照（`_cartographic_context_observation.selected_feature`）
    > canvas affordance 环最新几何动作（AOI 面）。无任何证据 → kind="none"。
    """
    snapshot = record_fields(interaction_snapshot)
    selection = snapshot.get("selection")
    selection = selection if isinstance(selection, dict) else None
    pre_turn = pre_turn_selected if isinstance(pre_turn_selected, Mapping) else None

    # An empty ledger has never established a current selection. Legacy
    # pre-turn selection is still a fallback there; a populated projection
    # with selection=None is an explicit current empty/replaced selection.
    has_current_selection = "selection" in snapshot and snapshot.get("event_count") != 0
    source = selection if has_current_selection else pre_turn
    sources: List[str] = ["ir_ledger"] if selection is not None else (
        ["pre_turn"] if pre_turn is not None else []
    )
    if source is not None:
        multi = _clip_ids(source.get("selected_ids"))
        single = source.get("feature_id")
        single = str(single)[:128] if isinstance(single, (str, int)) else None
        feature_ids = multi or ([single] if single else [])
        claimed_seq = None
        if selection is not None:
            src = selection.get("source")
            if isinstance(src, dict) and isinstance(src.get("server_seq"), int):
                claimed_seq = src["server_seq"]
        id_field = source.get("id_field")
        claimed: ClaimedScope = {
            "kind": "feature_ids" if multi else ("feature_single" if single else "none"),
            "layer_id": (str(source.get("layer_id") or "")[:200] or None),
            "dataset_ref": (str(source.get("dataset_ref") or "")[:200] or None),
            "feature_ids": feature_ids or None,
            "claimed_at_seq": claimed_seq,
            "sources": sources,
            **({"id_field": id_field[:64]} if isinstance(id_field, str) else {}),
            **_scope_disclosure(source, feature_ids),
        }
        # SW-02：框选 bbox 谓词随选择上行（"这些学校/刚才框选的"作为空间
        # 范围可解析——空间工具可用 extent 兜底而不必从 ids 反推）。
        selection_bbox = _bbox4(source.get("bbox"))
        if selection_bbox is not None:
            claimed["bbox"] = selection_bbox
        if claimed["kind"] != "none":
            return claimed

    # AOI 面：canvas affordance 环最新带几何动作（框选/套索——几何本体在环内
    # 有界保留，见 canvas_affordance._sanitize_geometry）。
    recent = snapshot.get("recent_interactions")
    last_aoi = snapshot.get("last_aoi_kind")
    if not last_aoi and isinstance(recent, list):
        last_aoi = next((
            event.get("kind") for event in recent if isinstance(event, dict)
            and event.get("kind") in ("aoi_drawn", "aoi_edited", "aoi_deleted")
        ), None)
    if last_aoi == "aoi_deleted":
        affordance_ring = []  # Explicit current deletion cannot resurrect old geometry.
    for entry in reversed(affordance_ring or []):
        if not isinstance(entry, dict):
            continue
        if entry.get("kind") not in ("box_select", "freehand_lasso", "polygon_lasso"):
            continue
        geometry = entry.get("geometry")
        bbox = _bbox4(entry.get("bbox"))
        sequence = entry.get("sequence", entry.get("seq"))
        if isinstance(geometry, dict) and isinstance(bbox, list) and len(bbox) == 4:
            return {
                "kind": "aoi",
                "layer_id": None,
                "dataset_ref": None,
                "feature_ids": None,
                "bbox": [float(v) for v in bbox],
                "geometry": geometry,
                "claimed_at_seq": sequence if isinstance(sequence, int) else None,
                "sources": ["canvas_affordance"],
            }
    return {
        "kind": "none", "layer_id": None, "dataset_ref": None,
        "feature_ids": None, "claimed_at_seq": None, "sources": [],
    }


def _scope_disclosure(
    source: Mapping[str, object], feature_ids: List[str]
) -> ClaimedScope:
    """SW-02（#2004）：从选择投影提取范围披露面。

    - ``matched_count``：真实命中数（IR 摄入白名单键；有界整数才透传）；
    - ``ids_truncated``：摄入层服务端派生的样本截断标记；
    - 兜底：披露缺席但 ids 已知时 matched_count=len(ids)（声称面自洽，
      不虚构更早的命中数 —— 披露缺席说明事件链上从未有该事实）。

    复审 P2-2：本函数也是 pre-turn 快照路径（不经 IR 摄入的
    ``_cartographic_context_observation``）的唯一披露入口 —— 上界
    ≤ :data:`MAX_MATCHED_COUNT` 与 IR 摄入同纪律（在进入 ClaimedScope
    前拒收巨数，丢键不猜）。
    """
    from app.services.gis_harness.interaction_ir.contracts import MAX_MATCHED_COUNT

    disclosure: ClaimedScope = {}
    matched = source.get("matched_count")
    if (
        isinstance(matched, int) and not isinstance(matched, bool)
        and 0 <= matched <= MAX_MATCHED_COUNT
    ):
        disclosure["matched_count"] = matched
    elif feature_ids:
        disclosure["matched_count"] = len(feature_ids)
    truncated = source.get("ids_truncated")
    if isinstance(truncated, bool):
        disclosure["ids_truncated"] = truncated
    return disclosure


def _spec_layer(spec: Mapping[str, object], layer_id: Optional[str]) -> Optional[Mapping[str, object]]:
    if not layer_id or not isinstance(spec.get("layers"), list):
        return None
    layers = spec.get("layers")
    if not isinstance(layers, list):
        return None
    for layer in layers:
        if isinstance(layer, dict) and str(layer.get("id")) == layer_id:
            return layer
    return None


def _feature_ids_in_payload(payload: Any, requested: List[str]) -> List[str]:
    """存活核对：payload（``{"geojson": FC}`` 信封或裸 FC）内按 FEATURE_ID_KEYS
    链解析每条要素 id（与 feature_edit 同链，不复制词表）。"""
    from app.services.feature_edit.identity import resolve_feature_id

    if isinstance(payload, dict) and isinstance(payload.get("geojson"), dict):
        payload = payload["geojson"]
    features = payload.get("features") if isinstance(payload, dict) else None
    if not isinstance(features, list):
        return []
    wanted = set(requested)
    present: List[str] = []
    for feature in features:
        if not isinstance(feature, dict):
            continue
        fid = resolve_feature_id(feature)  # type: ignore[arg-type]
        if fid and fid in wanted and fid not in present:
            present.append(fid)
            if len(present) == len(wanted):
                break
    return present


async def validate_analysis_scope(
    session_id: str,
    claimed: ClaimedScope,
    *,
    store: Any = None,
    mapspec_store: Any = None,
) -> ValidatedScope:
    """验证面（IO 壳，只读）：ClaimedScope → ValidatedScope。

    reason 封闭词表；``status``: usable / stale / unusable。stale 语义＝可
    继续但必须向 agent/用户披露（部分要素缺失/数据版本漂移）；unusable＝
    不可作为分析输入。任何读失败 → unusable + 对应 reason（fail-closed）。
    """
    if store is None:
        from app.services.session_data import session_data_manager as store
    if mapspec_store is None:
        from app.services.mapspec.store import mapspec_store_instance as mapspec_store

    kind = str(claimed.get("kind") or "none")
    result: ValidatedScope = {
        "kind": kind,
        "status": _STATUS_UNUSABLE,
        "reason": SCOPE_REASON_NO_SELECTION,
        "dataset_ref": None,
        "layer_id": claimed.get("layer_id"),
        "feature_ids": None,
        "missing_ids": [],
        "content_revision": None,
        "crs": None,
        "bbox": None,
        "checks": {},
        **({"matched_count": claimed["matched_count"]} if isinstance(claimed.get("matched_count"), int) else {}),
        **({"ids_truncated": claimed["ids_truncated"]} if isinstance(claimed.get("ids_truncated"), bool) else {}),
        # 复审 P1-2：框选范围独立透传（bbox 键保留数据集描述符语义）。
        **({"selection_bbox": list(claimed["bbox"])} if _bbox4(claimed.get("bbox")) is not None else {}),
    }
    if kind == "none":
        return result

    if kind == "aoi":
        # AOI 面几何来自 canvas affordance 环（服务端摄入时已 sanitize）；
        # 无 dataset 所有权/存活问题，只回有界几何供分析参数使用。
        geometry = claimed.get("geometry")
        bbox = _bbox4(claimed.get("bbox"))
        if not (isinstance(geometry, dict) and isinstance(bbox, list) and len(bbox) == 4):
            result["reason"] = SCOPE_REASON_NO_SELECTION
            return result
        result.update({
            "status": _STATUS_USABLE,
            "reason": SCOPE_REASON_OK,
            "bbox": [float(v) for v in bbox],
            "geometry": geometry,
            "checks": {
                "ownership": "not_applicable",
                "layer": "not_applicable",
                "revision": "not_applicable",
                "features": "not_applicable",
            },
        })
        return result

    # ── feature_ids / feature_single：五链验证 ──────────────────────────
    # Bind the claimed input to one authoritative desired layer/source snapshot.
    try:
        spec = await mapspec_store.get_mapspec(session_id)
    except Exception:  # noqa: BLE001 — unknown source is not proof of a live host
        spec = None
    layer_id = claimed.get("layer_id")
    if not isinstance(spec, dict) or not isinstance(spec.get("layers"), list) or not layer_id:
        result["reason"] = SCOPE_REASON_LAYER_UNAVAILABLE
        return result
    layer = _spec_layer(spec, layer_id)
    if layer is None:
        result["reason"] = SCOPE_REASON_LAYER_REMOVED
        return result
    sources = spec.get("sources")
    source = sources.get(layer.get("source")) if isinstance(sources, dict) and isinstance(layer.get("source"), str) else None
    from app.services.mapspec_source import ref as source_ref

    host_ref = source_ref(source) if isinstance(source, dict) else None
    if not isinstance(host_ref, str) or not host_ref.startswith("ref:"):
        result["reason"] = SCOPE_REASON_NO_DATASET_REF
        return result
    dataset_ref = claimed.get("dataset_ref") or host_ref
    if not isinstance(dataset_ref, str):
        result["reason"] = SCOPE_REASON_NO_DATASET_REF
        return result
    resolver = getattr(store, "resolve_alias", None)
    try:
        canonical_host = await resolver(session_id, host_ref) if callable(resolver) else host_ref
        canonical_claim = await resolver(session_id, dataset_ref) if callable(resolver) else dataset_ref
    except Exception:  # noqa: BLE001 — alias identity cannot be guessed
        result["reason"] = SCOPE_REASON_REF_UNRESOLVABLE
        return result
    if not isinstance(canonical_host, str) or not canonical_host.startswith("ref:"):
        result["reason"] = SCOPE_REASON_REF_UNRESOLVABLE
        return result
    if canonical_host != canonical_claim:
        result["reason"] = SCOPE_REASON_REF_MISMATCH
        result["checks"]["layer"] = "source_ref_mismatch"
        return result
    dataset_ref = canonical_host
    result["dataset_ref"] = dataset_ref
    result["checks"]["layer"] = "ok"

    getter = getattr(store, "get", None)
    payload: Any = None
    if callable(getter):
        try:
            payload = await getter(session_id, dataset_ref)
        except Exception:  # noqa: BLE001 — 读失败 = 不可验证（fail-closed）
            logger.warning("[analysis_scope] ref read failed for %s", dataset_ref)
            payload = None
    if payload is None:
        # 所有权：ref 只在本 session 的 store 内可解析（跨会话 ref 拒绝）。
        result["reason"] = SCOPE_REASON_REF_UNRESOLVABLE
        return result
    result["checks"]["ownership"] = "ok"

    descriptor: Mapping[str, object] = {}
    desc_getter = getattr(store, "get_ref_descriptor", None)
    if callable(desc_getter):
        try:
            descriptor = await desc_getter(session_id, dataset_ref) or {}
        except Exception:  # noqa: BLE001 — 描述符缺席按空处理（显式 unknown）
            descriptor = {}
    if not isinstance(descriptor, dict):
        descriptor = {}
    content_revision = descriptor.get("content_revision")
    result["content_revision"] = (
        int(content_revision) if isinstance(content_revision, (int, float)) and not isinstance(content_revision, bool) and math.isfinite(content_revision) else None
    )
    crs = descriptor.get("crs")
    result["crs"] = crs if isinstance(crs, str) else None
    result["bbox"] = (
        _bbox4(descriptor.get("bbox"))
    )

    # revision 一致（层 provenance 盖章 vs descriptor；无盖章 → 诚实 unavailable）。
    provenance = layer.get("provenance") if isinstance(layer, dict) else None
    stamped = (
        provenance.get("content_revision")
        if isinstance(provenance, dict)
        else source.get("content_revision") if isinstance(source, dict) else None
    )
    if isinstance(stamped, (int, float)) and not isinstance(stamped, bool) and math.isfinite(stamped) and result["content_revision"] is not None:
        if int(stamped) != result["content_revision"]:
            result["reason"] = SCOPE_REASON_REVISION_DRIFTED
            result["status"] = _STATUS_STALE
            result["checks"]["revision"] = "drifted"
            return result
        result["checks"]["revision"] = "ok"
    else:
        result["checks"]["revision"] = "unavailable"

    # 要素存活（≤ MAX_LIVENESS_FEATURES 才逐点核对；超限跳过披露）。
    requested = _clip_ids(claimed.get("feature_ids"))
    feature_count = descriptor.get("feature_count")
    if not requested:
        result["checks"]["features"] = "not_applicable"
        result["status"] = _STATUS_USABLE
        result["reason"] = SCOPE_REASON_OK
        return result
    if isinstance(feature_count, (int, float)) and int(feature_count) > MAX_LIVENESS_FEATURES:
        result["checks"]["features"] = "skipped_large_dataset"
        result["feature_ids"] = requested
        result["status"] = _STATUS_USABLE
        result["reason"] = SCOPE_REASON_OK
        return result
    present = _feature_ids_in_payload(payload, requested)
    missing = [fid for fid in requested if fid not in set(present)]
    result["missing_ids"] = missing
    if not present:
        result["reason"] = SCOPE_REASON_FEATURES_MISSING
        result["status"] = _STATUS_STALE
        result["checks"]["features"] = "all_missing"
        return result
    result["feature_ids"] = present
    result["checks"]["features"] = "partial_missing" if missing else "ok"
    # SW-02：样本截断披露 —— feature_ids 是 ≤50 的有界样本而非全量命中集
    # 时，验证面显式标注（消费方必须能对用户说"分析的是样本/共 M 个"）。
    if result.get("ids_truncated") is True:
        result["checks"]["features"] = (
            "truncated_sample+partial_missing" if missing else "truncated_sample"
        )
    result["status"] = _STATUS_USABLE
    result["reason"] = SCOPE_REASON_OK
    return result
