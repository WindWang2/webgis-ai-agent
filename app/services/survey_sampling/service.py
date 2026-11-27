"""Survey sampling service — 调查编排（WP1–WP5 产品入口唯一领域服务）。

Pi 工具与 REST/UI 两条入口共用本服务（同权限、同状态、同证据）。
职责：帧构建→设计冻结→可复现选样→调查记录（append-only + CAS）→
设计一致估计（源漂移/类字典漂移 → stale 拒绝）。

诚实边界（goal §4 硬约束）：
- 参考标签与预测类别分离；盲标默认（解释员看不到预测类/层名）；
- 非响应/不可判 → 预抽替补顶替（原因披露）——机制偏离 SRS 的程度
  超过设计声明上限 ⇒ 拒绝总体推断（not_evaluated）；
- 无概率设计或无参考数据 ⇒ 只出待核查计划，不出估计；
- 源内容漂移（fingerprint 不符）或类字典变化 ⇒ 估计 stale，历史保留。
"""
from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from app.lib.gis.scientific_errors import DegenerateData
from app.lib.gis.scientific_errors import InsufficientSamples as _LibInsufficient
from app.lib.survey_sampling import estimation as est_lib
from app.lib.survey_sampling.estimation import (
    EstimationNotEvaluable as _LibNotEvaluable,
)
from app.lib.survey_sampling import frame as frame_lib
from app.lib.survey_sampling import selection as sel_lib
from app.lib.survey_sampling.design import (
    MIN_N_PER_STRATUM_HARD,
    SurveyDesign,
    StratumSpec,
    design_weights,
    preview_sample_size,
)
from app.lib.survey_sampling.frame import build_frame, frame_strata_specs
from app.services.survey_sampling import store
from app.services.survey_sampling.contracts import (
    MAX_CLASS_NAME_CHARS,
    MAX_RECORD_BATCH,
    MAX_SAMPLES_PER_SURVEY,
    MAX_STRATA,
    NON_USABLE_STATUSES,
    REC_LABELLED,
    RECORD_STATUSES,
    SUB_REASONS,
    QUALITY_LEVELS,
    EVIDENCE_TYPES,
    SurveySamplingError,
)

logger = logging.getLogger(__name__)

#: 估计器版本（进 estimate artifact；语义变更即递增）
ESTIMATOR_VERSION = "survey-estimation/1.0.0"


def _map_lib_error(exc: Exception) -> SurveySamplingError:
    """科学错误 → 调查域 typed 错误（码语义对齐）。"""
    if isinstance(exc, _LibNotEvaluable):
        return SurveySamplingError(
            "ESTIMATION_NOT_EVALUABLE", str(exc), status_code=422,
            correction_hint=getattr(exc, "correction_hint", ""))
    if isinstance(exc, (_LibInsufficient, DegenerateData)):
        return SurveySamplingError(
            "INVALID_FRAME_REQUEST", str(exc), status_code=422,
            correction_hint=getattr(exc, "correction_hint", ""))
    return SurveySamplingError("SURVEY_FAILED", str(exc))


# ── 帧构建 ──────────────────────────────────────────────────────────

