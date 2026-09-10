"""DL runtime backend probe —— 推理运行时可用性探测（V3 §B）。

职责：**只探测，不执行**。torch/onnxruntime/tensorrt/openvino 等运行时
缺席、损坏（Windows DLL 加载失败是常态而非异常）时给出 honest 的
``available=False`` + 原因，绝不阻塞 provider 注册与主推理路径。

硬约束：

- **lazy import**：本模块 import 时不触碰任何 DL 运行时（首次 probe 才
  import）；import 失败被捕获为探测结果而非异常；
- 探测结果进程内缓存（probe 有可观测开销），``reset_probe_cache`` 供
  测试复位；
- provider 的 capability 声明消费本探测（cuda 设备只在探测到时声明），
  load 失败一律 typed（ProviderLoadFailed），绝不半可用。
"""
from __future__ import annotations

import importlib
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

BACKEND_TORCH = "torch"
BACKEND_ONNXRUNTIME = "onnxruntime"
BACKEND_TENSORRT = "tensorrt"
BACKEND_OPENVINO = "openvino"

#: 封闭词表（descriptor.provider_type 词表的运行时对偶）。
BACKEND_IDS = frozenset({BACKEND_TORCH, BACKEND_ONNXRUNTIME, BACKEND_TENSORRT, BACKEND_OPENVINO})


@dataclass(frozen=True)
class BackendInfo:
    """一个 DL 运行时的探测结论（honest：不可用=显式原因）。"""

    backend: str
    available: bool
    version: str = ""
    detail: str = ""
    cuda_available: bool = False
    cuda_device_count: int = 0
    #: onnxruntime 注册的执行提供者（有序；TensorRT/CUDA 优先于 CPU）。
    execution_providers: Tuple[str, ...] = ()

    def as_dict(self) -> Dict[str, Any]:
        return {
            "backend": self.backend,
            "available": self.available,
            "version": self.version,
            "detail": self.detail,
            "cuda_available": self.cuda_available,
            "cuda_device_count": self.cuda_device_count,
            "execution_providers": list(self.execution_providers),
        }


_probe_lock = threading.Lock()
_probe_cache: Dict[str, BackendInfo] = {}


def reset_probe_cache() -> None:
    """测试钩子：清空进程内探测缓存。"""
    with _probe_lock:
        _probe_cache.clear()


def probe_backend(backend: str) -> BackendInfo:
    """探测一个 DL 运行时（进程内缓存；绝不抛异常）。"""
    if backend not in BACKEND_IDS:
        raise ValueError(f"unknown backend {backend!r} (known: {sorted(BACKEND_IDS)})")
    with _probe_lock:
        cached = _probe_cache.get(backend)
    if cached is not None:
        return cached
    info = _probe_uncached(backend)
    with _probe_lock:
        _probe_cache[backend] = info
    return info


def probe_all() -> Dict[str, BackendInfo]:
    return {name: probe_backend(name) for name in sorted(BACKEND_IDS)}


def onnx_execution_providers(*, want_cuda: bool = True) -> List[str]:
    """onnxruntime 会话的执行提供者序列（探测驱动；CPU 永远兜底）。

    TensorRT/CUDA EP 只在 onnxruntime 实际注册时出现——声明了不存在的
    EP 会让 InferenceSession 构造失败，因此以 ``get_available_providers``
    为唯一真相。
    """
    info = probe_backend(BACKEND_ONNXRUNTIME)
    if not info.available:
        return []
    ordered: List[str] = []
    for ep in info.execution_providers:
        name = str(ep)
        if "CPU" in name:
            continue
        if not want_cuda and ("CUDA" in name or "Tensorrt" in name or "TensorRT" in name):
            continue
        ordered.append(name)
    ordered.append("CPUExecutionProvider")
    return ordered


def _probe_uncached(backend: str) -> BackendInfo:
    if backend == BACKEND_TORCH:
        return _probe_torch()
    if backend == BACKEND_ONNXRUNTIME:
        return _probe_onnxruntime()
    if backend == BACKEND_TENSORRT:
        return _probe_generic(backend, "tensorrt")
    if backend == BACKEND_OPENVINO:
        return _probe_generic(backend, "openvino")
    raise ValueError(backend)  # pragma: no cover — 词表已在上层校验


def _probe_torch() -> BackendInfo:
    try:
        torch = importlib.import_module("torch")
    except Exception as exc:  # noqa: BLE001 — DLL 损坏/缺席都是"不可用"
        return BackendInfo(
            backend=BACKEND_TORCH,
            available=False,
            detail=f"torch runtime unavailable: {type(exc).__name__}: {exc}"[:400],
        )
    try:
        version = str(getattr(torch, "__version__", ""))
        cuda_available = bool(torch.cuda.is_available())
        device_count = int(torch.cuda.device_count()) if cuda_available else 0
    except Exception as exc:  # noqa: BLE001 — CUDA 初始化失败不掩盖 torch 本体
        logger.debug("torch cuda probe failed: %s", exc)
        return BackendInfo(
            backend=BACKEND_TORCH, available=True, version=str(getattr(torch, "__version__", ""))
        )
    return BackendInfo(
        backend=BACKEND_TORCH,
        available=True,
        version=version,
        cuda_available=cuda_available,
        cuda_device_count=device_count,
    )


def _probe_onnxruntime() -> BackendInfo:
    try:
        ort = importlib.import_module("onnxruntime")
    except Exception as exc:  # noqa: BLE001
        return BackendInfo(
            backend=BACKEND_ONNXRUNTIME,
            available=False,
            detail=f"onnxruntime unavailable: {type(exc).__name__}: {exc}"[:400],
        )
    try:
        eps = tuple(str(ep) for ep in ort.get_available_providers())
    except Exception:  # noqa: BLE001 — 探测失败按纯 CPU 处理
        eps = ("CPUExecutionProvider",)
    cuda = any("CUDA" in ep or "Tensorrt" in ep or "TensorRT" in ep for ep in eps)
    return BackendInfo(
        backend=BACKEND_ONNXRUNTIME,
        available=True,
        version=str(getattr(ort, "__version__", "")),
        cuda_available=cuda,
        execution_providers=eps,
    )


def _probe_generic(backend: str, module_name: str) -> BackendInfo:
    """tensorrt/openvino：可选后端，缺席是常态（V3 §B：不阻塞主路径）。"""
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:  # noqa: BLE001
        return BackendInfo(
            backend=backend,
            available=False,
            detail=f"{module_name} unavailable: {type(exc).__name__}: {exc}"[:400],
        )
    version = str(getattr(module, "__version__", "") or "")
    return BackendInfo(backend=backend, available=True, version=version)


__all__ = [
    "BACKEND_IDS",
    "BACKEND_ONNXRUNTIME",
    "BACKEND_OPENVINO",
    "BACKEND_TENSORRT",
    "BACKEND_TORCH",
    "BackendInfo",
    "onnx_execution_providers",
    "probe_all",
    "probe_backend",
    "reset_probe_cache",
]
