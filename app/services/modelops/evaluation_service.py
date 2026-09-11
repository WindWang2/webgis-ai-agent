"""Evaluation runner —— 评估编排（ADR-0119 §3.8；Epic §K）。

输入：predictions（模型输出栅格/检测 JSON）+ references（标签栅格/
标签检测 JSON）→ typed 指标 + 泄漏审计报告 + 评估 manifest artifact。
不训练、不下载、无随机源。
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from app.lib.geo_raster.reader import RasterReader
from app.lib.modelops.evaluation import (
    detection_metrics,
    expected_calibration_error,
    leakage_guard,
    segmentation_metrics,
    spatial_blocked_split,
)
from app.lib.modelops.errors import ModelOpsError
from app.services.modelops.artifacts import publish_json_artifact


@dataclass(frozen=True)
class EvaluationRequest:
    """一次评估请求（owner scope 恰好一维）。"""

    owner_scope: Dict[str, str]
    task_type: str                                    # segmentation|object_detection|classification
    predictions_path: Path
    references_path: Optional[Path] = None            # segmentation/classification 用
    confidence_path: Optional[Path] = None            # ECE 校准（可选）
    reference_detections: Optional[List[Dict[str, Any]]] = None
    prediction_detections: Optional[List[Dict[str, Any]]] = None
    num_classes: int = 2
    ignore_index: int = 255
    block_size_px: int = 256
    num_folds: int = 5
    model_manifest: Optional[Dict[str, Any]] = None   # 被评估模型的推理 manifest
    output_dir: Optional[Path] = None
    #: V3 §G：分区栅格（region id raster；可选 per-region 指标）。
    regions_path: Optional[Path] = None
    #: V3 §G：边界质量指标（分割；默认开）。
    compute_boundary: bool = True
    boundary_tolerance_px: int = 1
    #: V3 §G：评估结果自动入 lineage（需 model_id/model_version）。
    model_id: Optional[str] = None
    model_version: Optional[str] = None


@dataclass(frozen=True)
class DriftEvaluationRequest:
    """漂移评估请求（V3 §G；baseline vs current 两次预测的对比）。"""

    owner_scope: Dict[str, str]
    baseline_path: Path
    current_path: Path
    baseline_confidence_path: Optional[Path] = None
    current_confidence_path: Optional[Path] = None
    #: 地理切片：region id 栅格（同网格）；逐 region 的 IoU/漂移。
    regions_path: Optional[Path] = None
    #: 时间/传感器标签（lineage 分组语义；进报告不做算法假设）。
    baseline_tags: Optional[Dict[str, str]] = None
    current_tags: Optional[Dict[str, str]] = None
    num_classes: int = 2
    ignore_index: int = 255
    model_id: Optional[str] = None
    model_version: Optional[str] = None
    output_dir: Optional[Path] = None


class EvaluationService:
    """评估运行器（纯本地；产物 = modelops_artifact DataObject）。"""

    def evaluate(self, request: EvaluationRequest) -> Dict[str, Any]:
        if set(request.owner_scope) - {"session_id", "project_id"} or len(request.owner_scope) != 1:
            raise ModelOpsError("owner_scope must be exactly one of session_id/project_id")
        run_id = uuid.uuid4().hex[:16]
        started = time.time()
        report: Dict[str, Any] = {"run_id": run_id, "task_type": request.task_type}

        if request.task_type == "segmentation":
            if request.references_path is None:
                raise ModelOpsError("segmentation evaluation requires references_path")
            pred, refs = self._read_pair(request.predictions_path, request.references_path)
            metrics = segmentation_metrics(
                refs, pred, num_classes=request.num_classes,
                ignore_index=request.ignore_index,
            )
            report["metrics"] = metrics.as_dict()
            # m-5：置信度校准需要 confidence 栅格输入（类代理无信息量）——
            # 不再伪造 ECE 数值；输入齐备时才计算。
            if request.confidence_path is not None:
                from app.lib.geo_raster.reader import RasterReader as _RR

                with _RR.open(str(request.confidence_path)) as cr:
                    conf = cr.read_full(budget_ok=True)
                    if conf.ndim == 3:
                        conf = conf[0]
                mask = pred != request.ignore_index
                report["ece"] = expected_calibration_error(
                    conf[mask].tolist(), (pred[mask] == refs[mask]).tolist()
                )
            else:
                report["ece"] = None
                report["ece_note"] = "requires confidence_path (class proxy is uninformative)"
            split = spatial_blocked_split(
                refs.shape[0], refs.shape[1],
                block_size_px=request.block_size_px, num_folds=request.num_folds,
            )
            # R2-M5：泄漏审计样本按行采样上界（block 级判定不需要全点集）。
            rr_idx, cc_idx = np.where(refs != request.ignore_index)
            step = max(1, len(rr_idx) // 10_000)
            samples = [(int(r), int(c)) for r, c in zip(rr_idx[::step], cc_idx[::step])]
            report["leakage_guard"] = leakage_guard(
                train_rows=[], eval_rows=samples, split=split,
            ).as_dict()
            report["spatial_split"] = split.as_dict()
            # V3 §G：边界质量（分割语义的空间连续性指标）。
            if request.compute_boundary:
                from app.lib.modelops.evaluation import boundary_metrics

                report["boundary"] = boundary_metrics(
                    refs, pred,
                    tolerance_px=request.boundary_tolerance_px,
                    ignore_index=request.ignore_index,
                )
            # V3 §G：spatial fold 逐折指标（泄漏防护的分区复用为评估切片）。
            from app.lib.modelops.evaluation import segmentation_metrics as _seg_m

            assign = {(br_, bc_): fold for br_, bc_, fold in split.assignment}
            br_idx = (np.arange(refs.shape[0]) // split.block_size_px)[:, None]
            bc_idx = (np.arange(refs.shape[1]) // split.block_size_px)[None, :]
            fold_grid = np.vectorize(
                lambda a, b: assign.get((int(a), int(b)), -1), otypes=[np.int64]
            )(br_idx, bc_idx)
            folds: Dict[str, Any] = {}
            for fold in range(request.num_folds):
                mask = (fold_grid == fold) & (refs != request.ignore_index)
                if not mask.any():
                    continue
                fm = _seg_m(refs[mask], pred[mask],
                            num_classes=request.num_classes,
                            ignore_index=request.ignore_index)
                folds[str(fold)] = {"miou": fm.miou, "support_px": int(mask.sum())}
            report["per_fold"] = folds
            # V3 §G：地理分区指标（region id 栅格，可选）。
            if request.regions_path is not None:
                from app.lib.modelops.evaluation import per_region_metrics

                regions = self._read_single(request.regions_path)
                report["per_region"] = per_region_metrics(
                    refs, pred, regions,
                    num_classes=request.num_classes,
                    ignore_index=request.ignore_index,
                )
        elif request.task_type == "object_detection":
            if request.prediction_detections is None or request.reference_detections is None:
                raise ModelOpsError(
                    "detection evaluation requires prediction_detections and "
                    "reference_detections"
                )
            metrics = detection_metrics(
                request.prediction_detections, request.reference_detections
            )
            report["metrics"] = metrics.as_dict()
            report["leakage_guard"] = leakage_guard(
                train_rows=[], eval_rows=[],
                train_times=[], eval_times=[],
            ).as_dict()
        elif request.task_type == "classification":
            if request.references_path is None:
                raise ModelOpsError("classification evaluation requires references_path")
            pred, refs = self._read_pair(request.predictions_path, request.references_path)
            from app.lib.modelops.evaluation import classification_metrics

            # R2-M5：numpy 直算（.tolist() 的 Python list 物化无必要）。
            report["metrics"] = classification_metrics(
                refs.ravel(), pred.ravel(), num_classes=request.num_classes,
            )
        else:
            raise ModelOpsError(f"unsupported evaluation task {request.task_type!r}")

        report["duration_s"] = round(time.time() - started, 6)
        report["model_manifest_fingerprint"] = (
            (request.model_manifest or {}).get("manifest_fingerprint")
        )
        # 评估 manifest artifact（modelops_artifact）。
        import json

        output_dir = Path(request.output_dir or Path("data/modelops/evaluations") / run_id)
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "evaluation_report.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        try:
            published = publish_json_artifact(
                path, owner_scope=request.owner_scope, source_refs=[],
                producer={"capability": "modelops.evaluation", "run_id": run_id},
            )
        except Exception as exc:  # noqa: BLE001 — 发布失败诚实降级（文件仍在）
            report["artifact_publish"] = {"published": False, "reason": str(exc)}
            published = {"published": False, "path": str(path)}
        report["artifact"] = published
        return report

    # ── 漂移评估（V3 §G：baseline vs current 的空间/分布对比）────────
    def evaluate_drift(self, request: DriftEvaluationRequest) -> Dict[str, Any]:
        run_id = uuid.uuid4().hex[:16]
        started = time.time()
        from app.lib.modelops.evaluation import (
            class_distribution_psi,
            per_region_metrics,
            population_stability_index,
            segmentation_metrics,
        )

        base, cur = self._read_pair(request.baseline_path, request.current_path)
        report: Dict[str, Any] = {
            "run_id": run_id,
            "report_type": "drift",
            "baseline_tags": dict(request.baseline_tags or {}),
            "current_tags": dict(request.current_tags or {}),
        }
        # 全局指标与 delta。
        mb = segmentation_metrics(base, cur, num_classes=request.num_classes,
                                  ignore_index=request.ignore_index)
        report["agreement_miou"] = mb.miou  # 两次预测的一致性（自漂移代理）
        # 类别分布 PSI。
        base_counts = [
            int((base == k).sum()) for k in range(request.num_classes)
        ]
        cur_counts = [
            int((cur == k).sum()) for k in range(request.num_classes)
        ]
        report["class_distribution_psi"] = class_distribution_psi(base_counts, cur_counts)
        # 置信度分布 PSI（可选）。
        if request.baseline_confidence_path and request.current_confidence_path:
            bconf = self._read_single(request.baseline_confidence_path)
            cconf = self._read_single(request.current_confidence_path)
            mask = (base != request.ignore_index) & (cur != request.ignore_index)
            if mask.any() and bconf.shape == base.shape and cconf.shape == cur.shape:
                report["confidence_psi"] = population_stability_index(
                    bconf[mask].tolist(), cconf[mask].tolist()
                )
        # 地理切片：逐 region 的一致性（地理漂移定位）。
        if request.regions_path is not None:
            regions = self._read_single(request.regions_path)
            report["per_region"] = per_region_metrics(
                base, cur, regions,
                num_classes=request.num_classes,
                ignore_index=request.ignore_index,
            )
        report["duration_s"] = round(time.time() - started, 6)
        import json

        output_dir = Path(request.output_dir or Path("data/modelops/evaluations") / run_id)
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "drift_report.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        try:
            published = publish_json_artifact(
                path, owner_scope=request.owner_scope, source_refs=[],
                producer={"capability": "modelops.drift", "run_id": run_id},
            )
        except Exception as exc:  # noqa: BLE001
            published = {"published": False, "path": str(path), "reason": str(exc)}
        report["artifact"] = published
        return report

    @staticmethod
    def _read_single(path: Path) -> np.ndarray:
        """读单波段栅格（regions/confidence；返回 2D）。"""
        with RasterReader.open(str(path)) as reader:
            data = reader.read_full()
        if data.ndim == 3:
            data = data[0]
        return data

    @staticmethod
    def _read_pair(pred_path: Path, ref_path: Path) -> tuple:
        """读预测/参考栅格（有界：同形状校验 + 预算护栏由 RasterReader 兜底）。"""
        # R2-M5：不绕过 reader 预算（budget_ok=True 是审计眼里的后门）。
        with RasterReader.open(str(pred_path)) as pr:
            pred = pr.read_full()
            pred_meta = pr.metadata()
        with RasterReader.open(str(ref_path)) as rr:
            refs = rr.read_full()
            ref_meta = rr.metadata()
        if pred_meta.width != ref_meta.width or pred_meta.height != ref_meta.height:
            raise ModelOpsError(
                f"prediction {pred_meta.width}x{pred_meta.height} and reference "
                f"{ref_meta.width}x{ref_meta.height} shapes differ"
            )
        if pred.ndim == 3:
            pred = pred[0]
        if refs.ndim == 3:
            refs = refs[0]
        return pred, refs