async def build_survey_frame(
    session_id: str,
    owner_token: Optional[str],
    owner_scope: Dict[str, str],
    *,
    source_ref: str,
    band: int,
    class_dictionary: Dict[int, str],
    trusted_dispatch: bool = False,
) -> Dict[str, Any]:
    """分类栅格 → 冻结抽样框（计数/面积/digest；无样本副作用）。"""
    if not class_dictionary:
        raise SurveySamplingError(
            "INVALID_FRAME_REQUEST",
            "class_dictionary is required (class value → class name)",
            field="class_dictionary")
    if len(class_dictionary) > MAX_STRATA:
        raise SurveySamplingError(
            "BUDGET_EXCEEDED",
            f"at most {MAX_STRATA} classes, got {len(class_dictionary)}",
            field="class_dictionary", status_code=413)
    for value, name in class_dictionary.items():
        if not name or len(name) > MAX_CLASS_NAME_CHARS:
            raise SurveySamplingError(
                "INVALID_FRAME_REQUEST",
                f"class name for value {value} must be 1..{MAX_CLASS_NAME_CHARS} "
                "chars",
                field="class_dictionary")
    source = await _resolve_source(session_id, owner_token, source_ref,
                                   trusted_dispatch)
    payload_existing = _find_frame_by_source(owner_scope, source)
    if payload_existing is not None:
        return payload_existing                      # content-addressed 幂等
    from app.lib.geo_raster.reader import RasterReader

    try:
        with RasterReader.open(source.path) as reader:
            frame = build_frame(
                reader, band=int(band),
                class_dictionary=dict(class_dictionary),
                nodata=None,
                crs=str(source.descriptor.crs
                        or reader.metadata().crs or ""),
            )
    except SurveySamplingError:
        raise
    except Exception as exc:                          # noqa: BLE001
        raise _map_lib_error(exc) from exc
    payload = dict(frame.payload)
    payload["source_ref"] = source_ref
    digest = store.save_frame_payload(owner_scope, payload)
    return store.get_frame_payload(owner_scope, digest) or payload


def _find_frame_by_source(
    owner_scope: Dict[str, str], source: Any,
) -> Optional[Dict[str, Any]]:
    """同源（uri+fingerprint）幂等：已有冻结框直接复用。"""
    for payload in store.list_frame_payloads(owner_scope):
        src = payload.get("source") or {}
        if (src.get("content_fingerprint")
                and src.get("content_fingerprint") == source.content_fingerprint
                and src.get("uri") == source.path):
            return payload
    return None


async def _resolve_source(
    session_id: str, owner_token: Optional[str], source_ref: str,
    trusted_dispatch: bool,
):
    from app.services.raster_authoring.source import resolve_source

    try:
        return await resolve_source(
            session_id, source_ref, owner_token,
            trusted_dispatch=trusted_dispatch)
    except SurveySamplingError:
        raise
    except Exception as exc:                          # noqa: BLE001
        raise SurveySamplingError(
            "SOURCE_UNAVAILABLE",
            f"raster source {source_ref!r} could not be resolved",
            field="source_ref", status_code=404) from exc


# ── 设计预览（无副作用） ────────────────────────────────────────────

