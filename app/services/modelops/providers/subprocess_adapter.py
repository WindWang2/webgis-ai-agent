"""SubprocessWorkerProvider —— 本地子进程推理 worker（V3 §B）。

定位：模型权重/预处理逻辑留在**独立进程**（core 进程零重依赖——无
torch/onnx 的部署也能经此通道消费算子侧 runtime），协议 = stdin/stdout
单行 JSON（数组 b64）。

信任域（与「包只校验不执行」的边界划分）：

- worker 脚本**不来自模型包**（package_security 成员黑名单禁止 .py——
  该门只约束数据包）；脚本由 **operator allowlist** 显式登记
  （``MODELOPS_SUBPROCESS_WORKERS = "slot=/abs/path/worker.py,…"``），
  descriptor.provider_ref = ``subprocess@<slot>``（C1 门照常生效）；
- worker 的输出仍过 ``TileOutput.validate_for``（output bomb / 概率
  归一抽验），不可信输出的防护与进程内 provider 一致。

资源有界：单请求/响应字节上限、墙钟 deadline（超时 kill）、每批一次
进程调用（无状态；cancellation 在 batch 边界生效）。
"""
from __future__ import annotations

import base64
import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np

from app.lib.modelops.capabilities import (
    DEVICE_CPU,
    TASK_CLASSIFICATION,
    TASK_EMBEDDING,
    TASK_OBJECT_DETECTION,
    TASK_SEMANTIC_SEGMENTATION,
    ProviderCapabilities,
)
from app.lib.modelops.descriptor import GeoModelDescriptor
from app.lib.modelops.errors import ProviderError, ProviderLoadFailed, ProviderOOM
from app.lib.modelops.resources import ResourceEstimate
from app.services.modelops.package_store import ModelPackageStore
from app.services.modelops.providers.base import (
    InferenceContext,
    LoadedModel,
    ProviderHealth,
    TileBatch,
    TileOutput,
)

#: 单次子进程请求/响应字节上限（b64 膨胀 ~4/3 已计入预算）。
MAX_REQUEST_BYTES = 64 * 1024 * 1024
MAX_RESPONSE_BYTES = 64 * 1024 * 1024
DEFAULT_DEADLINE_S = 120.0

_SERVED_TASKS = frozenset(
    {
        TASK_SEMANTIC_SEGMENTATION,
        TASK_OBJECT_DETECTION,
        TASK_CLASSIFICATION,
        TASK_EMBEDDING,
    }
)


def parse_worker_allowlist(raw_entries: list) -> Dict[str, str]:
    """``slot=path`` 条目 → {slot: 绝对路径}（非法条目 typed，不静默）。"""
    allowlist: Dict[str, str] = {}
    for entry in raw_entries:
        slot, sep, path = str(entry).partition("=")
        if not sep or not slot.strip() or not path.strip():
            raise ProviderError(
                f"invalid subprocess worker entry {entry!r} "
                "(expected 'slot=/abs/path/worker.py')"
            )
        resolved = str(Path(path.strip()).resolve())
        if not Path(resolved).is_file():
            raise ProviderError(
                f"subprocess worker script for slot {slot.strip()!r} does not exist: {resolved}"
            )
        allowlist[slot.strip()] = resolved
    return allowlist


def worker_request_payload(
    batch: TileBatch,
    descriptor: GeoModelDescriptor,
    ctx: InferenceContext,
    weights: Optional[np.ndarray],
) -> bytes:
    """TileBatch → stdin 请求行（b64 数组 + 参数；写前字节上限守门）。"""
    pixels_b64 = base64.b64encode(np.ascontiguousarray(batch.pixels).tobytes())
    payload: Dict[str, Any] = {
        "protocol": "modelops.subprocess/v1",
        "task": descriptor.task_types[0],
        "chip_hw": list(batch.chip_hw),
        "shape": list(batch.pixels.shape),
        "pixels_b64": pixels_b64.decode("ascii"),
        "seed": ctx.seed,
        "prompt": ctx.extras.get("prompt"),
    }
    if batch.valid_mask is not None:
        payload["valid_b64"] = base64.b64encode(
            np.ascontiguousarray(batch.valid_mask.astype(np.uint8)).tobytes()
        ).decode("ascii")
    if weights is not None:
        payload["weights_b64"] = base64.b64encode(
            np.ascontiguousarray(weights).tobytes()
        ).decode("ascii")
    line = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(line) > MAX_REQUEST_BYTES:
        raise ProviderOOM(
            f"subprocess request {len(line)} bytes exceeds {MAX_REQUEST_BYTES} "
            "(shrink batch/chip or offload weights)"
        )
    return line + b"\n"


