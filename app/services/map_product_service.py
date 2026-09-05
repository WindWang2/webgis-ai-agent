"""Map Product Versioning (ADR-0092 A6).

A project's map product is versioned every time its substantive state lands:
the artifact set of a workflow run plus the MapSpec generation it finalized
against. Each version records a machine-readable diff against the previous
one across five dimensions:

    data_changed       — input dataset fingerprints moved
    algorithm_changed  — per-step resolved algorithms moved (registry re-resolution)
    parameter_changed  — step args moved
    style_changed      — MapSpec fingerprint moved while the compute plan didn't
    output_changed     — artifact content fingerprints moved

``style_changed`` without the others is the machine-readable proof of the
"style-only change ⇒ no analysis re-computation" contract; ``data_changed``
is the trigger that *must* invalidate descendants on the next rerun.

Provenance is always COMPUTED server-side from run manifests — the REST
schema deliberately offers no client-supplied fingerprint/diff fields (a
forged diff_summary would let an LLM assert false provenance into the
durable ledger).
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.project import MapProductVersion, WorkflowRun
from app.services.provenance.fingerprint import canonical_dumps

logger = logging.getLogger(__name__)

#: Bounded per-step compute-plan projection (steps capped, args trimmed by the
#: manifest builder before landing here).
_MAX_PLAN_STEPS = 64


def _sha256(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _project_steps(
    manifest: Dict[str, Any], *, include_args: bool = False, sort: bool = False
) -> List[Dict[str, Any]]:
    """Single per-step manifest projection (fingerprint / compute-plan / diff
    all share this — four hand-rolled projections used to drift apart)."""
    plan: List[Dict[str, Any]] = []
    for s in (manifest.get("steps") or [])[:_MAX_PLAN_STEPS]:
        if not isinstance(s, dict):
            continue
        row: Dict[str, Any] = {
            "step_id": s.get("step_id"),
            "capability": s.get("capability"),
            "algorithm": s.get("algorithm"),
            "tool_name": s.get("tool_name"),
        }
        if include_args:
            row["args"] = s.get("args") or {}
        plan.append(row)
    if sort:
        plan.sort(key=lambda r: str(r.get("step_id") or ""))
    return plan


def compute_product_fingerprint(
    *,
    input_dataset_fingerprints: Optional[Dict[str, str]],
    run_manifest: Optional[Dict[str, Any]],
    mapspec_fingerprint: Optional[str],
    artifact_fingerprints: List[str],
) -> str:
    """Deterministic product identity over its substantive inputs/outputs."""
    payload = {
        "inputs": dict(sorted((input_dataset_fingerprints or {}).items())),
        "compute_plan": _project_steps(run_manifest or {}, sort=True),
        "mapspec": mapspec_fingerprint or "",
        "outputs": sorted(f for f in artifact_fingerprints if f),
    }
    return _sha256(canonical_dumps(payload))


class MapProductService:
    # ── Lifecycle V2（ADR-0099）lineage 词表 ──────────────────────────
    LINEAGE_KINDS = ("linear", "fork", "restore", "merge", "rerun", "auto")

    @staticmethod
    def record_version(
        db: Session,
        project_id: str,
        *,
        workflow_run_id: Optional[str] = None,
        mapspec_fingerprint: Optional[str] = None,
        mapspec_revision: Optional[int] = None,
        recipe_id: Optional[str] = None,
        artifact_ids: Optional[List[str]] = None,
        input_dataset_fingerprints: Optional[Dict[str, str]] = None,
        run_manifest: Optional[Dict[str, Any]] = None,
        mapspec_snapshot: Optional[Dict[str, Any]] = None,
        label: Optional[str] = None,
        actor: Optional[str] = None,
        parent_version_no: Optional[int] = None,
        lineage_kind: Optional[str] = None,
        artifact_fingerprints: Optional[List[str]] = None,
    ) -> MapProductVersion:
        """Append one product version (per-project monotonic version_no).

        Fingerprint and diff are ALWAYS computed here from the run manifest +
        previous version (no client-supplied provenance). An identical
        fingerprint to the previous version is still recorded as a new row
        (the timeline is evidence); the diff simply reports no changes.
        ``run_manifest`` may be supplied directly for versions not bound to a
        stored run; otherwise it is read from the referenced run row.
        """
        run: Optional[WorkflowRun] = None
        if workflow_run_id:
            # Project-scoped lookup (fail-closed, mirrors _load_and_authorize_run):
            # a foreign project's run must never feed this project's ledger.
            run = db.execute(
                select(WorkflowRun).where(
                    WorkflowRun.id == workflow_run_id,
                    WorkflowRun.project_id == project_id,
                )
            ).scalar_one_or_none()
            if run is None:
                raise ValueError(f"WorkflowRun {workflow_run_id} not found in project {project_id}")

        manifest = run_manifest if run_manifest is not None else (
            (run.run_manifest if run else None) or {}
        )
        artifact_ids = list(artifact_ids or [])
        if not artifact_ids and run:
            artifact_ids = [
                str(a.get("id"))
                for a in (manifest.get("artifacts") or [])
                if a.get("id")
            ]
        if artifact_fingerprints is not None:
            # Lifecycle rows (fork/restore/merge)：显式继承来源侧产物指纹 ——
            # manifest 重提取会得到空集，diff 会谎报 output_changed。
            fingerprints = [str(f) for f in artifact_fingerprints if f]
        else:
            fingerprints = [
                str(a.get("content_fingerprint"))
                for a in (manifest.get("artifacts") or [])
                if a.get("content_fingerprint")
            ]
        input_fps = dict(input_dataset_fingerprints or {})
        if not input_fps and run:
            input_fps = dict(run.input_dataset_fingerprints or {})

        previous = db.execute(
            select(MapProductVersion)
            .where(MapProductVersion.project_id == project_id)
            .order_by(MapProductVersion.version_no.desc())
        ).scalars().first()

        product_fingerprint = compute_product_fingerprint(
            input_dataset_fingerprints=input_fps,
            run_manifest=manifest,
            mapspec_fingerprint=mapspec_fingerprint,
            artifact_fingerprints=fingerprints,
        )
        diff_summary = MapProductService.diff_versions(previous, {
            "input_dataset_fingerprints": input_fps,
            "run_manifest": manifest,
            "mapspec_fingerprint": mapspec_fingerprint,
            "artifact_fingerprints": fingerprints,
        })

        # review M-C3：并发写者（lifecycle 路由线程池 + 引擎 auto-record
        # 线程）会在 SELECT-max 与 INSERT 之间撞 uq_map_product_version ——
        # 重读 max 重试（有界 3 次），不让证据行因竞态而丢失或 500。
        last_err: Optional[Exception] = None
        for attempt in range(3):
            if attempt > 0:
                db.rollback()
                previous = db.execute(
                    select(MapProductVersion)
                    .where(MapProductVersion.project_id == project_id)
                    .order_by(MapProductVersion.version_no.desc())
                ).scalars().first()
            version_no = (previous.version_no if previous else 0) + 1
            row = MapProductVersion(
                project_id=project_id,
                version_no=version_no,
            product_fingerprint=product_fingerprint,
            input_dataset_fingerprints=input_fps,
            compute_plan=_project_steps(manifest, include_args=True),
            output_fingerprints=sorted(f for f in fingerprints if f)[:128],
            workflow_id=str(run.workflow_id) if run else None,
            workflow_run_id=workflow_run_id,
            mapspec_fingerprint=mapspec_fingerprint,
            mapspec_revision=mapspec_revision,
            recipe_id=recipe_id,
            artifact_ids=artifact_ids[:128],
            diff_summary=diff_summary,
                mapspec_snapshot=mapspec_snapshot,
                label=label,
                actor=actor,
                parent_version_no=parent_version_no,
                lineage_kind=lineage_kind,
            )
            try:
                db.add(row)
                db.commit()
                db.refresh(row)
                return row
            except IntegrityError:
                last_err = None
                continue
        # 重试耗尽：并发同号插入仍冲突 —— 按幂等语义重查（同 run+指纹的
        # auto-record 竞态对手可能已落行）。
        db.rollback()
        existing = db.execute(
            select(MapProductVersion).where(
                MapProductVersion.project_id == project_id,
                MapProductVersion.version_no == version_no,
            )
        ).scalars().first()
        if existing is not None:
            return existing
        raise last_err or RuntimeError(
            f"map product version insert failed after retries (project {project_id})"
        )

    # ── Lifecycle V2（ADR-0099）────────────────────────────────────────
    # 语义：版本行不可变，所有生命周期操作都是**新增行**（append-only 证据）。
    # open = 只读检视（不落任何状态）；restore/fork/merge/rerun = 新行 +
    # lineage 边；auto = run 完成后的幂等自动记录。绝无 "改历史"。

    @staticmethod
    def maybe_auto_record_version(
        db: Session,
        run: WorkflowRun,
        *,
        mapspec_snapshot: Optional[Dict[str, Any]] = None,
        label: Optional[str] = None,
    ) -> Optional[MapProductVersion]:
        """Run 完成后的幂等自动记录（ADR-0099 auto-record）。

        同一 (workflow_run_id, product_fingerprint) 已有行 → 返回既有行，
        绝不重复记录。快照 best-effort：session 缺席/过期 → 行仍记录，
        snapshot 为空（诚实的 open 降级）。
        """
        if run.status != "completed" or not run.project_id:
            return None
        manifest = run.run_manifest or {}
        artifacts = manifest.get("artifacts") or []
        fingerprints = [
            str(a.get("content_fingerprint")) for a in artifacts
            if a.get("content_fingerprint")
        ]
        mapspec_fp = None
        try:
            outcome = (manifest.get("outcome") or {})
            mapspec_fp = outcome.get("mapspec_fingerprint") if isinstance(outcome, dict) else None
        except Exception:  # noqa: BLE001
            mapspec_fp = None
        product_fingerprint = compute_product_fingerprint(
            input_dataset_fingerprints=run.input_dataset_fingerprints or {},
            run_manifest=manifest,
            mapspec_fingerprint=mapspec_fp,
            artifact_fingerprints=fingerprints,
        )
        existing = db.execute(
            select(MapProductVersion).where(
                MapProductVersion.project_id == run.project_id,
                MapProductVersion.workflow_run_id == run.id,
                MapProductVersion.product_fingerprint == product_fingerprint,
            )
        ).scalars().first()
        if existing is not None:
            return existing
        return MapProductService.record_version(
            db,
            run.project_id,
            workflow_run_id=run.id,
            mapspec_fingerprint=mapspec_fp,
            recipe_id=(manifest.get("product") or {}).get("recipe_id")
            if isinstance(manifest.get("product"), dict) else None,
            input_dataset_fingerprints=run.input_dataset_fingerprints or {},
            mapspec_snapshot=mapspec_snapshot,
            label=label,
            actor="system:auto-record",
            lineage_kind="auto",
        )

    @staticmethod
    def open_version(
        db: Session, project_id: str, version_no: int
    ) -> Dict[str, Any]:
        """只读检视一个历史版本（绝不触碰当前会话状态）。

        返回版本事实 + 能力披露：snapshot 是否在场、哪些恢复模式可用、
        provenance 摘要（inputs/plan/artifacts）。open 永远成功（版本存在
        即可），能力不足只在 `restore_modes` 里如实降级。
        """
        row = MapProductService.get_version(db, project_id, version_no)
        if row is None:
            raise ValueError(f"map product version not found: {version_no}")

        has_snapshot = isinstance(row.mapspec_snapshot, dict) and bool(
            row.mapspec_snapshot
        )
        has_run = bool(row.workflow_run_id)
        restore_modes: List[Dict[str, Any]] = []
        if has_snapshot:
            restore_modes.append({
                "mode": "style_only",
                "available": True,
                "note": "presentation state from the version snapshot; no analysis recompute",
            })
        else:
            restore_modes.append({
                "mode": "style_only",
                "available": False,
                "note": "version predates snapshots (migration 0024); compare-only",
            })
        restore_modes.append({
            "mode": "full",
            "available": has_run,
            "note": (
                "re-executes the bound workflow run (fresh artifacts, input "
                "drift disclosed) and records a new version"
                if has_run else
                "no workflow run bound to this version — full restore unavailable"
            ),
        })
        plan = list(row.compute_plan or [])
        return {
            "version_no": row.version_no,
            "product_fingerprint": row.product_fingerprint,
            "recipe_id": row.recipe_id,
            "workflow_run_id": row.workflow_run_id,
            "mapspec_fingerprint": row.mapspec_fingerprint,
            "mapspec_revision": row.mapspec_revision,
            "lineage_kind": row.lineage_kind,
            "parent_version_no": row.parent_version_no,
            "label": row.label,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "diff_summary": row.diff_summary,
            "snapshot_available": has_snapshot,
            "restore_modes": restore_modes,
            "provenance": {
                "input_dataset_fingerprints": row.input_dataset_fingerprints or {},
                "plan_steps": len(plan),
                "artifact_count": len(row.artifact_ids or []),
                "output_fingerprints": len(row.output_fingerprints or []),
            },
        }

    @staticmethod
    async def restore_style_to_session(
        db: Session,
        project_id: str,
        version_no: int,
        *,
        session_id: str,
        actor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """style-only restore：版本快照的表达面 → 活会话（ADR-0099）。

        数据/计算不动 —— 五维 diff 的 style-only 契约在此是操作语义：
        恢复后记录的新版本 diff 只含 style 维（机器可证）。走
        RestoreStyleIntent（apply_mutation 的锁/CAS/校验/回滚事务）。
        """
        row = MapProductService.get_version(db, project_id, version_no)
        if row is None:
            raise ValueError(f"map product version not found: {version_no}")
        if not (isinstance(row.mapspec_snapshot, dict) and row.mapspec_snapshot):
            raise ValueError(
                f"version {version_no} has no mapspec snapshot — style restore "
                "unavailable (compare-only)"
            )

        from app.services.mapspec.lifecycle_engine import (
            MapSpecLifecycleEngine,
            RestoreStyleIntent,
        )

        # review M-Adv3：悬空 run 绑定（workflow 级联删除 run）会让恢复在
        # 「会话已变、账本未记」的不诚实点失败 —— 先探测，悬空则恢复行不
        # 绑 run（计算身份仍从来源版本行继承，append-only 语义不变）。
        restore_run_id = row.workflow_run_id
        if restore_run_id is not None:
            from app.models.project import WorkflowRun

            run_alive = db.execute(
                select(WorkflowRun.id).where(
                    WorkflowRun.id == restore_run_id,
                    WorkflowRun.project_id == project_id,
                )
            ).scalar_one_or_none()
            if run_alive is None:
                restore_run_id = None

        engine = MapSpecLifecycleEngine()
        result = await engine.apply_mutation(
            session_id,
            RestoreStyleIntent(snapshot=row.mapspec_snapshot),
            origin="system",
        )
        if getattr(result, "is_error", False):
            raise ValueError(result.error_msg or "style restore mutation rejected")

        # 恢复本身是新的版本证据（append-only）：谱系指向来源版本。
        # review m7：快照取恢复后的 spec（指纹与快照机器一致；来源行快照
        # 是恢复前的表达 —— 复用它会让下一次 style 恢复复活旧样式）。
        import copy as _copy

        restored_snapshot = (
            _copy.deepcopy(result.mapspec)
            if getattr(result, "mapspec", None)
            else _copy.deepcopy(row.mapspec_snapshot or {})
        )
        new_row = MapProductService.record_version(
            db,
            project_id,
            workflow_run_id=restore_run_id,
            mapspec_fingerprint=result.mapspec_fingerprint or row.mapspec_fingerprint,
            mapspec_revision=getattr(result, "mutation_revision", None),
            recipe_id=row.recipe_id,
            input_dataset_fingerprints=row.input_dataset_fingerprints,
            run_manifest={"steps": list(row.compute_plan or [])},
            mapspec_snapshot=restored_snapshot,
            artifact_ids=list(row.artifact_ids or []),
            artifact_fingerprints=list(row.output_fingerprints or []),
            label=f"restore-style from V{version_no}",
            actor=actor or "user",
            parent_version_no=version_no,
            lineage_kind="restore",
        )
        return {
            "restored_version_no": new_row.version_no,
            "source_version_no": version_no,
            "mode": "style_only",
            "mutation_revision": getattr(result, "mutation_revision", None),
            "warnings": list(result.warnings or []),
            # 机器证明：restore 行与**来源版本**的计算身份逐位相同（inputs
            # + compute plan 排序后相等）—— 只有表达面移动，没有任何分析
            # 执行。注意 diff_summary 是相对**账本前一行**的（若前一版本是
            # 分析迁移者，diff 会如实报告 plan 差异 —— 那是时间线事实，
            # 不是重算发生）。
            "style_only_proof": {
                "compute_identity_preserved": bool(
                    dict(new_row.input_dataset_fingerprints or {})
                    == dict(row.input_dataset_fingerprints or {})
                    and _project_steps(
                        {"steps": new_row.compute_plan or []}, sort=True)
                    == _project_steps(
                        {"steps": row.compute_plan or []}, sort=True)
                ),
                "analysis_executed": False,
                "note": (
                    "presentation restored from the version snapshot; no "
                    "workflow rerun was performed"
                ),
            },
        }

    @staticmethod
    def fork_version(
        db: Session,
        project_id: str,
        version_no: int,
        *,
        label: Optional[str] = None,
        actor: Optional[str] = None,
    ) -> MapProductVersion:
        """从历史版本开新谱系（ADR-0099 fork）。

        fork 是证据行：复制来源版本的 provenance（inputs/plan/outputs/
        snapshot），parent 指向来源，lineage_kind=fork。之后的版本按线性
        继续 —— fork 行标记分支点。不做 Git 语义伪装（无 branch 列、
        无移动指针）：谱系是 DAG 边 + 追溯，不是引用可变树。
        """
        row = MapProductService.get_version(db, project_id, version_no)
        if row is None:
            raise ValueError(f"map product version not found: {version_no}")
        return MapProductService.record_version(
            db,
            project_id,
            workflow_run_id=row.workflow_run_id,
            mapspec_fingerprint=row.mapspec_fingerprint,
            mapspec_revision=row.mapspec_revision,
            recipe_id=row.recipe_id,
            input_dataset_fingerprints=row.input_dataset_fingerprints,
            run_manifest={"steps": list(row.compute_plan or [])},
            mapspec_snapshot=dict(row.mapspec_snapshot or {})
            if row.mapspec_snapshot else None,
            artifact_ids=list(row.artifact_ids or []),
            artifact_fingerprints=list(row.output_fingerprints or []),
            label=label or f"fork of V{version_no}",
            actor=actor or "user",
            parent_version_no=version_no,
            lineage_kind="fork",
        )

    @staticmethod
    def merge_dimensions(
        db: Session,
        project_id: str,
        from_version_no: int,
        to_version_no: int,
        *,
        label: Optional[str] = None,
        actor: Optional[str] = None,
    ) -> MapProductVersion:
        """受限合并（ADR-0099 constrained merge）。

        只允许**维度不相交**的合并：一方仅样式变化（style-only），另一方
        仅分析变化（data/algorithm/parameter-only）—— 合并 = 分析侧的
        compute provenance + 样式侧的 mapspec 指纹/快照。双侧同时改样式
        或同时改分析 → 结构性冲突，如实拒绝（不静默择一）。产物是新版本
        行（append-only），parent 指向分析侧（计算身份所在），样式来源记
        入 label 语义。
        """
        diff = MapProductService.diff_versions_pairwise(
            db, project_id, from_version_no, to_version_no)
        style_changed = bool(diff.get("style_changed"))
        analysis_changed = bool(diff.get("analysis_recomputation_expected"))
        other_changed = bool(diff.get("output_changed"))

        a = MapProductService.get_version(db, project_id, from_version_no)
        b = MapProductService.get_version(db, project_id, to_version_no)
        if a is None or b is None:
            raise ValueError("map product version not found")

        style_side, analysis_side = None, None
        if style_changed and not analysis_changed:
            style_side, analysis_side = b, a
        elif analysis_changed and not style_changed:
            style_side, analysis_side = a, b
        elif not style_changed and not analysis_changed:
            raise ValueError(
                "merge refused: versions are product-identical on all five "
                "dimensions (nothing to merge)"
            )
        else:
            raise ValueError(
                "merge refused: conflicting changes — both versions moved the "
                f"same dimension(s) (style={style_changed}, "
                f"analysis={analysis_changed}); constrained merge only "
                "combines a style-only change with an analysis-only change"
            )
        if other_changed and analysis_side.output_fingerprints != style_side.output_fingerprints:
            # 输出内容随分析侧走 —— style 侧携带不同 artifact 集时它不是
            # 纯样式变化，如实拒绝。
            raise ValueError(
                "merge refused: style side carries different artifacts "
                "(output dimension moved on both sides)"
            )

        return MapProductService.record_version(
            db,
            project_id,
            workflow_run_id=analysis_side.workflow_run_id,
            mapspec_fingerprint=style_side.mapspec_fingerprint,
            mapspec_revision=style_side.mapspec_revision,
            recipe_id=analysis_side.recipe_id or style_side.recipe_id,
            input_dataset_fingerprints=analysis_side.input_dataset_fingerprints,
            run_manifest={"steps": list(analysis_side.compute_plan or [])},
            mapspec_snapshot=dict(style_side.mapspec_snapshot or {})
            if style_side.mapspec_snapshot else None,
            artifact_ids=list(analysis_side.artifact_ids or []),
            artifact_fingerprints=list(analysis_side.output_fingerprints or []),
            label=label or (
                f"merge V{from_version_no}+V{to_version_no} "
                f"(analysis V{analysis_side.version_no}, style V{style_side.version_no})"
            ),
            actor=actor or "user",
            parent_version_no=analysis_side.version_no,
            lineage_kind="merge",
        )

    @staticmethod
    def bounded_compute_plan(manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Bounded per-step compute-plan projection stored with each version."""
        return _project_steps(manifest, include_args=True)

    @staticmethod
    def diff_versions(
        previous: Optional[Any],
        current: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Five-dimension diff of a product state vs the previous one."""
        prev_inputs: Dict[str, str] = dict(
            getattr(previous, "input_dataset_fingerprints", None) or {}
        )
        curr_inputs: Dict[str, str] = dict(current.get("input_dataset_fingerprints") or {})

        # Previous compute plan comes from the stored snapshot column; the
        # current one from the (possibly inline) manifest. Both use the
        # shared projection so the diff compares like with like.
        prev_manifest: Dict[str, Any] = {
            "steps": list(getattr(previous, "compute_plan", None) or [])
        }
        curr_manifest: Dict[str, Any] = current.get("run_manifest") or {}

        def _index(manifest: Dict[str, Any], key: str) -> Dict[str, Any]:
            return {
                str(s.get("step_id")): (s.get(key) or "")
                for s in (manifest.get("steps") or [])
                if isinstance(s, dict) and s.get("step_id")
            }

        prev_algos, curr_algos = _index(prev_manifest, "algorithm"), _index(curr_manifest, "algorithm")
        prev_args, curr_args = _index(prev_manifest, "args"), _index(curr_manifest, "args")

        prev_outputs = sorted(
            str(fp)
            for fp in (getattr(previous, "output_fingerprints", None) or [])
            if fp
        )
        curr_outputs = sorted(
            str(fp) for fp in (current.get("artifact_fingerprints") or []) if fp
        )

        prev_style = getattr(previous, "mapspec_fingerprint", None)
        curr_style = current.get("mapspec_fingerprint")
        style_changed = bool(prev_style) and bool(curr_style) and prev_style != curr_style

        data_changed = prev_inputs != curr_inputs
        algorithm_changed = prev_algos != curr_algos
        parameter_changed = prev_args != curr_args
        output_changed = prev_outputs != curr_outputs

        return {
            "vs_version_no": previous.version_no if previous is not None else None,
            "data_changed": data_changed,
            "algorithm_changed": algorithm_changed,
            "parameter_changed": parameter_changed,
            "style_changed": style_changed,
            "output_changed": output_changed,
            # Convenience flags for UI copy.
            "analysis_recomputation_expected": bool(
                data_changed or algorithm_changed or parameter_changed
            ),
        }

    @staticmethod
    def list_versions_paginated(
        db: Session, project_id: str, *, limit: int = 50, offset: int = 0
    ) -> tuple[List[MapProductVersion], int]:
        """(rows, total) for the paginated ledger endpoint — newest first."""
        from sqlalchemy import func

        rows = list(
            db.execute(
                select(MapProductVersion)
                .where(MapProductVersion.project_id == project_id)
                .order_by(MapProductVersion.version_no.desc())
                .offset(offset)
                .limit(limit)
            ).scalars().all()
        )
        total = int(
            db.execute(
                select(func.count(MapProductVersion.id)).where(
                    MapProductVersion.project_id == project_id
                )
            ).scalar()
            or 0
        )
        return rows, total

    @staticmethod
    def get_version(
        db: Session, project_id: str, version_no: int
    ) -> Optional[MapProductVersion]:
        return db.execute(
            select(MapProductVersion).where(
                MapProductVersion.project_id == project_id,
                MapProductVersion.version_no == version_no,
            )
        ).scalar_one_or_none()

    @staticmethod
    def _stored_version_diff_input(row: MapProductVersion) -> Dict[str, Any]:
        """Project a STORED version row into the dict shape ``diff_versions``
        expects for ``current`` (mirrors the columns the row was recorded
        with, so a pairwise diff compares like with like)."""
        return {
            "input_dataset_fingerprints": dict(row.input_dataset_fingerprints or {}),
            "run_manifest": {"steps": list(row.compute_plan or [])},
            "artifact_fingerprints": list(row.output_fingerprints or []),
            "mapspec_fingerprint": row.mapspec_fingerprint,
        }

    @staticmethod
    def diff_versions_pairwise(
        db: Session, project_id: str, from_version_no: int, to_version_no: int
    ) -> Dict[str, Any]:
        """Five-dimension diff between ANY two stored versions (ADR-0092 A6
        + version-workspace UI): reuses ``diff_versions`` on the stored
        projections, then attaches the drill-down details the UI renders
        (before/after fingerprints, changed parameter keys, artifact
        membership changes). Raises ValueError when either version is
        missing (route maps to 404)."""
        prev = MapProductService.get_version(db, project_id, from_version_no)
        curr = MapProductService.get_version(db, project_id, to_version_no)
        if prev is None or curr is None:
            missing = from_version_no if prev is None else to_version_no
            raise ValueError(f"map product version not found: {missing}")

        diff = MapProductService.diff_versions(
            prev, MapProductService._stored_version_diff_input(curr)
        )
        diff["from_version_no"] = from_version_no
        diff["to_version_no"] = to_version_no

        # ── drill-down (bounded, structured summaries — not raw dumps) ──
        prev_inputs = dict(prev.input_dataset_fingerprints or {})
        curr_inputs = dict(curr.input_dataset_fingerprints or {})

        def _index_args(row: MapProductVersion) -> Dict[str, Any]:
            return {
                str(s.get("step_id")): (s.get("args") or "")
                for s in (row.compute_plan or [])
                if isinstance(s, dict) and s.get("step_id")
            }

        prev_args, curr_args = _index_args(prev), _index_args(curr)
        changed_param_steps = [
            {
                "step_id": sid,
                "from": prev_args.get(sid),
                "to": curr_args.get(sid),
            }
            for sid in sorted(set(prev_args) | set(curr_args))
            if prev_args.get(sid) != curr_args.get(sid)
        ]

        def _index_algo(row: MapProductVersion) -> Dict[str, str]:
            return {
                str(s.get("step_id")): str(s.get("algorithm") or "")
                for s in (row.compute_plan or [])
                if isinstance(s, dict) and s.get("step_id")
            }

        prev_algos, curr_algos = _index_algo(prev), _index_algo(curr)
        changed_algo_steps = [
            {
                "step_id": sid,
                "from": prev_algos.get(sid) or None,
                "to": curr_algos.get(sid) or None,
            }
            for sid in sorted(set(prev_algos) | set(curr_algos))
            if prev_algos.get(sid) != curr_algos.get(sid)
        ]

        # Membership uses output fingerprints — artifact_ids are only
        # extracted when a WorkflowRun backs the version (inline-manifest
        # versions record fingerprints alone).
        prev_outs = set(str(f) for f in (prev.output_fingerprints or []) if f)
        curr_outs = set(str(f) for f in (curr.output_fingerprints or []) if f)
        diff["details"] = {
            "input_dataset_fingerprints": {
                "from": prev_inputs,
                "to": curr_inputs,
                "changed_keys": sorted(
                    set(prev_inputs) ^ set(curr_inputs)
                    | {k for k in set(prev_inputs) & set(curr_inputs)
                       if prev_inputs[k] != curr_inputs[k]}
                ),
            },
            "algorithm_steps": changed_algo_steps,
            "parameter_steps": changed_param_steps,
            "mapspec_fingerprint": {
                "from": prev.mapspec_fingerprint,
                "to": curr.mapspec_fingerprint,
            },
            "artifacts": {
                "added": sorted(curr_outs - prev_outs),
                "removed": sorted(prev_outs - curr_outs),
                "unchanged_count": len(prev_outs & curr_outs),
            },
            "workflow_runs": {
                "from": prev.workflow_run_id,
                "to": curr.workflow_run_id,
            },
        }
        return diff
