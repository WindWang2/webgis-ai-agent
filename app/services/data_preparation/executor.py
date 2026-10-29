"""准备计划执行器 —— dry-run / 幂等执行 / 收据铸造（M4；契约 §4）。

硬红线：
- **原件不可变**：输出一律写新 ref（``ref:prep-…``）；纯元数据计划
  （仅 field_mapping 步）不复制数据，output_ref = input_ref —— 语义写回
  走 descriptor 版本化，绝不覆盖原 ref 载荷；
- **幂等**：同 (session, plan_hash) 已执行 → 直接返回既有 receipt；
- **指纹闸**：执行前对账输入 descriptor 指纹；不匹配 = STALE_INPUT
  typed 拒绝（数据已替换，绝不把旧计划套在新数据上）；
- **有界**：行过滤硬闸（max_drop_frac 超限 → typed 拒绝而非静默清空）；
  协作取消（CURRENT_TOKEN，每 512 要素检查一次）；
- **谱系**：输出经 provenance.record_transformation + descriptor 铸造
  （携带用户确认角色，role_confidence=user_declared）+ artifact 登记。

质量 delta：执行前后各跑一次有界画像 + 质量检查（同 ingest 口径，
profile_features 50k 行扫描预算），issue 按 code 计数差进 receipt ——
「准备让数据变好了多少」可对账。
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field, field_validator

from app.lib.cancellation import OperationCancelled, current_token
from app.services.data_preparation.plan import (
    PreparationPlan,
    PreparationStep,
    get_plan,
    save_plan,
)
from app.services.data_preparation.vocabulary import to_semantic_roles

logger = logging.getLogger(__name__)

#: dry-run 采样上限（契约 §4：≤2000 行或全量取小者）。
PREVIEW_SAMPLE_ROWS = 2000
#: 取消检查粒度（要素数）。
_CANCEL_CHECK_EVERY = 512
#: 收据记录上限（JSONL ring）。
_MAX_RECEIPTS = 64
_RECEIPTS_FILE = "receipts.jsonl"

#: 执行错误码（closed；typed 返回，不抛裸异常）。
EXEC_ERROR_CODES = (
    "PLAN_NOT_FOUND", "PLAN_DISCARDED", "STALE_INPUT", "INPUT_REF_MISSING",
    "PAYLOAD_UNSUPPORTED", "DROP_FRACTION_EXCEEDED", "STORE_FAILED",
    "CANCELLED",
)


class PreparationReceipt(BaseModel):
    """执行收据（有界；随 prepared ref 落 artifact metadata + 本地 JSONL）。"""

    plan_id: str
    plan_hash: str
    input_ref: str
    output_ref: str
    output_fingerprint: str = ""      # dsd-v1:…（输出 descriptor 指纹）
    quality_before: Dict[str, int] = Field(default_factory=dict)   # code → count
    quality_after: Dict[str, int] = Field(default_factory=dict)
    row_before: int = 0
    row_after: int = 0
    steps_applied: Tuple[Dict[str, Any], ...] = ()
    user_confirmations: Tuple[Dict[str, str], ...] = ()  # {field, role, source}
    degraded: Tuple[str, ...] = ()   # 降级披露码（如 reproject attach-only）
    created_at: float = 0.0

    @field_validator("quality_before", "quality_after")
    @classmethod
    def _qc(cls, v: Dict[str, int]) -> Dict[str, int]:
        return {str(k)[:48]: int(x) for k, x in list(v.items())[:32]}

    @field_validator("steps_applied")
    @classmethod
    def _steps(cls, v: Tuple[Dict[str, Any], ...]) -> Tuple[Dict[str, Any], ...]:
        return tuple(dict(s) for s in v[:16])

    def to_bounded_dict(self) -> Dict[str, Any]:
        return self.model_dump(mode="json")

    @property
    def rows_dropped(self) -> int:
        return max(self.row_before - self.row_after, 0)


class ExecError(Exception):
    """typed 执行失败（code ∈ EXEC_ERROR_CODES）。"""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code if code in EXEC_ERROR_CODES else "STORE_FAILED"
        self.detail = str(detail)[:200]


# ── 纯变换（可单测；dry-run 与 execute 共用）───────────────────────────────

def _iter_positions(coordinates: Any):
    """递归展开 GeoJSON 坐标叶节点（[x, y(, z?)]）。"""
    if (isinstance(coordinates, (list, tuple)) and coordinates
            and isinstance(coordinates[0], (int, float))):
        yield coordinates
    else:
        for child in coordinates or []:
            yield from _iter_positions(child)


def _swap_positions(coordinates: Any) -> Any:
    if (isinstance(coordinates, (list, tuple)) and coordinates
            and isinstance(coordinates[0], (int, float))):
        vals = list(coordinates)
        if len(vals) >= 2:
            vals[0], vals[1] = vals[1], vals[0]
        return vals
    return [_swap_positions(c) for c in coordinates or []]


def _first_position(geometry: Optional[Dict[str, Any]]) -> Optional[List[float]]:
    if not isinstance(geometry, dict):
        return None
    coords = geometry.get("coordinates")
    for pos in _iter_positions(coords):
        return [float(pos[0]), float(pos[1])] if len(pos) >= 2 else None
    return None


def _check_cancel() -> None:
    token = current_token()
    if token is not None and token.cancelled:
        raise OperationCancelled(token.reason or "preparation cancelled",
                                 job_id=token.job_id)


def apply_steps(
    payload: Dict[str, Any],
    steps: Sequence[PreparationStep],
    *,
    max_rows: Optional[int] = None,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[str]]:
    """payload（{"geojson": FC, ...}）× 步集 → (新 payload, 步证据, 降级码)。

    纯函数；深拷贝输入（new-ref 语义硬前提）。max_rows 供 dry-run 截断采样
    （证据里如实标注 truncated）。
    """
    from app.services.data_quality.autofix import AutofixStep, apply_autofix

    new_payload = copy.deepcopy(payload)
    fc = new_payload.get("geojson")
    features = fc.get("features") if isinstance(fc, dict) else None
    if not isinstance(features, list):
        raise ExecError("PAYLOAD_UNSUPPORTED", "payload.geojson.features 不是列表")
    truncated = False
    if max_rows is not None and len(features) > max_rows:
        features = features[:max_rows]
        fc["features"] = features       # 截断副本，原件不动
        truncated = True

    evidence: List[Dict[str, Any]] = []
    degraded: List[str] = []

    for step in steps:
        _check_cancel()
        stype = step.type
        if stype == "field_mapping":
            evidence.append({"type": "field_mapping", "role": step.role,
                             "field": step.field, "source": step.source,
                             "data_changed": False})
            continue
        if stype == "axis_swap":
            swapped = 0
            for feat in features:
                _check_cancel()
                props = feat.get("properties") if isinstance(feat, dict) else None
                if isinstance(props, dict):
                    a = props.get(step.lon_field)
                    b = props.get(step.lat_field)
                    if a is not None or b is not None:
                        props[step.lon_field], props[step.lat_field] = b, a
                geom = feat.get("geometry") if isinstance(feat, dict) else None
                if isinstance(geom, dict) and geom.get("coordinates") is not None:
                    geom["coordinates"] = _swap_positions(geom["coordinates"])
                    swapped += 1
            evidence.append({"type": "axis_swap", "lon_field": step.lon_field,
                             "lat_field": step.lat_field,
                             "features_swapped": swapped, "data_changed": True})
            continue
        if stype == "filter_rows":
            kept: List[Dict[str, Any]] = []
            dropped = 0
            for feat in features:
                _check_cancel()
                geom = feat.get("geometry") if isinstance(feat, dict) else None
                if step.condition_code == "null_geometry":
                    bad = not isinstance(geom, dict) or geom.get("coordinates") in (None, [])
                else:  # impossible_coordinates
                    pos = _first_position(geom)
                    bad = pos is not None and not (
                        -180.0 <= pos[0] <= 180.0 and -90.0 <= pos[1] <= 90.0)
                if bad:
                    dropped += 1
                else:
                    kept.append(feat)
            total = max(len(features), 1)
            frac = dropped / total
            if frac > step.max_drop_frac:
                raise ExecError(
                    "DROP_FRACTION_EXCEEDED",
                    f"过滤将剔除 {dropped}/{total} 行（{frac:.1%}）超过闸值 "
                    f"{step.max_drop_frac:.1%} —— 数据面问题超出准备步语义，"
                    "请人工核验数据来源")
            features[:] = kept
            evidence.append({"type": "filter_rows",
                             "condition_code": step.condition_code,
                             "dropped": dropped, "drop_frac": round(frac, 6),
                             "data_changed": True})
            continue
        if stype == "dedup":
            seen = set()
            kept2: List[Dict[str, Any]] = []
            dropped2 = 0
            for feat in features:
                _check_cancel()
                props = feat.get("properties") if isinstance(feat, dict) else {}
                key = json.dumps(
                    [props.get(k) for k in step.key_fields],
                    ensure_ascii=False, sort_keys=True, default=str)
                if key in seen:
                    dropped2 += 1
                else:
                    seen.add(key)
                    kept2.append(feat)
            features[:] = kept2
            evidence.append({"type": "dedup", "key_fields": list(step.key_fields),
                             "dropped": dropped2, "data_changed": True})
            continue
        if stype == "rename_field":
            renamed = 0
            for feat in features:
                _check_cancel()
                props = feat.get("properties") if isinstance(feat, dict) else None
                if isinstance(props, dict) and step.from_field in props:
                    props[step.to_field] = props.pop(step.from_field)
                    renamed += 1
            evidence.append({"type": "rename_field", "from_field": step.from_field,
                             "to_field": step.to_field, "renamed": renamed,
                             "data_changed": True})
            continue
        if stype == "reproject":
            fixed_payload, ev = apply_autofix(
                new_payload,
                [AutofixStep(operation="reproject",
                             params={"target_crs": step.target_crs})],
            )
            new_payload = fixed_payload
            fc2 = new_payload.get("geojson")
            if isinstance(fc2, dict):
                features = fc2.get("features") or features
            op = (ev.get("operations") or [{}])[-1]
            if op.get("reprojected") is False and op.get("attached_crs"):
                degraded.append("REPROJECT_ATTACH_CRS_ONLY")
            evidence.append({"type": "reproject", "target_crs": step.target_crs,
                             "reprojected": bool(op.get("reprojected")),
                             "data_changed": True})
            continue
        raise ExecError("PAYLOAD_UNSUPPORTED", f"unknown step {stype}")

    if truncated:
        evidence.append({"type": "preview_truncation", "rows": len(features),
                         "data_changed": False})
    return new_payload, evidence, degraded


# ── 质量计数（执行前后 delta 的口径）──────────────────────────────────────

def _quality_counts(payload: Dict[str, Any]) -> Tuple[Dict[str, int], int]:
    """payload → (issue code 计数, 行数)；有界口径与 ingest 相同。"""
    from app.lib.data.profile import DatasetProfileV3, profile_features
    from app.lib.data.quality import run_quality_checks

    fc = payload.get("geojson") if isinstance(payload, dict) else None
    features = fc.get("features") if isinstance(fc, dict) else []
    features = features if isinstance(features, list) else []
    declared = ""
    if isinstance(payload, dict):
        declared = str(payload.get("crs") or "")
        if not declared and isinstance(fc, dict) and isinstance(fc.get("crs"), dict):
            declared = str(fc["crs"].get("properties", {}).get("name") or "")
    try:
        vp, _q = profile_features(features, crs=declared)
        profile = DatasetProfileV3(
            target_ref="", category="vector", crs=declared,
            extent=vp.extent, vector=vp)
        report = run_quality_checks(profile)
        counts: Dict[str, int] = {}
        for issue in report.issues:
            counts[issue.code.value] = counts.get(issue.code.value, 0) + 1
        return counts, len(features)
    except Exception:  # noqa: BLE001 — 质量计数是披露面，失败诚实空
        return {}, len(features)


# ── dry-run / execute ──────────────────────────────────────────────────────

def dry_run(
    payload: Dict[str, Any],
    plan: PreparationPlan,
    *,
    sample_rows: int = PREVIEW_SAMPLE_ROWS,
) -> Dict[str, Any]:
    """采样预演（不落任何 store）：步证据 + 行数/质量预估 delta。"""
    before_counts, before_rows = _quality_counts(payload)
    preview_payload, evidence, degraded = apply_steps(
        payload, plan.steps, max_rows=sample_rows)
    after_counts, after_rows = _quality_counts(preview_payload)
    sampled = min(before_rows, sample_rows)
    return {
        "plan_id": plan.plan_id,
        "sampled_rows": sampled,
        "truncated": before_rows > sample_rows,
        "rows_in_sample": sampled,
        "estimated_rows_after": (
            after_rows + (before_rows - sampled) if before_rows > sampled else after_rows),
        "quality_delta_sample": {
            code: after_counts.get(code, 0) - before_counts.get(code, 0)
            for code in sorted(set(before_counts) | set(after_counts))
            if after_counts.get(code, 0) != before_counts.get(code, 0)
        },
        "steps": evidence,
        "degraded": degraded,
    }


# ── 收据持久化（JSONL ring；plans 同目录）─────────────────────────────────

def _receipts_path(session_id: str) -> Path:
    from app.services.data_preparation.plan import _plans_base

    root = _plans_base()
    session_dir = (root / str(session_id)).resolve()
    if session_dir.parent != root:
        raise ValueError("invalid session id for preparation receipts")
    return session_dir / "data_preparation" / _RECEIPTS_FILE


def _load_receipts_sync(path: Path) -> List[PreparationReceipt]:
    if not path.exists():
        return []
    out: List[PreparationReceipt] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(PreparationReceipt.model_validate(json.loads(line)))
                except Exception:  # noqa: BLE001
                    continue
    except Exception:  # noqa: BLE001
        return []
    return out[-_MAX_RECEIPTS:]


def _append_receipt_sync(path: Path, receipt: PreparationReceipt) -> None:
    records = [r for r in _load_receipts_sync(path) if r.plan_hash != receipt.plan_hash]
    records.append(receipt)
    records = records[-_MAX_RECEIPTS:]
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r.to_bounded_dict(), ensure_ascii=False,
                                   sort_keys=True, separators=(",", ":")) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


async def find_receipt(session_id: str, plan_hash: str) -> Optional[PreparationReceipt]:
    try:
        records = await asyncio.to_thread(_load_receipts_sync, _receipts_path(session_id))
    except Exception:  # noqa: BLE001
        return None
    for r in reversed(records):
        if r.plan_hash == plan_hash:
            return r
    return None


# ── 执行主链 ───────────────────────────────────────────────────────────────

async def execute_plan(plan: PreparationPlan) -> PreparationReceipt:
    """幂等执行计划 → receipt（原件不可变；typed 失败抛 ExecError）。"""
    from app.services.artifact_registry import register_artifact
    from app.services.dataset_semantics import (
        derive_descriptor_from_v3,
        get_dataset_semantic_store,
    )
    from app.services.dataset_semantics.provenance import record_transformation
    from app.services.session_data import session_data_manager

    # 0) 幂等：同 plan_hash 已执行 → 既有 receipt。
    existing = await find_receipt(plan.session_id, plan.plan_hash)
    if existing is not None:
        return existing

    stored = await get_plan(plan.session_id, plan.plan_id)
    if stored is None:
        raise ExecError("PLAN_NOT_FOUND", plan.plan_id)
    if stored.state == "discarded":
        raise ExecError("PLAN_DISCARDED", plan.plan_id)

    # 1) 载入 + 指纹对账（数据替换后旧计划必须拒执行）。
    payload = await session_data_manager.get(plan.session_id, plan.input_ref)
    if payload is None:
        raise ExecError("INPUT_REF_MISSING", plan.input_ref)
    rec = await get_dataset_semantic_store().get(plan.session_id, plan.input_ref)
    current_fp = rec.descriptor.descriptor_fingerprint if rec.ok and rec.descriptor else ""
    if plan.input_fingerprint and current_fp and current_fp != plan.input_fingerprint:
        raise ExecError("STALE_INPUT",
                        f"输入数据已变化（{current_fp[:24]}… ≠ 计划锚 {plan.input_fingerprint[:24]}…）")

    # 2) 执行变换（或纯元数据计划零拷贝）。
    before_counts, before_rows = _quality_counts(payload)
    user_confirmations: List[Dict[str, str]] = []
    for s in plan.steps:
        if s.type == "field_mapping":
            user_confirmations.append({"field": s.field, "role": s.role,
                                       "source": s.source})
    if plan.mutates_data:
        new_payload, evidence, degraded = apply_steps(payload, plan.steps)
        output_ref = await session_data_manager.store(
            plan.session_id, new_payload, prefix="prep")
        if not output_ref:
            raise ExecError("STORE_FAILED", "session store refused prepared payload")
    else:
        new_payload, evidence, degraded = payload, [], []
        output_ref = plan.input_ref

    after_counts, after_rows = _quality_counts(new_payload)

    # 3) 输出 descriptor 铸造（携带用户确认角色）+ 谱系 + 台账。
    fc = new_payload.get("geojson") if isinstance(new_payload, dict) else None
    features = fc.get("features") if isinstance(fc, dict) else []
    output_fingerprint = ""
    try:
        from app.lib.data.profile import DatasetProfileV3, profile_features
        from app.lib.gis.dataset_descriptor import SourceRef
        declared = str((new_payload or {}).get("crs") or "") if isinstance(new_payload, dict) else ""
        vp, _q = profile_features(features if isinstance(features, list) else [], crs=declared)
        out_profile = DatasetProfileV3(
            target_ref=output_ref, category="vector", crs=declared,
            extent=vp.extent, vector=vp)
        descriptor = derive_descriptor_from_v3(
            out_profile, dataset_key=output_ref, features=features,
            source_refs=[SourceRef(type="ref", ref=plan.input_ref,
                                   fingerprint=plan.input_fingerprint)],
            provenance=[{"producer": "data_preparation",
                         "method": f"plan:{plan.plan_hash}"}],
        )
        # 用户确认角色写回（提案角色 → SemanticFieldRole 诚实投影）。
        by_field = {c["field"]: c for c in user_confirmations}
        for entry in descriptor.fields:
            conf = by_field.get(entry.name)
            if conf is not None:
                semantic = list(to_semantic_roles(conf["role"]))
                if semantic:
                    entry.roles = semantic
                    entry.role_confidence = (
                        "user_declared" if conf["source"] == "user" else "rule_derived")
        # 角色改写后指纹必须重铸（store.put 只认预铸指纹字段）。
        descriptor = descriptor.with_fingerprints()
        put = await get_dataset_semantic_store().put(
            plan.session_id, output_ref, descriptor)
        if put.ok:
            output_fingerprint = put.fingerprint
    except Exception:  # noqa: BLE001 — additive evidence
        logger.warning("[data_preparation] output descriptor mint skipped",
                       exc_info=True)

    receipt = PreparationReceipt(
        plan_id=plan.plan_id,
        plan_hash=plan.plan_hash,
        input_ref=plan.input_ref,
        output_ref=output_ref,
        output_fingerprint=output_fingerprint[:96],
        quality_before=before_counts,
        quality_after=after_counts,
        row_before=before_rows,
        row_after=after_rows,
        steps_applied=tuple(evidence),
        user_confirmations=tuple(user_confirmations),
        degraded=tuple(degraded),
        created_at=time.time(),
    )

    # 谱系 + 产物台账（增值面：失败只降级不阻断）。
    try:
        await record_transformation(
            plan.session_id,
            output_ref=output_ref,
            output_fingerprint=output_fingerprint,
            inputs=[{"ref": plan.input_ref,
                     "descriptor_fingerprint": plan.input_fingerprint}],
            algorithm="data_preparation",
            algorithm_version="v1",
            parameters=plan.plan_hash,
        )
    except Exception:  # noqa: BLE001
        logger.warning("[data_preparation] provenance record skipped", exc_info=True)
    try:
        await register_artifact(
            plan.session_id,
            artifact_id=output_ref,
            artifact_type="dataset",
            producer_tool="prepare_dataset",
            inputs=[plan.input_ref],
            metadata={
                "preparation_plan_id": plan.plan_id,
                "preparation_plan_hash": plan.plan_hash,
                "preparation_degraded": list(degraded),
                "quality_delta": {
                    k: after_counts.get(k, 0) - before_counts.get(k, 0)
                    for k in sorted(set(before_counts) | set(after_counts))
                    if after_counts.get(k, 0) != before_counts.get(k, 0)},
            },
        )
    except Exception:  # noqa: BLE001
        logger.warning("[data_preparation] artifact register skipped", exc_info=True)

    # 4) 状态迁移 + 收据落盘（幂等键供重入查询）。
    plan.state = "executed"
    await save_plan(plan)
    try:
        await asyncio.to_thread(_append_receipt_sync, _receipts_path(plan.session_id),
                                receipt)
    except Exception:  # noqa: BLE001 — 收据落盘失败不否定已完成的执行
        logger.warning("[data_preparation] receipt persist skipped", exc_info=True)
    return receipt