def preview_allocation(
    owner_scope: Dict[str, str],
    frame_digest: str,
    *,
    strategy: str,
    n_total: Optional[int] = None,
    target_se_oa: Optional[float] = None,
    prior_precision: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    payload = store.get_frame_payload(owner_scope, frame_digest)
    if payload is None:
        raise SurveySamplingError(
            "FRAME_NOT_FOUND", f"frame {frame_digest} not found",
            status_code=404)
    strata = _specs_from_payload(payload)
    try:
        preview = preview_sample_size(
            strata, strategy=strategy,                 # type: ignore[arg-type]
            n_total=n_total, target_se_oa=target_se_oa,
            prior_precision=prior_precision)
    except (ValueError, _LibInsufficient) as exc:
        raise SurveySamplingError(
            "INVALID_DESIGN", str(exc), field="allocation",
            correction_hint=getattr(exc, "correction_hint", "")) from exc
    preview["frame_digest"] = frame_digest
    preview["total_area"] = payload["total_area"]
    preview["blind_labelling_default"] = True
    return preview


def _specs_from_payload(payload: Dict[str, Any]) -> List[StratumSpec]:
    return [
        StratumSpec(stratum_id=sid, N_units=int(info["N_units"]),
                    area=float(info["area"]))
        for sid, info in payload["strata"]
    ]


# ── 设计创建（冻结）与选样 ──────────────────────────────────────────

async def create_survey(
    owner_scope: Dict[str, str],
    frame_digest: str,
    *,
    objective: str,
    target_time: str,
    strategy: str = "equal",
    n_total: Optional[int] = None,
    target_se_oa: Optional[float] = None,
    prior_precision: Optional[Dict[str, float]] = None,
    seed: int = 42,
    backup_per_stratum: int = 0,
    blind_labelling: bool = True,
    max_nonresponse_rate: float = 0.2,
    notes: str = "",
) -> Dict[str, Any]:
    """创建调查：冻结设计（digest 不可变快照）→ 立即选样（幂等）。"""
    payload = store.get_frame_payload(owner_scope, frame_digest)
    if payload is None:
        raise SurveySamplingError(
            "FRAME_NOT_FOUND", f"frame {frame_digest} not found",
            status_code=404)
    strata = _specs_from_payload(payload)
    preview = preview_allocation(
        owner_scope, frame_digest, strategy=strategy, n_total=n_total,
        target_se_oa=target_se_oa, prior_precision=prior_precision)
    allocation = preview["allocation"]
    total_draw = sum(allocation.values()) + backup_per_stratum * len(strata)
    if total_draw > MAX_SAMPLES_PER_SURVEY:
        raise SurveySamplingError(
            "BUDGET_EXCEEDED",
            f"total samples+backups {total_draw:,} exceed budget "
            f"{MAX_SAMPLES_PER_SURVEY:,}",
            status_code=413,
            correction_hint="reduce n_total or backup_per_stratum")
    survey_id = uuid.uuid4().hex[:16]
    design = SurveyDesign(
        design_id=survey_id,
        design_type="stratified_srs",
        frame_ref=frame_digest,
        frame_crs=payload["crs"],
        strata=strata,
        allocation={k: int(v) for k, v in allocation.items()},
        backup_per_stratum=int(backup_per_stratum),
        seed=int(seed),
        target_time=target_time,
        objective=objective,
        max_nonresponse_rate=float(max_nonresponse_rate),
        source_version=str((payload.get("source") or {}).get(
            "content_fingerprint") or ""),
        class_dictionary_version=str(payload.get("class_dictionary", "")),
        total_area=float(payload["total_area"]),
        notes=notes,
        blind_labelling=bool(blind_labelling),
    ).with_digest()
    store.save_survey_document(owner_scope, survey_id, "design.json",
                               _design_to_payload(design, preview))
    samples = _draw_for_survey(owner_scope, survey_id, design, payload)
    store.save_survey_document(
        owner_scope, survey_id, "samples.json",
        {"survey_id": survey_id, "design_digest": design.digest,
         "drawn_at": time.time(), "samples": [s.as_dict() for s in samples]})
    await store.bump_revision(owner_scope, survey_id, None,
                              note="create_survey")
    return {
        "survey_id": survey_id,
        "design_digest": design.digest,
        "frame_digest": frame_digest,
        "allocation": design.allocation,
        "backup_per_stratum": design.backup_per_stratum,
        "sample_count": len(samples),
        "main_count": sum(design.allocation.values()),
        "preview": preview,
    }


def _design_to_payload(design: SurveyDesign, preview: Dict[str, Any]) -> Dict[str, Any]:
    out = design.canonical_payload()
    out["digest"] = design.digest
    out["weights_W"] = preview["weights_W"]
    out["created_at"] = time.time()
    return out


def _load_design(owner_scope: Dict[str, str], survey_id: str) -> SurveyDesign:
    doc = store.get_survey_document(owner_scope, survey_id, "design.json")
    if doc is None:
        raise SurveySamplingError(
            "SURVEY_NOT_FOUND", f"survey {survey_id} not found",
            status_code=404)
    return SurveyDesign(
        design_id=survey_id,
        design_type=doc["design_type"],
        frame_ref=doc["frame_ref"],
        frame_crs=doc["frame_crs"],
        strata=[
            StratumSpec(stratum_id=s["stratum_id"], N_units=s["N_units"],
                        area=s["area"])
            for s in doc["strata"]
        ],
        allocation=dict(doc["allocation"]),
        backup_per_stratum=int(doc["backup_per_stratum"]),
        seed=int(doc["seed"]),
        target_time=doc["target_time"],
        objective=doc["objective"],
        max_nonresponse_rate=float(doc["max_nonresponse_rate"]),
        source_version=doc["source_version"],
        class_dictionary_version=doc["class_dictionary_version"],
        total_area=float(doc["total_area"]),
        notes=doc.get("notes", ""),
        digest=doc.get("digest", ""),
        blind_labelling=bool(doc.get("blind_labelling", True)),
    )


def _draw_for_survey(
    owner_scope: Dict[str, str], survey_id: str,
    design: SurveyDesign, frame_payload: Dict[str, Any],
) -> List[sel_lib.SampleUnit]:
    """重建索引数组并选样（确定性；源指纹已由调用方核对）。"""
    from app.lib.geo_raster.reader import RasterReader

    src = frame_payload.get("source") or {}
    class_dictionary = {
        int(v): name
        for v, name in (frame_payload.get("class_dictionary") or {}).items()
    }
    with RasterReader.open(src["uri"]) as reader:
        frame = build_frame(
            reader,
            band=int(src.get("band", 1)),
            class_dictionary=class_dictionary,
            nodata=src.get("nodata"),
            crs=str(frame_payload.get("crs") or ""),
        )
    if frame.digest != frame_payload["frame_digest"]:
        raise SurveySamplingError(
            "SOURCE_DRIFTED",
            "classification raster changed since frame was frozen "
            "(frame digest mismatch) — rebuild the frame",
            status_code=409,
            correction_hint="re-run survey_frame_preview on the current "
                            "raster and create a new survey (history is kept)")
    return sel_lib.draw_samples(design, frame)


# ── 调查记录（盲标 / CAS / append-only） ────────────────────────────

async def record_reference(
    owner_scope: Dict[str, str],
    survey_id: str,
    *,
    sample_id: str,
    status: str,
    reference_class: Optional[str] = None,
    evidence_type: str = "other",
    evidence_note: str = "",
    quality: str = "medium",
    interpreter: str = "",
    observed_at: str = "",
    note: str = "",
    actor: str = "user",
    expected_revision: Optional[int] = None,
) -> Dict[str, Any]:
    """记录一条调查观测（append-only；同 sample_id 版本递增）。

    盲标约束：响应**不回显**预测类/层名（design.blind_labelling）。
    状态/词表校验失败 → RECORD_INVALID；revision 不符 → 409 冲突。
    """
    design = _load_design(owner_scope, survey_id)
    samples_doc = store.get_survey_document(owner_scope, survey_id,
                                            "samples.json") or {}
    stratum_id = _sample_stratum(samples_doc, sample_id)
    version = _next_version(owner_scope, survey_id, sample_id)
    from app.services.survey_sampling.contracts import ReferenceRecord

    rec = ReferenceRecord(
        survey_id=survey_id, sample_id=sample_id, stratum_id=stratum_id,
        status=status, reference_class=reference_class, version=version,
        evidence_type=evidence_type, evidence_note=evidence_note,
        quality=quality, interpreter=interpreter, observed_at=observed_at,
        note=note, actor=actor)
    try:
        rec.model_validate_strict()
    except ValueError as exc:
        raise SurveySamplingError(
            "RECORD_INVALID", str(exc), field="status") from exc
    if status == REC_LABELLED:
        class_dict = design.class_dictionary_version
        # reference_class 必须在冻结类别字典内（类字典漂移阻断）
        names = set(_class_names(class_dict))
        if reference_class not in names:
            raise SurveySamplingError(
                "CLASS_DICTIONARY_DRIFT",
                f"reference class {reference_class!r} outside frozen class "
                "dictionary — adjudicate against the design's dictionary or "
                "create a new survey",
                field="reference_class")
    await store.append_records(owner_scope, survey_id, [rec.model_dump()])
    new_revision = await store.bump_revision(
        owner_scope, survey_id, expected_revision, note=f"record {sample_id}")
    return {
        "recorded": True,
        "sample_id": sample_id,
        "version": version,
        "revision": new_revision,
        # 盲标：不回显 stratum_id（预测类）——解释员不可见
        "blind": design.blind_labelling,
    }


def _class_names(class_dictionary_version: str) -> List[str]:
    """design 冻结的类别字典（repr 格式）→ 类名列表（稳定解析）。"""
    import ast

    try:
        data = ast.literal_eval(class_dictionary_version)
    except (ValueError, SyntaxError):
        return []
    return [str(v) for v in data.values()] if isinstance(data, dict) else []


def _sample_stratum(samples_doc: Dict[str, Any], sample_id: str) -> str:
    for s in samples_doc.get("samples", []):
        if s.get("sample_id") == sample_id:
            return str(s.get("stratum_id"))
    raise SurveySamplingError(
        "SAMPLE_NOT_FOUND", f"sample {sample_id} not found in this survey",
        status_code=404)


def _next_version(
    owner_scope: Dict[str, str], survey_id: str, sample_id: str,
) -> int:
    max_v = 0
    for rec in store.load_records(owner_scope, survey_id):
        if rec.get("sample_id") == sample_id:
            max_v = max(max_v, int(rec.get("version", 0)))
    return max_v + 1


async def import_records(
    owner_scope: Dict[str, str], survey_id: str,
    rows: List[Dict[str, Any]], *,
    actor: str = "import",
    expected_revision: Optional[int] = None,
) -> Dict[str, Any]:
    """批量导入参考表（按 sample_id 精确关联；冲突显式处置）。

    冲突规则：同 sample_id 已有可用标签且**参考类不同** → 该行以
    disputed 状态落库（保留双方），绝不静默覆盖；同类重复 → 幂等跳过。
    """
    if not rows:
        raise SurveySamplingError("RECORD_INVALID", "empty import batch",
                                  field="rows")
    if len(rows) > MAX_RECORD_BATCH:
        raise SurveySamplingError(
            "BUDGET_EXCEEDED",
            f"import batch {len(rows)} exceeds {MAX_RECORD_BATCH}",
            status_code=413)
    design = _load_design(owner_scope, survey_id)
    samples_doc = store.get_survey_document(owner_scope, survey_id,
                                            "samples.json") or {}
    known_ids = {s["sample_id"]: s["stratum_id"]
                 for s in samples_doc.get("samples", [])}
    names = set(_class_names(design.class_dictionary_version))
    effective = effective_labels(owner_scope, survey_id)
    appended: List[Dict[str, Any]] = []
    skipped_same = 0
    conflicts = 0
    for row in rows:
        sid = str(row.get("sample_id") or "")
        if sid not in known_ids:
            raise SurveySamplingError(
                "SAMPLE_NOT_FOUND",
                f"import row sample_id {sid!r} not in this survey",
                field="rows", status_code=404)
        rclass = row.get("reference_class")
        if rclass is not None and str(rclass) not in names:
            raise SurveySamplingError(
                "CLASS_DICTIONARY_DRIFT",
                f"import reference_class {rclass!r} outside frozen dictionary",
                field="rows")
        status = str(row.get("status") or REC_LABELLED)
        if status not in RECORD_STATUSES:
            raise SurveySamplingError(
                "RECORD_INVALID", f"import status {status!r} outside "
                "closed vocabulary", field="rows")
        prev = effective.get(sid)
        if prev and prev.get("status") == REC_LABELLED:
            if prev.get("reference_class") == rclass and status == REC_LABELLED:
                skipped_same += 1
                continue
            conflicts += 1
            appended.append(_import_row(survey_id, sid, known_ids[sid],
                                        "disputed", None, row, actor,
                                        _next_version(owner_scope, survey_id,
                                                      sid)))
            continue
        if status == REC_LABELLED and rclass is None:
            raise SurveySamplingError(
                "RECORD_INVALID",
                f"import row {sid}: status labelled requires reference_class",
                field="rows")
        appended.append(_import_row(survey_id, sid, known_ids[sid], status,
                                    rclass, row, actor,
                                    _next_version(owner_scope, survey_id, sid)))
    if appended:
        await store.append_records(owner_scope, survey_id, appended)
    new_revision = await store.bump_revision(
        owner_scope, survey_id, expected_revision, note="import_records")
    return {
        "imported": len(appended),
        "skipped_same": skipped_same,
        "conflicts_disputed": conflicts,
        "revision": new_revision,
    }


def _import_row(
    survey_id: str, sample_id: str, stratum_id: str, status: str,
    reference_class: Optional[str], row: Dict[str, Any], actor: str,
    version: int,
) -> Dict[str, Any]:
    from app.services.survey_sampling.contracts import ReferenceRecord

    rec = ReferenceRecord(
        survey_id=survey_id, sample_id=sample_id, stratum_id=stratum_id,
        status=status, reference_class=reference_class, version=version,
        evidence_type=str(row.get("evidence_type") or "existing_survey"),
        evidence_note=str(row.get("evidence_note") or ""),
        quality=str(row.get("quality") or "medium"),
        interpreter=str(row.get("interpreter") or ""),
        observed_at=str(row.get("observed_at") or ""),
        note=str(row.get("note") or "")[:280],
        actor=actor)
    rec.model_validate_strict()
    return rec.model_dump()


def effective_labels(
    owner_scope: Dict[str, str], survey_id: str,
) -> Dict[str, Dict[str, Any]]:
    """sample_id → 当前生效记录（最高版本；历史保留于 jsonl）。"""
    out: Dict[str, Dict[str, Any]] = {}
    for rec in store.load_records(owner_scope, survey_id):
        sid = str(rec.get("sample_id"))
        cur = out.get(sid)
        if cur is None or int(rec.get("version", 0)) > int(cur.get("version", 0)):
            out[sid] = rec
    return out


async def adjudicate(
    owner_scope: Dict[str, str], survey_id: str,
    *,
    sample_id: str, reference_class: str,
    actor: str = "user",
    expected_revision: Optional[int] = None,
    note: str = "",
) -> Dict[str, Any]:
    """争议裁定：落一条 labelled 终版记录（旧版本保留可追溯）。"""
    return await record_reference(
        owner_scope, survey_id, sample_id=sample_id, status=REC_LABELLED,
        reference_class=reference_class, evidence_type="other",
        quality="high", interpreter="", note=note or "adjudicated",
        actor=actor, expected_revision=expected_revision)


# ── 进度（调查状态可见性） ──────────────────────────────────────────

def survey_progress(owner_scope: Dict[str, str], survey_id: str) -> Dict[str, Any]:
    design = _load_design(owner_scope, survey_id)
    samples_doc = store.get_survey_document(owner_scope, survey_id,
                                            "samples.json") or {}
    effective = effective_labels(owner_scope, survey_id)
    per_stratum: Dict[str, Dict[str, int]] = {}
    for s in samples_doc.get("samples", []):
        sid_h = str(s["stratum_id"])
        bucket = per_stratum.setdefault(sid_h, {
            "planned": 0, "main": 0, "backup": 0,
            "labelled": 0, "nonusable": 0, "unvisited": 0})
        if s.get("is_backup"):
            bucket["backup"] += 1
        else:
            bucket["main"] += 1
            bucket["planned"] += 1
        rec = effective.get(str(s["sample_id"]))
        if rec is None:
            bucket["unvisited"] += 1
        elif rec.get("status") == REC_LABELLED and not s.get("is_backup"):
            bucket["labelled"] += 1
        elif rec.get("status") in NON_USABLE_STATUSES and not s.get("is_backup"):
            bucket["nonusable"] += 1
    for sid_h, bucket in per_stratum.items():
        bucket["response_rate"] = (
            round(bucket["labelled"] / bucket["planned"], 4)
            if bucket["planned"] else 0.0)
    total_planned = sum(b["planned"] for b in per_stratum.values())
    total_labelled = sum(b["labelled"] for b in per_stratum.values())
    return {
        "survey_id": survey_id,
        "design_digest": design.digest,
        "per_stratum": per_stratum,
        "total_planned": total_planned,
        "total_labelled": total_labelled,
        "overall_response_rate": (
            round(total_labelled / total_planned, 4) if total_planned else 0.0),
        "can_estimate": total_planned > 0 and total_labelled >= MIN_N_PER_STRATUM_HARD,
    }


# ── 估计（设计一致；源/类字典漂移 → stale） ─────────────────────────

async def estimate_survey(
    owner_scope: Dict[str, str], survey_id: str, *,
    actor: str = "user",
) -> Dict[str, Any]:
    design = _load_design(owner_scope, survey_id)
    frame_payload = store.get_frame_payload(owner_scope, design.frame_ref)
    if frame_payload is None:
        raise SurveySamplingError(
            "FRAME_NOT_FOUND",
            f"frame {design.frame_ref} missing for survey {survey_id}",
            status_code=404)
    _verify_source_current(owner_scope, survey_id, frame_payload)

    samples_doc = store.get_survey_document(owner_scope, survey_id,
                                            "samples.json") or {}
    counts = _assemble_counts(design, samples_doc,
                              effective_labels(owner_scope, survey_id))
    try:
        result = est_lib.stratified_estimate(
            counts, total_area=float(design.total_area))
    except _LibNotEvaluable as exc:
        raise _map_lib_error(exc) from exc
    estimate_id = uuid.uuid4().hex[:12]
    payload = result.as_dict()
    payload.update({
        "estimate_id": estimate_id,
        "survey_id": survey_id,
        "design_digest": design.digest,
        "frame_digest": design.frame_ref,
        "source_fingerprint": design.source_version,
        "estimator_version": ESTIMATOR_VERSION,
        "actor": actor,
        "created_at": time.time(),
        "provenance": {
            "design_digest": design.digest,
            "frame_digest": design.frame_ref,
            "frame_total_area": design.total_area,
            "max_nonresponse_rate": design.max_nonresponse_rate,
            "seed": design.seed,
            "blind_labelling": design.blind_labelling,
        },
    })
    store.save_estimate(owner_scope, survey_id, estimate_id, payload)
    return payload


def _verify_source_current(
    owner_scope: Dict[str, str], survey_id: str,
    frame_payload: Dict[str, Any],
) -> None:
    """源漂移检查（journey 2）：fingerprint 不符 ⇒ stale 拒绝。"""
    src = frame_payload.get("source") or {}
    uri = src.get("uri")
    expected_fp = src.get("content_fingerprint")
    if not uri or not expected_fp:
        return
    from app.lib.geo_raster.reader import RasterReader

    try:
        with RasterReader.open(str(uri)) as reader:
            current = reader.metadata().fingerprint
    except Exception as exc:                          # noqa: BLE001
        raise SurveySamplingError(
            "SOURCE_UNAVAILABLE",
            f"frame source {uri!r} unavailable for drift check",
            status_code=404) from exc
    if current != expected_fp:
        raise SurveySamplingError(
            "STALE_ESTIMATE",
            "classification raster changed since the survey frame was "
            "frozen — estimates on the old frame are stale",
            status_code=409,
            details={"frame_fingerprint": expected_fp,
                     "current_fingerprint": current},
            correction_hint="rebuild the frame and create a new survey "
                            "(history is kept), or restore the original "
                            "raster version")


def _assemble_counts(
    design: SurveyDesign, samples_doc: Dict[str, Any],
    effective: Dict[str, Dict[str, Any]],
) -> est_lib.StratifiedCounts:
    """分析集组装：主样本按 draw_rank；非响应 → 预抽替补按序顶替。

    - 主样本有可用标签 → 计入 counts[h][ref]；
    - 主样本非响应/不可判/拒绝 → 原因披露，替补（同层、rank 顺序、
      有可用标签）逐位顶替；
    - 替补率超设计声明上限 ⇒ 拒绝总体推断（not_evaluated）；
    - 响应缺口（无替补可顶）超上限 ⇒ 同上。
    """
    strata = [s.stratum_id for s in design.strata]
    ref_classes = _class_names(design.class_dictionary_version)
    H = len(strata)
    R = len(ref_classes)
    ref_index = {c: k for k, c in enumerate(ref_classes)}
    counts = np.zeros((H, R), dtype=np.int64)
    excluded = np.zeros(H, dtype=np.int64)
    reasons: Dict[str, int] = {}
    substitution_rate = {s: 0.0 for s in strata}
    shortfall_rate = {s: 0.0 for s in strata}

    by_stratum: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    for s in samples_doc.get("samples", []):
        b = by_stratum.setdefault(str(s["stratum_id"]),
                                  {"main": [], "backup": []})
        b["backup" if s.get("is_backup") else "main"].append(s)
    for bucket in by_stratum.values():
        bucket["main"].sort(key=lambda x: x["draw_rank"])
        bucket["backup"].sort(key=lambda x: x["draw_rank"])

    W = design_weights(design.strata)
    N = np.array([s.N_units for s in design.strata], dtype=np.float64)
    n_planned = np.array([int(design.allocation[s]) for s in strata],
                         dtype=np.int64)

    for h, sid_h in enumerate(strata):
        bucket = by_stratum.get(sid_h, {"main": [], "backup": []})
        backups = list(bucket["backup"])
        backup_i = 0
        nonresponse = 0
        for main in bucket["main"]:
            rec = effective.get(str(main["sample_id"]))
            if rec is not None and rec.get("status") == REC_LABELLED:
                counts[h, ref_index[str(rec["reference_class"])]] += 1
                continue
            nonresponse += 1
            reason = str(rec.get("status")) if rec else "not_visited"
            reasons[reason] = reasons.get(reason, 0) + 1
            # 顶替：下一个有可用标签的替补
            substituted = False
            while backup_i < len(backups):
                cand = backups[backup_i]
                backup_i += 1
                crec = effective.get(str(cand["sample_id"]))
                if crec is not None and crec.get("status") == REC_LABELLED:
                    counts[h, ref_index[str(crec["reference_class"])]] += 1
                    substituted = True
                    break
            if not substituted:
                excluded[h] += 1
        n_h = int(counts[h].sum())
        planned_h = int(n_planned[h])
        if planned_h > 0:
            substitution_rate[sid_h] = round(nonresponse / planned_h, 4)
            shortfall_rate[sid_h] = round((planned_h - n_h) / planned_h, 4)

    return est_lib.StratifiedCounts(
        strata=strata, ref_classes=ref_classes,
        W=W, N=N, counts=counts, n_planned=n_planned,
        excluded=excluded, excluded_reasons=reasons,
        substitution_rate=substitution_rate,
        shortfall_rate=shortfall_rate,
        max_nonresponse_rate=design.max_nonresponse_rate,
    )


def survey_detail(
    owner_scope: Dict[str, str], survey_id: str, *,
    blind: Optional[bool] = None,
) -> Dict[str, Any]:
    """调查详情；``blind``（默认=设计值）控制解释员视图是否含层名。"""
    design = _load_design(owner_scope, survey_id)
    doc = _design_to_payload(design, {
        "weights_W": {s.stratum_id: float(w) for s, w in zip(
            design.strata, design_weights(design.strata))},
    })
    samples_doc = store.get_survey_document(owner_scope, survey_id,
                                            "samples.json") or {}
    is_blind = design.blind_labelling if blind is None else bool(blind)
    samples = samples_doc.get("samples", [])
    if is_blind:
        samples = [
            {k: v for k, v in s.items()
             if k not in ("stratum_id", "unit_index")}
            for s in samples
        ]
    doc.update({
        "survey_id": survey_id,
        "samples": samples,
        "sample_count": len(samples_doc.get("samples", [])),
        "revision": store.get_revision(owner_scope, survey_id),
        "estimates": store.list_estimates(owner_scope, survey_id),
    })
    return doc


def list_surveys(owner_scope: Dict[str, str]) -> List[Dict[str, Any]]:
    return store.list_surveys(owner_scope)
