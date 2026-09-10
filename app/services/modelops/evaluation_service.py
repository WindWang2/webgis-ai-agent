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
