"""DL runtime 输出 → TileOutput 契约映射（V3 §B 共享件）。

onnx/torch/subprocess 三个 adapter 共用的唯一映射口径（一处实现，三处
消费——输出语义漂移 = reuse/评估层系统性失真，不允许各 adapter 自行
解释）：

- ``semantic_segmentation`` / ``change_detection``：runtime 输出
  (N,K,H,W) logits/probs → descriptor.output_transform.activation 变换
  → ``class_probabilities``；
- ``classification``：(N,K) → activation → ``label_probabilities``；
- ``object_detection``：(N,M,6) = x1,y1,x2,y2,score,label(≥1)（chip 像素
  坐标）→ ``detections``（box=[x,y,w,h] + batch_index）；
- ``embedding``：(N,D) → ``embeddings``。

activation 语义（descriptor.output_transform，进指纹）：
softmax（默认；logits 归一为概率）/ sigmoid（二类 p/(1-p) 展开）/ none
（模型自报已归一；``TileOutput.validate_for`` 的概率抽验兜底）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from app.lib.modelops.descriptor import GeoModelDescriptor
from app.lib.modelops.errors import ProviderError
from app.services.modelops.providers.base import TileBatch, TileOutput

TASK_TO_SHAPE = "dl_output/v1"


def apply_activation(raw: np.ndarray, *, activation: str) -> np.ndarray:
    """logits → 契约概率（确定性 float32；数值稳定实现）。"""
    x = np.asarray(raw, dtype=np.float32)
    if activation == "none":
        return x
    if activation == "sigmoid":
        # 二类展开：(N,1,...) p → (N,2,...) = (1-p, p)。
        if x.shape[1] != 1:
            raise ProviderError(
                f"sigmoid activation expects 1 logit channel (got {x.shape[1]})"
            )
        p = 1.0 / (1.0 + np.exp(-x))
        other = 1.0 - p
        return np.concatenate([other, p], axis=1).astype(np.float32)
    # softmax（沿 channel 轴 1；数值稳定：减最大值）。
    shifted = x - x.max(axis=1, keepdims=True)
    ex = np.exp(shifted)
    return (ex / ex.sum(axis=1, keepdims=True)).astype(np.float32)


def map_dl_outputs(
    task: str,
    raw_outputs: Sequence[np.ndarray],
    descriptor: GeoModelDescriptor,
    batch: TileBatch,
) -> TileOutput:
    """runtime 原始输出张量 → 任务判别 TileOutput（唯一实现点）。"""
    if not raw_outputs:
        raise ProviderError("dl runtime returned no output tensors")
    primary = np.asarray(raw_outputs[0])
    activation = descriptor.output_transform.activation

    if task in ("semantic_segmentation", "promptable_segmentation", "change_detection"):
        if primary.ndim != 4:
            raise ProviderError(
                f"{task} dl output must be (N,K,H,W); got {primary.shape}"
            )
        probs = apply_activation(primary, activation=activation)
        return TileOutput(task_type=task, class_probabilities=probs)

    if task == "classification":
        if primary.ndim != 2:
            raise ProviderError(
                f"classification dl output must be (N,K); got {primary.shape}"
            )
        return TileOutput(
            task_type=task, label_probabilities=apply_activation(primary, activation=activation)
        )

    if task == "object_detection":
        if primary.ndim != 3 or primary.shape[-1] != 6:
            raise ProviderError(
                f"object_detection dl output must be (N,M,6) "
                f"[x1,y1,x2,y2,score,label]; got {primary.shape}"
            )
        detections: List[Dict[str, Any]] = []
        for i in range(primary.shape[0]):
            for m in primary[i]:
                x1, y1, x2, y2, score, label = (float(v) for v in m)
                if score <= 0.0 or label < 1:
                    continue
                if x2 <= x1 or y2 <= y1:
                    continue
                detections.append(
                    {
                        "box": [x1, y1, x2 - x1, y2 - y1],
                        "score": score,
                        "label": int(label),
                        "batch_index": i,
                    }
                )
        return TileOutput(task_type=task, detections=detections)

    if task == "embedding":
        if primary.ndim != 2:
            raise ProviderError(
                f"embedding dl output must be (N,D); got {primary.shape}"
            )
        return TileOutput(task_type=task, embeddings=primary.astype(np.float32))

    raise ProviderError(
        f"dl runtime mapping does not support task {task!r} "
        "(instance segmentation / super-resolution / fusion need dedicated providers)"
    )


__all__ = ["apply_activation", "map_dl_outputs"]
