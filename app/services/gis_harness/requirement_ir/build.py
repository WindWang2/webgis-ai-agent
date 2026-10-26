"""RequirementDocument 构建（core → sections 确定性派生 + 多轮 edit 差分）。

红线（D-02/D-09）：理解真相是既有 ``resolve_map_request_intent`` 产出的
``MapRequestIntent``；本模块只做**确定性投影**（无 LLM、无 IO、无第二套
解析规则）。多轮"改成各区统计"类增量不重建文档，而是
:func:`diff_to_patches` → patch 协议（保留已确认事实与 user locks）。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from app.services.gis_harness.intent import MapRequestIntent
from app.services.gis_harness.requirement_ir.classify import (
    TaskClassification,
    classification_from_core,
)
from app.services.gis_harness.requirement_ir.contracts import (
    GISIntentSpec,
    MeasureSpec,
    PatchRecord,
    Provenance,
    RequirementDocument,
    TaskSpec,
    UserLock,
)
from app.services.gis_harness.requirement_ir.digest import requirement_digest
from app.services.gis_harness.requirement_ir.obligations import derive_requirements
from app.services.gis_harness.requirement_ir.normalize import (
    normalize_admin_name,
    normalize_group_by,
    normalize_normalization,
    normalize_statistic,
    normalize_time,
)
from app.services.gis_harness.requirement_ir import normalize as norm
from app.services.gis_harness.requirement_ir.patch import replay
from app.services.gis_harness.workflow_instance import canonical_fingerprint


# AOI 卫生词表：resolver 偶发把指示词组当范围名（如「统计这片区」）。
# IR 侧做有界卫生检查——命中即视为未解析（不复制范围解析权威，只把关）。
_DEICTIC_TOKENS: Tuple[str, ...] = (
    "统计", "分析", "一下", "看看", "这", "那", "该", "此",
    "片区", "区域", "当前", "地图", "显示", "图上",
)


def derive_sections(
    core: MapRequestIntent,
    classification: TaskClassification,
    query: str = "",
) -> GISIntentSpec:
    """MapRequestIntent → typed sections（纯投影；不新造语义）。

    ``query`` 仅作时间词面来源（resolver 不承载年份/粒度解析——时间事实
    在话语表面，用有界 normalize_time 表提取，确定性）。"""
    # AOI：core.scope 是唯一解析真相；name 归一仅用于 digest 稳定。
    # 卫生检查：指代/动词污染的"范围名"视为未解析（清空 + 标记），
    # 由 clarify 的 aoi_unresolved 接管。
    aoi_name = core.scope.name
    aoi_degraded = bool(aoi_name) and any(t in aoi_name for t in _DEICTIC_TOKENS)
    if aoi_degraded:
        aoi_name = ""
    geometry_ref = (
        f"local:admin:{core.scope.level}:{normalize_admin_name(aoi_name)}"
        if aoi_name else ""
    )
    # 指标：core.measure 是度量词（count/density/…），规范形派生
    statistic = normalize_statistic(core.measure) if core.measure else "none"
    measures: List[MeasureSpec] = []
    if statistic != "none":
        measures.append(MeasureSpec(
            id="m1", phrase=core.measure, statistic=statistic,
            provenance=Provenance(origin="rule"),
        ))
    # 统计口径：core.group_by 规范化
    dimension, group_by = normalize_group_by(core.group_by) if core.group_by else ("none", "")
    normalization = normalize_normalization(core.comparison) if core.comparison else "none"
    # 时间：core.time 优先（权威词面），否则取话语词面；趋势任务自带
    # series 语义（区间缺失 → clarify 的 time_range_missing_for_series 问）
    if core.time:
        range_start, range_end, granularity, series = normalize_time(core.time)
    else:
        range_start, range_end, granularity, series = normalize_time(query)
    if core.task == "temporal_trend":
        series = True
    # 空间关系：由分析意图词表映射（kind 面即分析权威已判定的语义）
    sr_kind = "none"
    if "proximity_buffer" in core.analysis_intents:
        sr_kind = "near"
    elif "service_area" in core.analysis_intents or core.task == "accessibility_analysis":
        sr_kind = "service_area"
    # 输出面
    formats = tuple(core.export_intents)
    purpose = "briefing" if core.report_product else ""

    spec = GISIntentSpec(
        core=core,
        task=TaskSpec(
            kind=classification.kind,
            stages=classification.stages,
            task_type=core.task,
            reason_codes=classification.reason_codes,
            confidence=classification.confidence,
            provenance=Provenance(origin="rule", evidence_refs=tuple(core.matched_rules[:4])),
        ),
        measures=measures,
        field_provenance={},
    )
    spec.aoi = spec.aoi.model_copy(update={
        "name": aoi_name, "level": core.scope.level,
        "geometry_ref": geometry_ref,
        "resolver_state": ("degraded" if aoi_degraded
                           else "resolved" if aoi_name else "unresolved"),
        "provenance": Provenance(origin="rule",
                                 evidence_refs=tuple(core.matched_rules[:2]),
                                 rationale="deictic/verb token in scope name"
                                 if aoi_degraded else ""),
        "state": "proposed",
    })
    spec.subject = spec.subject.model_copy(update={
        "type": core.subject.type, "category": core.subject.category,
        "provenance": Provenance(origin="rule"),
    })
    spec.statistics = spec.statistics.model_copy(update={
        "dimension": dimension, "group_by": group_by,
        "normalization": normalization,
        "provenance": Provenance(origin="rule"),
    })
    spec.time = spec.time.model_copy(update={
        "range_start": range_start, "range_end": range_end,
        "granularity": granularity, "series": series,
        "provenance": Provenance(origin="rule"),
    })
    spec.spatial_relation = spec.spatial_relation.model_copy(update={
        "kind": sr_kind,  # type: ignore[arg-type]
        "provenance": Provenance(origin="rule"),
    })
    spec.output = spec.output.model_copy(update={
        "formats": formats, "report": core.report_product,
        # edit 是对既有成果的增量修订——live map 仍然在（review §2）
        "live_map": classification.kind not in ("query_only", "analysis"),
        "provenance": Provenance(origin="rule"),
    })
    spec.purpose = purpose
    if purpose:
        spec.field_provenance["purpose"] = Provenance(origin="rule")
    # G3D04：genesis 话语的 3D cue → SceneSpec（用户显式表达 → 字段级
    # origin=user，user-wins 从第一轮即生效；无 cue → 全默认零推断）。
    if _apply_scene_cues_to_spec(spec, query):
        _seed_scene_locks_from_cues(spec)
    return spec


def _seed_scene_locks_from_cues(spec: GISIntentSpec) -> None:
    """显式维度/相机表达 → user lock（genesis 即锁；超替重建路径同样成立）。

    同 scope 不同值的旧锁被替换（最新表达 wins）；锁数有界（MAX_LOCKS）。
    """
    from app.services.gis_harness.requirement_ir.contracts import UserLock

    def _upsert(scope: str, value: str) -> None:
        for lock in [item for item in spec.locks if item.scope == scope]:
            if lock.value != value:
                spec.locks.remove(lock)
        if not any(item.scope == scope and item.value == value
                   for item in spec.locks) and len(spec.locks) < 32:
            spec.locks.append(UserLock(
                scope=scope, value=value,  # type: ignore[arg-type]
                provenance=Provenance(origin="user", evidence_refs=("utterance",))))

    if spec.scene.dimension:
        _upsert("scene_mode", spec.scene.dimension)
    if spec.scene.camera_pitch is not None:
        _upsert("scene_camera", "pitch")
    if spec.scene.camera_bearing is not None:
        _upsert("scene_camera", "bearing")


def _apply_scene_cues_to_spec(spec: GISIntentSpec, query: str) -> bool:
    """3D cue → SceneSpec/OutputSpec.medium 就地投影（纯确定性）。

    返回是否命中任何 cue。cue 是用户话语的直接表达——字段级
    provenance 记 user（agent 后续不可静默覆盖，patch 层强制）。
    """
    from app.services.gis_harness.scene3d.cues import extract_scene3d_cues

    cues = extract_scene3d_cues(query)
    if not cues.has_any():
        return False
    user_prov = Provenance(origin="user", evidence_refs=("utterance",))
    if cues.dimension:
        spec.scene.dimension = cues.dimension  # type: ignore[assignment]
        spec.field_provenance["scene.dimension"] = user_prov
    if cues.terrain_requested is not None:
        spec.scene.terrain_requested = cues.terrain_requested
        spec.field_provenance["scene.terrain_requested"] = user_prov
    if cues.extrusion_requested is not None:
        spec.scene.extrusion_requested = cues.extrusion_requested
        spec.field_provenance["scene.extrusion_requested"] = user_prov
    if cues.exaggeration is not None:
        spec.scene.exaggeration = min(10.0, max(0.01, cues.exaggeration))
        spec.field_provenance["scene.exaggeration"] = user_prov
    if cues.camera_pitch is not None:
        spec.scene.camera_pitch = cues.camera_pitch
        spec.field_provenance["scene.camera_pitch"] = user_prov
    if cues.camera_bearing is not None:
        spec.scene.camera_bearing = cues.camera_bearing
        spec.field_provenance["scene.camera_bearing"] = user_prov
    if cues.analysis_goals:
        spec.scene.analysis_goals = cues.analysis_goals  # type: ignore[assignment]
        spec.field_provenance["scene.analysis_goals"] = user_prov
    if cues.medium:
        spec.output.medium = cues.medium  # type: ignore[assignment]
        spec.field_provenance["output.medium"] = user_prov
    return True


def build_document(
    query: str,
    core: MapRequestIntent,
    *,
    document_id: str = "",
    turn: int = 0,
    classification: Optional[TaskClassification] = None,
    carried_locks: Optional[List[UserLock]] = None,
) -> RequirementDocument:
    """构建 genesis 文档（rev 1；patches 为空 → 可从它 replay 全史）。"""
    if classification is None:
        classification = classification_from_core(query, core, has_document=False)
    spec = derive_sections(core, classification, query=query)
    if carried_locks:
        spec.locks = list(carried_locks)[:32]
        for lock in spec.locks:
            # user locks 是显式选择，落 representation 等对应面（user-wins 存活）。
            # G3D04：本话语已显式表达维度时，新表达优先——跳过冲突的旧
            # scene_mode 携带锁（最新用户表达 wins，不留双真相）。
            if lock.scope == "scene_mode" and spec.scene.dimension \
                    and lock.value != spec.scene.dimension:
                continue
            _apply_lock_to_spec(spec, lock)
        spec.locks = [
            lock for lock in spec.locks
            if not (lock.scope == "scene_mode" and spec.scene.dimension
                    and lock.value != spec.scene.dimension)]
        # 本话语的 cue 锁在携带合并后重播种（携带段以 list(carried) 覆盖
        # 了 derive_sections 播种的锁——最新表达必须在场）
        _seed_scene_locks_from_cues(spec)
    doc_id = document_id or f"req-{canonical_fingerprint({'q': query[:200], 't': turn})[:12]}"
    doc = RequirementDocument(
        document_id=doc_id,
        intent=spec,
        created_turn=turn,
        updated_turn=turn,
    )
    doc.requirements = derive_requirements(spec)
    doc.requirements.derived_from = requirement_digest(doc)
    return doc


def rederive_requirements(doc: RequirementDocument) -> RequirementDocument:
    """patch 后义务面重派生（委托 obligations；保留旧条目状态/pin）。"""
    from app.services.gis_harness.requirement_ir.obligations import rederive
    fresh = rederive(doc.intent, doc.requirements.items, doc)
    return doc.model_copy(update={"requirements": fresh})


def _apply_lock_to_spec(spec: GISIntentSpec, lock: UserLock) -> None:
    """user lock → 对应 section 面（携带重建时 user-wins 存活的机制面）。"""
    scope_value = lock.value
    if lock.scope == "palette":
        spec.representation.palette = scope_value
        spec.field_provenance["representation.palette"] = lock.provenance
    elif lock.scope == "geometry_kind":
        spec.representation.geometry_kind = scope_value
        spec.field_provenance["representation.geometry_kind"] = lock.provenance
    elif lock.scope == "group_by":
        spec.statistics.group_by = scope_value
        spec.field_provenance["statistics.group_by"] = lock.provenance
    # layer_visibility / layer_lock 的值面在 representation 元组中保持
    elif lock.scope == "layer_visibility" and scope_value:
        hidden = tuple(dict.fromkeys([*spec.representation.hidden_layer_ids, scope_value]))
        spec.representation.hidden_layer_ids = hidden
    # G3D04：场景维度/相机锁在超替重建时落回对应面（user-wins 存活）
    elif lock.scope == "scene_mode" and scope_value in ("2d", "2.5d", "3d"):
        spec.scene.dimension = scope_value  # type: ignore[assignment]
        spec.field_provenance["scene.dimension"] = lock.provenance
    elif lock.scope == "scene_camera":
        if scope_value == "pitch" and spec.scene.camera_pitch is not None:
            spec.field_provenance["scene.camera_pitch"] = lock.provenance
        elif scope_value == "bearing" and spec.scene.camera_bearing is not None:
            spec.field_provenance["scene.camera_bearing"] = lock.provenance


def diff_to_patches(
    old: RequirementDocument,
    new_core: MapRequestIntent,
    *,
    turn: int,
    query: str = "",
) -> List[PatchRecord]:
    """多轮增量：旧文档 vs 新解析 core + 话语 cue 的**字段级差分** → user patches。

    两个来源：core 差分（task/aoi/subject/group_by/time/formats/report）与
    话语 cue 差分（隐藏图层/显示图层/换色/导出格式/出版——载荷在用户原话、
    理解权威不提取的面）。统一经单一发射器生成（expected_revision 严格
    链式）；user locks 保护的字段跳过并保留锁；值与现网相同不发射（幂等）。
    """
    classification = classification_from_core(
        "", new_core, has_document=True)
    new_spec = derive_sections(new_core, classification)
    old_spec = old.intent
    patches: List[PatchRecord] = []

    def emit(op: str, path: str = "", value: Any = None, reason: str = "") -> None:
        patches.append(PatchRecord(
            op_id=f"edit-{turn}-{len(patches) + 1}-"
                  f"{canonical_fingerprint({'p': path, 'v': value, 'o': op})[:8]}",
            turn=turn, actor="user", op=op,  # type: ignore[arg-type]
            path=path, value=value, reason=reason,
            expected_revision=old.revision + len(patches),
        ))

    cue_paths = _emit_cue_patches(old_spec, query, emit)

    # ── 词面信号守卫：edit 语句的 resolver 重解析是无信号兜底
    #    （distribution_overview / district 等默认值不得当"用户要改"），
    #    只有话语本身带对应词面时才允许该字段的差分发射。──
    q_norm = (query or "").lower()
    query_task_signal = _QUERY_TASK_SIGNAL_RE.search(q_norm) is not None
    q_time_start, q_time_end, q_time_gran, q_time_series = normalize_time(q_norm)
    gb_dimension, gb_group = normalize_group_by(q_norm)

    if new_spec.task.task_type != old_spec.task.task_type and (
            old_spec.task.task_type == "distribution_overview"
            or query_task_signal):
        emit("set", "task.task_type", new_spec.task.task_type, "task changed by user")
    if normalize_admin_name(new_spec.aoi.name) != normalize_admin_name(old_spec.aoi.name) \
            and new_spec.aoi.name and new_spec.aoi.name in (query or "") \
            and not _locked(old_spec, "aoi.name"):
        emit("set", "aoi.name", new_spec.aoi.name, "scope changed by user")
    if new_spec.aoi.level != old_spec.aoi.level and new_spec.aoi.level != "unknown" \
            and new_spec.aoi.name and new_spec.aoi.name in (query or "") \
            and not _locked(old_spec, "aoi.level"):
        emit("set", "aoi.level", new_spec.aoi.level, "scope level changed")
    # 隐藏/显示图层的 utterance 会带出 subject 词面（如「道路」），那是表达
    # 面操作而非分析主体变化——cue 已覆盖时抑制 subject 差分。
    if new_core.subject.category != old_spec.core.subject.category \
            and new_core.subject.category \
            and "representation.hidden_layer_ids" not in cue_paths \
            and not _locked(old_spec, "subject.category"):
        emit("set", "subject.category", new_core.subject.category,
             "subject changed by user")
    if gb_group and gb_group != old_spec.statistics.group_by \
            and not _locked(old_spec, "statistics.group_by"):
        emit("set", "statistics.group_by", gb_group, "group_by changed by user")
        if gb_dimension != old_spec.statistics.dimension:
            emit("set", "statistics.dimension", gb_dimension,
                 "dimension follows group_by")
    if q_time_start and q_time_start != old_spec.time.range_start:
        emit("set", "time.range_start", q_time_start, "time range changed")
    if q_time_end and q_time_end != old_spec.time.range_end:
        emit("set", "time.range_end", q_time_end, "time range changed")
    if (q_time_start or q_time_gran != "none") \
            and q_time_series != old_spec.time.series:
        emit("set", "time.series", q_time_series, "series flag changed")
    # 输出格式在 edit 模式只由 cue 路径写入（话语中的显式格式词）——
    # resolver 重解析的 export_intents 无信号，不得回退既有格式义务。
    if new_spec.output.report != old_spec.output.report and "报告" in (query or ""):
        emit("set", "output.report", new_spec.output.report, "report flag changed")
    # 指标差分：新 core 的度量词出现旧 measures 没有的规范形 → 增补指标
    new_stat = normalize_statistic(new_core.measure) if new_core.measure else "none"
    old_stats = {m.statistic for m in old_spec.measures}
    if new_stat != "none" and new_stat not in old_stats and len(old_spec.measures) < 8:
        emit("add_measure", value={"statistic": new_stat,
                                   "phrase": new_core.measure[:128]},
             reason="measure changed by user")
    return patches


_LAYER_HIDE_RE = re.compile(r"隐藏\s*([^,，。;；！!?？]{1,24}?)(图层|层|layer)")
_LAYER_SHOW_RE = re.compile(r"(?:显示|取消隐藏|恢复)\s*([^,，。;；！!?？]{1,24}?)(图层|层|layer)")
_COLOR_CHANGE_RE = re.compile(r"(换成|改成|改为|换为|涂成|recolor to|change .* to)")
_COLOR_WORD_RE = re.compile(
    r"(蓝色|红色|绿色|橙色|紫色|暖色|冷色|灰度|viridis|blue|red|green|orange|purple)")
_PUBLISH_CUES: Tuple[str, ...] = ("出版", "发布", "print-ready", "publication")
_FORMAT_WORDS: Tuple[str, ...] = ("pdf", "png", "svg", "csv", "geojson")
# edit 语句中"用户真的在说任务/统计语义"的词面信号（守卫兜底回退）
_QUERY_TASK_SIGNAL_RE = re.compile(
    r"统计|分析|密度|占比|比例|趋势|对比|比较|热力|分级|聚合|各|每|"
    r"statistics|analysis|density|trend|compare|per ")


def _emit_cue_patches(spec: GISIntentSpec, query: str, emit) -> set:
    """隐藏/显示图层、换色、导出格式、出版、3D 表达 —— cue → user patches。

    只发射"值确有变化"的 patch（幂等：重复话语零 patch）；换色/隐藏/维度
    附 user lock（显式选择 user-wins 存活）。返回**话语触及**的 path 集合
    （无论是否发射——幂等重放时同样要抑制对应面的差分）。
    """
    touched: set = set()
    q = (query or "").lower()
    if not q:
        return touched
    existing_locks = {(lock.scope, lock.value) for lock in spec.locks}

    # ── G3D04：3D 表达 cue（维度/地形/挤出/夸张/相机/媒介/分析目标） ──
    touched |= _emit_scene_cue_patches(spec, q, emit, existing_locks)

    hide = _LAYER_HIDE_RE.search(q)
    if hide:
        layer = hide.group(1).strip().replace("的", "")[:64]
        touched.add("representation.hidden_layer_ids")
        if layer and not _locked(spec, "representation.hidden_layer_ids") \
                and layer not in spec.representation.hidden_layer_ids:
            hidden = tuple(dict.fromkeys([*spec.representation.hidden_layer_ids, layer]))
            emit("set", "representation.hidden_layer_ids", sorted(hidden),
                 f"hide layer cue: {layer}")
            if ("layer_visibility", layer) not in existing_locks:
                emit("add_lock", value={"scope": "layer_visibility", "value": layer},
                     reason="explicit user lock")
    show = _LAYER_SHOW_RE.search(q)
    if show:
        layer = show.group(1).strip().replace("的", "")[:64]
        touched.add("representation.hidden_layer_ids")
        if layer and layer in spec.representation.hidden_layer_ids \
                and not _locked(spec, "representation.hidden_layer_ids"):
            remaining = tuple(x for x in spec.representation.hidden_layer_ids if x != layer)
            emit("set", "representation.hidden_layer_ids", sorted(remaining),
                 f"show layer cue: {layer}")
    if _COLOR_CHANGE_RE.search(q) and _COLOR_WORD_RE.search(q):
        touched.add("representation.palette")
        color = _COLOR_WORD_RE.search(q)
        if color and not _locked(spec, "representation.palette"):
            palette = norm.normalize_palette(color.group(1))
            if palette and palette != spec.representation.palette:
                emit("set", "representation.palette", palette,
                     "palette changed by user")
                if ("palette", palette) not in existing_locks:
                    emit("add_lock", value={"scope": "palette", "value": palette},
                         reason="explicit user lock")
    formats = norm.normalize_formats([w for w in _FORMAT_WORDS if w in q])
    if formats:
        touched.add("output.formats")
        if not _locked(spec, "output.formats"):
            merged = tuple(dict.fromkeys([*spec.output.formats, *formats]))
            if merged != spec.output.formats:
                emit("set", "output.formats", sorted(merged),
                     "export format requested by user")
    if any(cue in q for cue in _PUBLISH_CUES):
        touched.add("output.publish")
        if not spec.output.publish:
            emit("set", "output.publish", True, "publication requested by user")
    return touched


def _emit_scene_cue_patches(
    spec: GISIntentSpec, q: str, emit, existing_locks: set,
) -> set:
    """3D cue → user patches（edit 轮；与 genesis 的 _apply_scene_cues_to_spec
    共用 cues.py 词面，投影语义一致）。

    维度 cue 附 ``scene_mode`` 锁（用户显式维度选择跨轮存活）；相机角度
    cue 附 ``scene_camera`` 锁。幂等：值相同不发射；已锁字段跳过（保留锁）。
    """
    from app.services.gis_harness.scene3d.cues import extract_scene3d_cues

    touched: set = set()
    cues = extract_scene3d_cues(q)
    if not cues.has_any():
        return touched
    scene = spec.scene
    if cues.dimension:
        touched.add("scene.dimension")
        existing_scene_lock = next(
            (lock for lock in spec.locks
             if lock.scope == "scene_mode" and lock.value != cues.dimension),
            None)
        if not _locked(spec, "scene.dimension") \
                and cues.dimension != scene.dimension:
            emit("set", "scene.dimension", cues.dimension,
                 f"scene dimension cue: {cues.dimension}")
            # 维度切换是新的显式选择——清除旧降级注记（新决策会重判）
            if scene.degrade_reason:
                emit("set", "scene.degrade_reason", "", "re-expressed by user")
            if ("scene_mode", cues.dimension) not in existing_locks:
                emit("add_lock", value={"scope": "scene_mode", "value": cues.dimension},
                     reason="explicit user scene choice")
        elif existing_scene_lock is not None and cues.dimension != scene.dimension:
            # 同一用户改主意：先解除自己的旧锁，再表达新选择（锁不挡 owner）
            emit("remove_lock",
                 value={"scope": "scene_mode", "value": existing_scene_lock.value},
                 reason="user re-expressed scene dimension")
            emit("set", "scene.dimension", cues.dimension,
                 f"scene dimension cue: {cues.dimension}")
            if scene.degrade_reason:
                emit("set", "scene.degrade_reason", "", "re-expressed by user")
            emit("add_lock", value={"scope": "scene_mode", "value": cues.dimension},
                 reason="explicit user scene choice")
    if cues.terrain_requested is not None:
        touched.add("scene.terrain_requested")
        if not _locked(spec, "scene.terrain_requested") \
                and cues.terrain_requested != scene.terrain_requested:
            emit("set", "scene.terrain_requested", cues.terrain_requested,
                 "terrain expression requested by user")
    if cues.extrusion_requested is not None:
        touched.add("scene.extrusion_requested")
        if not _locked(spec, "scene.extrusion_requested") \
                and cues.extrusion_requested != scene.extrusion_requested:
            emit("set", "scene.extrusion_requested", cues.extrusion_requested,
                 "vertical extrusion requested by user")
    if cues.exaggeration is not None:
        touched.add("scene.exaggeration")
        clamped = round(min(10.0, max(0.01, cues.exaggeration)), 4)
        if not _locked(spec, "scene.exaggeration") and clamped != scene.exaggeration:
            emit("set", "scene.exaggeration", clamped,
                 f"exaggeration cue: {clamped}")
    if cues.camera_pitch is not None:
        touched.add("scene.camera_pitch")
        if not _locked(spec, "scene.camera_pitch") \
                and cues.camera_pitch != scene.camera_pitch:
            emit("set", "scene.camera_pitch", cues.camera_pitch,
                 f"camera pitch cue: {cues.camera_pitch}")
            if ("scene_camera", "pitch") not in existing_locks:
                emit("add_lock", value={"scope": "scene_camera", "value": "pitch"},
                     reason="explicit user camera choice")
    if cues.camera_bearing is not None:
        touched.add("scene.camera_bearing")
        if not _locked(spec, "scene.camera_bearing") \
                and cues.camera_bearing != scene.camera_bearing:
            emit("set", "scene.camera_bearing", cues.camera_bearing,
                 f"camera bearing cue ({cues.bearing_source})")
            if ("scene_camera", "bearing") not in existing_locks:
                emit("add_lock", value={"scope": "scene_camera", "value": "bearing"},
                     reason="explicit user camera choice")
    if cues.analysis_goals:
        touched.add("scene.analysis_goals")
        merged = tuple(dict.fromkeys([*scene.analysis_goals, *cues.analysis_goals]))[:4]
        if not _locked(spec, "scene.analysis_goals") and merged != scene.analysis_goals:
            emit("set", "scene.analysis_goals", list(merged),
                 f"3d analysis goal cue: {','.join(cues.analysis_goals)}")
    if cues.medium:
        touched.add("output.medium")
        if not _locked(spec, "output.medium") and cues.medium != spec.output.medium:
            emit("set", "output.medium", cues.medium,
                 f"output medium cue: {cues.medium}")
    return touched


def _locked(spec: GISIntentSpec, path: str) -> bool:
    from app.services.gis_harness.requirement_ir.patch import _LOCK_SCOPE_PATHS
    for lock in spec.locks:
        if path in _LOCK_SCOPE_PATHS.get(lock.scope, ()):
            return True
        for prefix in _LOCK_SCOPE_PATHS.get(lock.scope, ()):
            if path.startswith(prefix):
                return True
    return False


def rebuild_with_patches(
    genesis: RequirementDocument,
    patches: List[PatchRecord],
) -> RequirementDocument:
    """从 genesis 重放全量 journal（service 层回放不变式的执行面）。"""
    return replay(genesis, patches)


def carry_user_state(old: RequirementDocument) -> Tuple[List[UserLock], Dict[str, Any]]:
    """超替/重建时必须携带的用户状态（user-wins 存活，D-05）。

    Returns:
        (user locks, {path: (value, provenance)})——值与所有权成对携带，
        由 service._restore_user_fields 决定是否落回（新话语已表达的面
        以最新用户表达优先）。
    """
    from app.services.gis_harness.requirement_ir.patch import _get_path_value
    locks = [lock for lock in old.intent.locks if lock.provenance.is_user()]
    user_fields: Dict[str, Any] = {}
    for path, prov in old.intent.field_provenance.items():
        if not prov.is_user():
            continue
        user_fields[path] = (_get_path_value(old.intent, path), prov)
    return (locks, user_fields)
