"""Requirement patch 协议（F02 DoD #3：可 replay、可 diff、可归因）。

硬约束：

- **白名单寻址**：path 必须命中 schema 绑定的处理器，值在写边界校验
  （fail-closed，非法值/路径抛 typed error，不静默进入）；
- **user-wins**：``origin="user"`` 的字段仅 ``actor="user"`` 可改
  （D-05）；locks 同理，唯一解除方式是 user 的 ``remove_lock``；
- **CAS**：``expected_revision`` 不匹配 → :class:`PatchStale`；
- **幂等**：``op_id`` 重复 → 不重复应用（返回原文档，``applied=False``）；
- **可回放**：:func:`replay` 从 genesis 顺序重放 journal 必须重建现网
  文档（回放不变式由测试钉住）；
- **core 同步**：patch 写 section 的同时经显式 SYNC 表维护
  ``MapRequestIntent`` 对应字段（D-02 单一理解真相，不产生第二语义）。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple, get_args

from app.lib.cartography.standards import MAP_AUDIENCES, MAP_PURPOSES
from app.services.gis_harness.intent import (
    ExportFormat,
    ScopeIntent,
    SubjectIntent,
    TaskType,
)
from app.services.gis_harness.requirement_ir import normalize as norm
from app.services.gis_harness.requirement_ir.contracts import (
    MAX_LOCKS,
    MAX_MEASURES,
    MAX_TEXT,
    EMPTY_PROVENANCE,
    GISIntentSpec,
    MeasureSpec,
    PatchRecord,
    Provenance,
    RequirementDocument,
    UserLock,
)
from app.services.gis_harness.requirement_ir.lifecycle import (
    can_accept,
    transition_document,
)
from app.services.gis_harness.workflow_instance import canonical_fingerprint

_OUTPUT_PURPOSES = (
    "screen_16_9", "screen_4_3", "a4_portrait", "a4_landscape",
    "a3_portrait", "a3_landscape",
)
_TASK_TYPES = tuple(get_args(TaskType))
_LEVELS = ("country", "province", "city", "district", "unknown")
_SUBJECT_TYPES = ("poi", "facility", "boundary", "region", "network", "raster", "unknown")
_STATISTICS = ("count", "sum", "mean", "median", "min", "max",
               "ratio", "rate", "density", "share", "index", "none")
_DIMENSIONS = ("none", "administrative", "grid", "category", "custom")
_NORMALIZATIONS = ("none", "per_area", "per_capita", "custom")
_GRANULARITIES = ("none", "year", "quarter", "month", "day")
_SRKINDS = ("none", "within", "intersects", "near", "service_area", "drainage_to", "along")
_COMPONENTS = ("title", "legend", "scale_bar", "north_arrow", "labels", "attribution")

# lock scope → 受保护 path 前缀（user-wins 锁定面）
_LOCK_SCOPE_PATHS: Dict[str, Tuple[str, ...]] = {
    "palette": ("representation.palette",),
    "geometry_kind": ("representation.geometry_kind",),
    "layer_visibility": ("representation.hidden_layer_ids",),
    "layer_lock": ("representation.locked_layer_ids",),
    "component": ("components.",),
    "measure": ("measures.",),
    "group_by": ("statistics.group_by", "statistics.dimension"),
    "purpose": ("purpose",),
    "audience": ("audience",),
    "export_format": ("output.formats",),
    "aoi": ("aoi.name", "aoi.level"),
}


class PatchError(ValueError):
    """patch 拒绝基类（``code`` 为稳定 reason code）。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code


class PatchUnknownPath(PatchError):
    def __init__(self, path: str) -> None:
        super().__init__("patch_unknown_path", f"path {path!r} 不在白名单")


class PatchValueInvalid(PatchError):
    def __init__(self, path: str, detail: str = "") -> None:
        super().__init__("patch_value_invalid", f"path {path!r} 值非法 {detail}".strip())


class PatchConflict(PatchError):
    """user-wins 冲突：非 user actor 触碰 user-owned 字段/锁。"""

    def __init__(self, path: str, actor: str) -> None:
        super().__init__(
            "patch_user_wins_conflict",
            f"path {path!r} 为用户显式输入，actor={actor!r} 不可改写")