class SubprocessWorkerProvider:
    """一个 allowlist slot 一个实例（provider_ref = ``subprocess@<slot>``）。"""

    def __init__(
        self,
        slot: str,
        script_path: str,
        *,
        packages: Optional[ModelPackageStore] = None,
        deadline_s: float = DEFAULT_DEADLINE_S,
    ) -> None:
        self._slot = slot
        self._script_path = str(Path(script_path).resolve())
        self._packages = packages
        self._deadline_s = max(1.0, deadline_s)
        self._lock = threading.Lock()
        self._in_flight = 0

    @property
    def provider_id_value(self) -> str:
        return f"subprocess@{self._slot}"

    # ── capabilities / lifecycle ────────────────────────────────────
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_id=self.provider_id_value,
            provider_type="local_subprocess",
            semantic_version=f"subprocess/{self._slot}/1.0.0",
            tasks=frozenset(_SERVED_TASKS),
            prompt_modes=frozenset(),  # promptable 不在 _SERVED_TASKS（诚实声明）
            devices=frozenset({DEVICE_CPU}),
            max_batch=4,
            streaming=False,
            cancellation=True,  # batch 边界（子进程单批调用以 deadline 兜底）
            text_prompt=False,
            max_output_bytes=MAX_RESPONSE_BYTES,
        )

    def load(self, descriptor: GeoModelDescriptor, *, device: str) -> LoadedModel:
        if not (_SERVED_TASKS & set(descriptor.task_types)):
            raise ProviderLoadFailed(
                f"subprocess worker serves {sorted(_SERVED_TASKS)}; "
                f"descriptor offers {sorted(descriptor.task_types)}"
            )
        weights = None
        if descriptor.artifact_format == "modelops-synthetic-v1":
            pass  # synthetic：无实体包；worker 以内置逻辑/参数工作
        elif self._packages is not None:
            from app.lib.modelops.package_security import load_weights_array

            package_path = self._packages.resolve(descriptor)
            import zipfile

            with zipfile.ZipFile(package_path) as zf:
                names = [n for n in zf.namelist() if n.endswith(".npy")]
                if not names:
                    raise ProviderLoadFailed(
                        f"subprocess package for {descriptor.model_id} has no weights.npy"
                    )
                weights = load_weights_array(zf.read(names[0]), member_name=names[0])
        else:
            raise ProviderLoadFailed(
                f"descriptor.artifact_format={descriptor.artifact_format!r} requires "
                "a package store on the provider"
            )
        return LoadedModel(
            descriptor=descriptor,
            provider_id=self.provider_id_value,
            device=DEVICE_CPU,
            handle_id=f"{descriptor.model_id}@{descriptor.model_version}#subprocess",
            state={"weights": weights},
        )

    def warmup(self, model: LoadedModel) -> Dict[str, Any]:
        return {"warmed": True, "script": Path(self._script_path).name}

    def estimate_resources(
        self, descriptor: GeoModelDescriptor, *, batch: int, device: str
    ) -> ResourceEstimate:
        h, w = descriptor.spatial.context_size
        k = len(descriptor.class_schema.classes) + 1 if descriptor.class_schema else 4
        per_chip = descriptor.input_bands * h * w * 4 + k * h * w * 4
        return ResourceEstimate(
            vram_bytes=0,
            host_ram_bytes=per_chip * 2,
            recommended_batch=min(4, max(1, batch)),
            externally_enforced=True,  # 子进程内存对本进程不可观测
        )

    # ── 推理（每批一次子进程调用）──────────────────────────────────
    def infer(
        self, model: LoadedModel, batch: TileBatch, ctx: InferenceContext
    ) -> TileOutput:
        with self._lock:
            self._in_flight += 1
        try:
            if ctx.is_cancelled():
                from app.lib.modelops.errors import InferenceCancelled

                raise InferenceCancelled(f"run {ctx.run_id} cancelled before batch")
            request = worker_request_payload(batch, model.descriptor, ctx, model.state["weights"])
            try:
                proc = subprocess.Popen(
                    [sys.executable, self._script_path],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            except OSError as exc:
                raise ProviderError(f"subprocess spawn failed: {exc}") from exc
            # 响应增量读取：字节上限在读取过程中生效（先全量缓冲的
            # "cap" 防不住 output bomb）。stderr 有界随读。
            out, err = self._read_bounded(proc, request)
            if proc.returncode != 0:
                detail = (err or b"").decode("utf-8", "replace")[:400]
                raise ProviderError(
                    f"subprocess worker exited {proc.returncode}: {detail}"
                )
            return self._parse_response(model, batch, ctx, out)
        finally:
            with self._lock:
                self._in_flight -= 1

    def _read_bounded(self, proc: subprocess.Popen, request: bytes) -> tuple:
        """stdin 一次性写入；stdout 以字节上限增量读取 + deadline kill。"""
        import threading

        proc.stdin.write(request)
        proc.stdin.close()

        buf = bytearray()
        exceeded = threading.Event()

        def _reader():
            try:
                while True:
                    chunk = proc.stdout.read(65536)
                    if not chunk:
                        break
                    buf.extend(chunk)
                    if len(buf) > MAX_RESPONSE_BYTES:
                        exceeded.set()
                        proc.kill()
                        break
            except Exception:  # noqa: BLE001 — 管道随 kill 断开是预期
                pass

        reader_thread = threading.Thread(target=_reader, daemon=True)
        reader_thread.start()
        try:
            proc.wait(timeout=self._deadline_s)
        except subprocess.TimeoutExpired as exc:
            proc.kill()
            proc.wait()
            raise ProviderOOM(
                f"subprocess worker exceeded deadline {self._deadline_s}s "
                f"(killed); shrink batch or raise MODELOPS_SUBPROCESS_DEADLINE_S"
            ) from exc
        reader_thread.join(timeout=5.0)
        if exceeded.is_set():
            raise ProviderOOM(
                f"subprocess response exceeds {MAX_RESPONSE_BYTES} bytes "
                "(output bomb guard, killed mid-read)"
            )
        err = proc.stderr.read() if proc.stderr else b""
        return bytes(buf), err

    def _parse_response(
        self, model: LoadedModel, batch: TileBatch, ctx: InferenceContext, raw: bytes
    ) -> TileOutput:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise ProviderError(f"subprocess response is not valid JSON: {exc}") from exc
        # 多任务 descriptor 按「引擎解析的请求任务」映射（缺失 = 首任务）。
        task = ctx.extras.get("task") or model.descriptor.task_types[0]
        if task in (TASK_SEMANTIC_SEGMENTATION, "promptable_segmentation"):
            probs = self._array(payload, "class_probabilities_b64", ndim=4)
            return TileOutput(task_type=task, class_probabilities=probs)
        if task == TASK_CLASSIFICATION:
            return TileOutput(
                task_type=task,
                label_probabilities=self._array(payload, "label_probabilities_b64", ndim=2),
            )
        if task == TASK_EMBEDDING:
            return TileOutput(
                task_type=task, embeddings=self._array(payload, "embeddings_b64", ndim=2)
            )
        if task == TASK_OBJECT_DETECTION:
            detections = payload.get("detections")
            if not isinstance(detections, list):
                raise ProviderError("object_detection worker response requires detections[]")
            return TileOutput(task_type=task, detections=detections)
        raise ProviderError(f"subprocess mapping lacks task {task!r}")

    @staticmethod
    def _array(payload: Dict[str, Any], key: str, *, ndim: int) -> np.ndarray:
        raw_b64 = payload.get(key)
        if not raw_b64:
            raise ProviderError(f"subprocess response missing {key}")
        arr = np.frombuffer(base64.b64decode(raw_b64), dtype=np.float32)
        shape = payload.get(f"{key}_shape")
        if not shape:
            raise ProviderError(f"subprocess response missing {key}_shape")
        arr = arr.reshape([int(v) for v in shape])
        if arr.ndim != ndim:
            raise ProviderError(f"{key} must have ndim={ndim} (got {arr.ndim})")
        return np.ascontiguousarray(arr, dtype=np.float32)

    # ── cancel/health/unload ────────────────────────────────────────
    def cancel(self, model: LoadedModel, run_id: str) -> bool:
        return True  # batch 边界协作取消；单批由 deadline 兜底

    def health(self) -> ProviderHealth:
        ok = Path(self._script_path).is_file()
        return ProviderHealth(
            healthy=ok,
            detail="" if ok else f"worker script missing: {self._script_path}",
            in_flight=self._in_flight,
        )

    def unload(self, model: LoadedModel) -> None:
        model.state["weights"] = None


__all__ = [
    "SubprocessWorkerProvider",
    "MAX_REQUEST_BYTES",
    "MAX_RESPONSE_BYTES",
    "parse_worker_allowlist",
    "worker_request_payload",
]