class PatchStale(PatchError):
    def __init__(self, expected: Optional[int], current: int) -> None:
        super().__init__(
            "patch_stale_revision",
            f"expected_revision={expected!r} 与现网 revision={current} 不符")


class PatchBlocked(PatchError):
    """accept 门禁未过（存在 open blocking 歧义）。"""

    def __init__(self, codes: str) -> None:
        super().__init__("patch_accept_blocked", f"存在未决 blocking 歧义: {codes}")


# ── 值校验器（写边界） ────────────────────────────────────────────────


def _v_str(path: str, value: Any) -> str:
    if not isinstance(value, str):
        raise PatchValueInvalid(path, "expect str")
    return value[:MAX_TEXT]


def _v_bool(path: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise PatchValueInvalid(path, "expect bool")
    return value


def _v_opt_bool(path: str, value: Any):
    if value is None:
        return None
    return _v_bool(path, value)


def _v_opt_int(path: str, value: Any, lo: int, hi: int):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise PatchValueInvalid(path, f"expect int in [{lo},{hi}]")
    return value


def _v_vocab(vocab) -> Callable[[str, Any], str]:
    def check(path: str, value: Any) -> str:
        if not isinstance(value, str) or value not in vocab:
            raise PatchValueInvalid(path, f"expect one of {vocab}")
        return value
    return check


def _v_str_list(path: str, value: Any) -> Tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or len(value) > 32:
        raise PatchValueInvalid(path, "expect list[str] <=32")
    return tuple(_v_str(path, v)[:128] for v in value)


def _v_formats(path: str, value: Any) -> Tuple[ExportFormat, ...]:
    formats = norm.normalize_formats(value if isinstance(value, (list, tuple)) else [value])
    if not formats and value:
        raise PatchValueInvalid(path, "无可识别格式（png/pdf/svg/csv/geojson）")
    if len(formats) > 8:
        raise PatchValueInvalid(path, "formats 过多")
    return formats  # type: ignore[return-value]


def _v_channels(path: str, value: Any) -> Dict[str, str]:
    if not isinstance(value, dict) or len(value) > 8:
        raise PatchValueInvalid(path, "expect dict[str,str] <=8")
    return {str(k)[:32]: str(v)[:32] for k, v in value.items()}


def _v_datasets(path: str, value: Any) -> Tuple[str, ...]:
    items = _v_str_list(path, value)
    return items[:16]


# path → (setter(spec_dict, value), validator)
# setter 直接操作 intent 的对应模型属性（pydantic validate_assignment 面在
# GISIntentSpec 层不适用，故在各 section 模型上就地赋值——它们同样 forbid extra）。
_SETTERS: Dict[str, Tuple[Callable[[GISIntentSpec, Any], None], Callable[[str, Any], Any]]] = {
    "aoi.name": (lambda s, v: setattr(s.aoi, "name", v), _v_str),
    "aoi.level": (lambda s, v: setattr(s.aoi, "level", v), _v_vocab(_LEVELS)),
    "aoi.geometry_ref": (lambda s, v: setattr(s.aoi, "geometry_ref", v), _v_str),
    "subject.type": (lambda s, v: setattr(s.subject, "type", v), _v_vocab(_SUBJECT_TYPES)),
    "subject.category": (lambda s, v: setattr(s.subject, "category", v), _v_str),
    "purpose": (lambda s, v: setattr(s, "purpose", v), _v_vocab(MAP_PURPOSES + ("",))),
    "audience": (lambda s, v: setattr(s, "audience", v), _v_vocab(MAP_AUDIENCES + ("",))),
    "task.task_type": (lambda s, v: setattr(s.task, "task_type", v), _v_vocab(_TASK_TYPES)),
    "time.range_start": (lambda s, v: setattr(s.time, "range_start", v), _v_str),
    "time.range_end": (lambda s, v: setattr(s.time, "range_end", v), _v_str),
    "time.granularity": (lambda s, v: setattr(s.time, "granularity", v), _v_vocab(_GRANULARITIES)),
    "time.series": (lambda s, v: setattr(s.time, "series", v), _v_bool),
    "statistics.dimension": (lambda s, v: setattr(s.statistics, "dimension", v), _v_vocab(_DIMENSIONS)),
    "statistics.group_by": (lambda s, v: setattr(s.statistics, "group_by", v), _v_str),
    "statistics.normalization": (lambda s, v: setattr(s.statistics, "normalization", v), _v_vocab(_NORMALIZATIONS)),
    "statistics.denominator": (lambda s, v: setattr(s.statistics, "denominator", v), _v_str),
    "spatial_relation.kind": (lambda s, v: setattr(s.spatial_relation, "kind", v), _v_vocab(_SRKINDS)),
    "spatial_relation.target": (lambda s, v: setattr(s.spatial_relation, "target", v), _v_str),
    "spatial_relation.distance_m": (lambda s, v: setattr(s.spatial_relation, "distance_m", v),
                                    lambda p, v: _v_opt_int(p, v, 0, 10_000_000)),
    "representation.palette": (lambda s, v: setattr(s.representation, "palette", norm.normalize_palette(v)), _v_str),
    "representation.geometry_kind": (lambda s, v: setattr(s.representation, "geometry_kind", norm.normalize_geometry_kind(v)), _v_str),
    "representation.hidden_layer_ids": (lambda s, v: setattr(s.representation, "hidden_layer_ids", _v_str_list("representation.hidden_layer_ids", v)), _v_str_list),
    "representation.locked_layer_ids": (lambda s, v: setattr(s.representation, "locked_layer_ids", _v_str_list("representation.locked_layer_ids", v)), _v_str_list),
    "representation.pinned_channels": (lambda s, v: setattr(s.representation, "pinned_channels", _v_channels("representation.pinned_channels", v)), _v_channels),
    "output.live_map": (lambda s, v: setattr(s.output, "live_map", v), _v_bool),
    "output.formats": (lambda s, v: setattr(s.output, "formats", _v_formats("output.formats", v)), _v_formats),
    "output.publish": (lambda s, v: setattr(s.output, "publish", v), _v_bool),
    "output.report": (lambda s, v: setattr(s.output, "report", v), _v_bool),
    "output.dpi": (lambda s, v: setattr(s.output, "dpi", _v_opt_int("output.dpi", v, 72, 1200)), lambda p, v: _v_opt_int(p, v, 72, 1200)),
    "output.output_purpose": (lambda s, v: setattr(s.output, "output_purpose", v), _v_vocab(_OUTPUT_PURPOSES)),
    "datasets": (lambda s, v: setattr(s, "datasets", _v_datasets("datasets", v)), _v_datasets),
}
for _component in _COMPONENTS:
    _SETTERS[f"components.{_component}"] = (
        (lambda comp: lambda s, v: setattr(s.components, comp, v))(_component),
        lambda p, v: _v_opt_bool(p, v),
    )
for _measure_field in ("phrase", "statistic", "denominator", "temporal_required", "subject_token"):
    _MEASURE_VALIDATORS = {
        "phrase": _v_str,
        "subject_token": _v_str,
        "denominator": _v_str,
        "statistic": _v_vocab(_STATISTICS),
        "temporal_required": _v_bool,
    }
    _SETTERS[f"measures.*.{_measure_field}"] = (
        (lambda f: lambda s, v, mid=None: _set_measure_field(s, mid, f, v))(_measure_field),
        _MEASURE_VALIDATORS[_measure_field],
    )


def _set_measure_field(spec: GISIntentSpec, measure_id: Optional[str], field: str, value: Any) -> None:
    for measure in spec.measures:
        if measure.id == measure_id:
            setattr(measure, field, value)
            return
    raise PatchValueInvalid(f"measures.{measure_id}.{field}", "measure id 不存在")


def _resolve_setter(path: str):
    if path in _SETTERS:
        return _SETTERS[path]
    if path.startswith("measures.") and path.count(".") == 2:
        head, measure_id, field = path.split(".")
        template = _SETTERS.get(f"measures.*.{field}")
        if template is not None:
            setter, validator = template
            return (lambda s, v, mid=measure_id: setter(s, v, mid=mid), validator)
    return None


# ── core 同步表（D-02：section patch 的显式 core 对应面） ────────────────


def _sync_core(spec: GISIntentSpec, path: str) -> None:
    core = spec.core
    if path == "aoi.name":
        core.scope = ScopeIntent(name=spec.aoi.name, level=core.scope.level)  # type: ignore[arg-type]
    elif path == "aoi.level":
        core.scope = ScopeIntent(name=core.scope.name, level=spec.aoi.level)  # type: ignore[arg-type]
    elif path == "task.task_type":
        core.task = spec.task.task_type
    elif path == "subject.type":
        core.subject = SubjectIntent(type=spec.subject.type, category=core.subject.category)
        core.entity_type = spec.subject.type
    elif path == "subject.category":
        core.subject = SubjectIntent(type=core.subject.type, category=spec.subject.category)
    elif path == "statistics.group_by":
        core.group_by = spec.statistics.group_by
    elif path in ("time.range_start", "time.range_end", "time.granularity", "time.series"):
        core.time = _canonical_time_str(spec)
    elif path == "output.formats":
        core.export_intents = list(spec.output.formats)
    elif path == "output.report":
        core.report_product = spec.output.report


def _canonical_time_str(spec: GISIntentSpec) -> str:
    if spec.time.range_start and spec.time.range_end:
        return f"{spec.time.range_start}-{spec.time.range_end}"
    if spec.time.range_start:
        return spec.time.range_start
    if spec.time.series and spec.time.granularity != "none":
        return {"year": "yearly", "quarter": "quarterly",
                "month": "monthly", "day": "daily"}.get(spec.time.granularity, "")
    return ""


def provenance_for(patch: PatchRecord) -> Provenance:
    if patch.actor == "user":
        return Provenance(origin="user", turn=patch.turn, evidence_refs=(patch.op_id,))
    if patch.actor == "agent":
        return Provenance(origin="llm", turn=patch.turn,
                          evidence_refs=(patch.op_id,), rationale=patch.reason[:MAX_TEXT])
    return Provenance(origin="default", turn=patch.turn,
                      rationale=patch.reason[:MAX_TEXT] or "system default")


def _SECTION_MAP(spec: GISIntentSpec) -> Dict[str, Any]:
    return {
        "aoi": spec.aoi, "subject": spec.subject, "time": spec.time,
        "statistics": spec.statistics, "spatial_relation": spec.spatial_relation,
        "representation": spec.representation, "components": spec.components,
        "output": spec.output, "task": spec.task,
    }


def _get_path_value(spec: GISIntentSpec, path: str) -> Any:
    """按 dotted path 读取 spec 当前值（只支持 _SETTERS 白名单内的路径）。"""
    if path == "datasets":
        return tuple(spec.datasets)
    if path == "purpose":
        return spec.purpose
    if path == "audience":
        return spec.audience
    section_attr, _, rest = path.partition(".")
    section = _SECTION_MAP(spec).get(section_attr)
    if section is None:
        return None
    if not rest:
        return section
    return getattr(section, rest, None)


def _is_default_value(value: Any) -> bool:
    """「新话语对该面无表达」的判定：字段处于默认空态。"""
    return value is None or value == "" or value is False \
        or value == () or value == [] or value == {}


def _measure_provenance(spec: GISIntentSpec, path: str) -> Optional[Provenance]:
    """measures.<id>.field 路径 → 对应 measure 的 provenance（精确归属）。"""
    parts = path.split(".")
    if len(parts) == 3 and parts[0] == "measures":
        for measure in spec.measures:
            if measure.id == parts[1]:
                return measure.provenance
    return None


def _current_owner(spec: GISIntentSpec, path: str) -> Provenance:
    override = spec.field_provenance.get(path)
    if override is not None:
        return override
    measure_prov = _measure_provenance(spec, path)
    if measure_prov is not None:
        return measure_prov
    section_attr = path.split(".", 1)[0]
    section = _SECTION_MAP(spec).get(section_attr)
    if section is not None:
        return getattr(section, "provenance", EMPTY_PROVENANCE)
    return Provenance(origin="default")


def _lock_conflict(spec: GISIntentSpec, path: str, actor: str) -> Optional[UserLock]:
    if actor == "user":
        return None
    for lock in spec.locks:
        for protected in _LOCK_SCOPE_PATHS.get(lock.scope, ()):
            if path == protected or path.startswith(protected):
                return lock
    return None


# ── 应用 / 回放 / diff ───────────────────────────────────────────────


def apply_patch(
    doc: RequirementDocument,
    patch: PatchRecord,
) -> Tuple[RequirementDocument, bool]:
    """应用单个 patch（纯函数）。

    Returns:
        (new_doc, applied)；``applied=False`` 表示 op_id 幂等跳过。

    Raises:
        PatchStale / PatchConflict / PatchUnknownPath / PatchValueInvalid /
        PatchBlocked（fail-closed；调用方负责转 typed reason code 上报）。
    """
    if any(p.op_id == patch.op_id for p in doc.patches):
        return (doc, False)
    if patch.expected_revision is not None and patch.expected_revision != doc.revision:
        raise PatchStale(patch.expected_revision, doc.revision)

    spec = doc.intent.model_copy(deep=True)
    updates: Dict[str, Any] = {}

    if patch.op == "set":
        handler = _resolve_setter(patch.path)
        if handler is None:
            raise PatchUnknownPath(patch.path)
        setter, validator = handler
        owner = _current_owner(spec, patch.path)
        if owner.is_user() and patch.actor != "user":
            raise PatchConflict(patch.path, patch.actor)
        lock = _lock_conflict(spec, patch.path, patch.actor)
        if lock is not None:
            raise PatchConflict(patch.path, f"{patch.actor}:locked:{lock.scope}")
        value = validator(patch.path, patch.value)
        setter(spec, value)
        provenance = provenance_for(patch)
        _touch_provenance(spec, patch.path, provenance)
        _sync_core(spec, patch.path)

    elif patch.op == "add_measure":
        fields = _measure_fields(patch)
        if len(spec.measures) >= MAX_MEASURES:
            raise PatchValueInvalid("measures", "measure 数超上限")
        measure_id = f"m{len(spec.measures) + 1}"
        if any(m.id == measure_id for m in spec.measures):
            measure_id = f"m{canonical_fingerprint({'op': patch.op_id})[:6]}"
        measure = MeasureSpec(id=measure_id, **fields)
        measure.provenance = provenance_for(patch)
        spec.measures.append(measure)
        if not spec.core.measure and fields.get("statistic"):
            spec.core.measure = str(fields["statistic"])

    elif patch.op == "remove_measure":
        measure_id = str((patch.value or {}).get("id", ""))
        target = next((m for m in spec.measures if m.id == measure_id), None)
        if target is None:
            raise PatchValueInvalid("measures", f"measure id {measure_id!r} 不存在")
        if target.provenance.is_user() and patch.actor != "user":
            raise PatchConflict(f"measures.{measure_id}", patch.actor)
        spec.measures = [m for m in spec.measures if m.id != measure_id]

    elif patch.op == "answer_ambiguity":
        fields = _measure_fields_strict(patch, ("context_key", "answer"))
        target = next(
            (a for a in spec.ambiguities
             if a.context_key == fields["context_key"] and a.state == "open"),
            None)
        if target is None:
            raise PatchValueInvalid("ambiguities", "context_key 无 open 歧义")
        # 澄清闭环（review P1-3）：答案必须回写到寻址字段，否则
        # aoi_unresolved 答完 aoi.name 仍空、accept 门禁被静默放行。
        # 先验证后落账（fail-closed，无部分状态）。
        handler = _resolve_setter(target.path) if target.path else None
        written = False
        if handler is not None and patch.actor in ("user", "agent"):
            setter, validator = handler
            value = validator(target.path, fields["answer"])
            owner = _current_owner(spec, target.path)
            if owner.is_user() and patch.actor != "user":
                raise PatchConflict(target.path, patch.actor)
            setter(spec, value)
            _touch_provenance(spec, target.path, provenance_for(patch))
            _sync_core(spec, target.path)
            written = True
        target.state = "answered"
        target.answer = str(fields["answer"])[:128]
        target.answered_turn = patch.turn
        if written and patch.actor == "user":
            _touch_provenance(spec, f"ambiguities.{fields['context_key']}",
                              provenance_for(patch))

    elif patch.op == "waive_ambiguity":
        fields = _measure_fields_strict(patch, ("context_key",))
        waived = False
        for ambiguity in spec.ambiguities:
            if ambiguity.context_key == fields["context_key"] and ambiguity.state == "open":
                if ambiguity.blocking and patch.actor != "user":
                    raise PatchConflict(
                        f"ambiguities.{fields['context_key']}",
                        f"{patch.actor}:blocking 不可系统豁免")
                ambiguity.state = "waived"
                ambiguity.answered_turn = patch.turn
                if ambiguity.default_value:
                    ambiguity.answer = ambiguity.default_value
                waived = True
        if not waived:
            raise PatchValueInvalid("ambiguities", "context_key 无 open 歧义")

    elif patch.op == "add_lock":
        fields = _measure_fields_strict(patch, ("scope", "value"))
        if patch.actor != "user":
            raise PatchConflict("locks", patch.actor)
        if len(spec.locks) >= MAX_LOCKS:
            raise PatchValueInvalid("locks", "lock 数超上限")
        lock = UserLock(scope=fields["scope"], value=str(fields["value"])[:128],  # type: ignore[arg-type]
                        provenance=provenance_for(patch))
        if not any(item.canonical() == lock.canonical() for item in spec.locks):
            spec.locks.append(lock)

    elif patch.op == "remove_lock":
        fields = _measure_fields_strict(patch, ("scope", "value"))
        if patch.actor != "user":
            raise PatchConflict("locks", patch.actor)
        raw_key = (fields["scope"], norm.normalize_text(str(fields["value"])))  # type: ignore[index]
        spec.locks = [
            item for item in spec.locks
            if (item.scope, norm.normalize_text(item.value)) != raw_key]

    elif patch.op == "accept":
        if patch.actor != "user":
            raise PatchConflict("lifecycle.accept", patch.actor)
        # accept 校验先于构造（fail-closed）；深拷贝 requirements 防止
        # 浅拷贝共享引用对入参文档的副作用。
        probe = doc.model_copy(update={"intent": spec})
        ok, blockers = can_accept(probe)
        if not ok:
            raise PatchBlocked(blockers)
        requirements = doc.requirements.model_copy(deep=True)
        for item in requirements.items:
            if item.state in ("proposed", "clarified"):
                item.state = "accepted"
        updates["requirements"] = requirements

    elif patch.op == "supersede":
        if patch.actor not in ("system", "agent"):
            raise PatchConflict("lifecycle.supersede", patch.actor)

    else:  # pragma: no cover — Literal 防线
        raise PatchUnknownPath(str(patch.op))

    lifecycle = doc.lifecycle
    if patch.op == "accept":
        lifecycle = transition_document(lifecycle, "accepted")
    elif patch.op == "supersede":
        lifecycle = "superseded"
    elif lifecycle == "accepted" and patch.actor == "user" and patch.op != "accept":
        lifecycle = "draft"   # 用户修订重新打开
    if lifecycle != doc.lifecycle:
        updates["lifecycle"] = lifecycle

    # journal 有界性由 service 层在持久化前折叠（apply 是纯函数，不持有
    # genesis——在此折叠会使 replay 不变式失效，见 review P1-1）。
    journal = list(doc.patches)
    journal.append(patch)

    # 义务面跟随理解面单向重派生（accepted 状态与 user pin 按匹配保留；
    # accept 分支已写入 accepted 条目时以其为状态源，避免重派生降级）
    if patch.op != "supersede":
        from app.services.gis_harness.requirement_ir.obligations import rederive
        probe = doc.model_copy(update={"intent": spec})
        state_source = updates["requirements"].items \
            if "requirements" in updates else doc.requirements.items
        updates["requirements"] = rederive(spec, state_source, probe)

    updates.update({
        "intent": spec,
        "revision": doc.revision + 1,
        "updated_turn": patch.turn,
        "patches": journal,
    })
    return (doc.model_copy(update=updates), True)


def _touch_provenance(spec: GISIntentSpec, path: str, provenance: Provenance) -> None:
    if len(spec.field_provenance) < 32 or path in spec.field_provenance:
        spec.field_provenance[path] = provenance
    # section 级 provenance 同步推进（新写入即离开 rule 默认来源）
    section_attr = path.split(".", 1)[0]
    section = {
        "aoi": spec.aoi, "subject": spec.subject, "time": spec.time,
        "statistics": spec.statistics, "spatial_relation": spec.spatial_relation,
        "representation": spec.representation, "components": spec.components,
        "output": spec.output, "task": spec.task,
    }.get(section_attr)
    if section is not None and not section.provenance.is_user():
        section.provenance = provenance


def _measure_fields(patch: PatchRecord) -> Dict[str, Any]:
    value = patch.value or {}
    fields: Dict[str, Any] = {}
    if "phrase" in value:
        fields["phrase"] = _v_str("measures.phrase", value["phrase"])
    if "subject_token" in value:
        fields["subject_token"] = _v_str("measures.subject_token", value["subject_token"])
    if "statistic" in value:
        fields["statistic"] = _v_vocab(_STATISTICS)("measures.statistic", value["statistic"])
    if "denominator" in value:
        fields["denominator"] = _v_str("measures.denominator", value["denominator"])
    if "temporal_required" in value:
        fields["temporal_required"] = _v_bool("measures.temporal_required", value["temporal_required"])
    return fields


def _measure_fields_strict(patch: PatchRecord, keys: Tuple[str, ...]) -> Dict[str, str]:
    value = patch.value or {}
    out: Dict[str, str] = {}
    for key in keys:
        item = value.get(key)
        if not isinstance(item, str) or not item:
            raise PatchValueInvalid(patch.path or patch.op, f"缺 {key}")
        out[key] = item[:128]
    return out


def replay(genesis: RequirementDocument, patches: List[PatchRecord]) -> RequirementDocument:
    """从 genesis 顺序重放 journal（回放不变式：结果与现网逐字段一致）。"""
    doc = genesis
    seen: set[str] = set()
    for patch in patches:
        if patch.op_id in seen:
            continue
        seen.add(patch.op_id)
        doc, _applied = apply_patch(doc, patch)
    return doc


# ── diff / 归因 ──────────────────────────────────────────────────────


def _flatten(prefix: str, value: Any, out: Dict[str, Any]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _flatten(f"{prefix}.{key}" if prefix else str(key), item, out)
    elif isinstance(value, (list, tuple)):
        out[prefix] = list(value)
    else:
        out[prefix] = value


def diff_documents(old: RequirementDocument, new: RequirementDocument) -> Dict[str, Any]:
    """语义核 diff（路径级 before/after；digest 输入面，provenance 不参与）。"""
    from app.services.gis_harness.requirement_ir.digest import canonical_core

    old_flat: Dict[str, Any] = {}
    new_flat: Dict[str, Any] = {}
    _flatten("", canonical_core(old), old_flat)
    _flatten("", canonical_core(new), new_flat)
    changes = []
    for path in sorted(set(old_flat) | set(new_flat)):
        before, after = old_flat.get(path), new_flat.get(path)
        if before != after:
            changes.append({"path": path, "before": before, "after": after})
    return {
        "changes": changes,
        "lifecycle": {"before": old.lifecycle, "after": new.lifecycle},
        "revision": {"before": old.revision, "after": new.revision},
    }


def attribute_changes(
    old: RequirementDocument, new: RequirementDocument,
) -> List[Dict[str, Any]]:
    """把 old→new 的语义变化归因到 journal patch（actor/op_id/turn/reason）。"""
    diff = diff_documents(old, new)
    changed_paths = [c["path"] for c in diff["changes"]]
    old_op_ids = {p.op_id for p in old.patches}
    attribution: List[Dict[str, Any]] = []
    for patch in new.patches:
        if patch.op_id in old_op_ids:
            continue
        touched = patch.path or patch.op
        if any(path == touched or path.startswith(touched) or touched.startswith(path)
               for path in changed_paths) or patch.op in ("accept", "supersede"):
            attribution.append({
                "path": touched, "op": patch.op, "actor": patch.actor,
                "op_id": patch.op_id, "turn": patch.turn, "reason": patch.reason,
            })
    return attribution
