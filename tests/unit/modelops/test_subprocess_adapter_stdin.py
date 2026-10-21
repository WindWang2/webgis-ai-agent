"""#1709 回归：子进程推理通道 stdin 死锁防护（modelops subprocess adapter）。

死锁场景：worker 在消费 stdin 之前先向 stderr 写出超过管道缓冲（Linux
默认 64KiB）的量。旧实现主线程同步写 stdin（无超时）、stderr 直到
``proc.wait()`` 之后才读 —— 双方互卡直到进程被外部杀掉，且 deadline
守门在该路径上不可达。修复：三条管道各由独立线程消费（stderr 先起
drain），主线程只做 deadline 守门。
"""
from __future__ import annotations

import threading

import numpy as np
import pytest

from app.services.modelops.providers.base import (
    InferenceContext,
    TileBatch,
)

#: 测试 worker：先写爆 stderr 管道再读 stdin 并响应（死锁场景）。
STDERR_FIRST_WORKER = r'''
"""测试 worker：先向 stderr 写爆管道缓冲，再读 stdin 并响应（#1709）。"""
import base64, json, sys
import numpy as np

sys.stderr.write("E" * 300000)   # 超过 Linux 管道缓冲（64KiB）
sys.stderr.flush()
request = json.loads(sys.stdin.readline())
shape = request["shape"]
pixels = np.frombuffer(base64.b64decode(request["pixels_b64"]), dtype=np.float32).reshape(shape)
mean = pixels.mean(axis=1, keepdims=True)
bright = (mean > 0.5).astype(np.float32)
p_obj = np.clip(bright, 0.02, 0.98)
probs = np.concatenate([1.0 - p_obj, p_obj], axis=1).astype(np.float32)
response = {
    "class_probabilities_b64": base64.b64encode(probs.tobytes()).decode("ascii"),
    "class_probabilities_b64_shape": list(probs.shape),
}
sys.stdout.write(json.dumps(response))
'''

#: 测试 worker：无界 stderr 写手（超过 MAX_STDERR_BYTES 后长睡）。
UNBOUNDED_STDERR_WORKER = r'''
"""测试 worker：无界 stderr 写入（#1709 drain 上界守卫场景）。"""
import sys

sys.stderr.write("E" * (4 * 1024 * 1024))
sys.stderr.flush()
sys.stdin.readline()
'''


def _descriptor():
    from app.lib.modelops.descriptor import (
        ClassSchema,
        DeviceRequirements,
        GeoModelDescriptor,
        NormalizationSpec,
        SpatialRequirements,
    )

    return GeoModelDescriptor.model_validate({
        "model_id": "sub-1709",
        "model_version": "1.0.0",
        "checksum": "d" * 64,
        "provider_type": "local_subprocess",
        "provider_ref": "subprocess@fixture",
        "provider_semantic_version": "subprocess/fixture/1.0.0",
        "task_types": ("semantic_segmentation",),
        "input_modalities": ("optical_rgb",),
        "input_bands": 3,
        "normalization": NormalizationSpec(kind="none"),
        "output_types": ("class_raster",),
        "class_schema": ClassSchema(classes=("background", "object")),
        "spatial": SpatialRequirements(chip_size=(256, 256), context_size=(256, 256)),
        "device_requirements": DeviceRequirements(required="cpu"),
        "license": "test",
    })


def _big_batch() -> TileBatch:
    """请求体超过 stdin 管道缓冲（~1MiB > 64KiB）—— 否则旧实现不会卡住。"""
    pixels = np.zeros((1, 3, 256, 256), dtype=np.float32)
    pixels[0] = 0.9
    return TileBatch(pixels=pixels, chip_hw=(256, 256))


def test_stderr_first_worker_does_not_deadlock(tmp_path):
    """stderr 先行写爆管道的 worker：infer 必须完成。

    看门狗把死锁变成失败（而不是挂起整个套件）：修复前本用例 60s 内
    不会返回（主线程写 stdin 与 worker 写 stderr 互卡）。
    """
    from app.services.modelops.providers.subprocess_adapter import (
        SubprocessWorkerProvider,
        worker_request_payload,
    )

    script = tmp_path / "stderr_first_worker.py"
    script.write_text(STDERR_FIRST_WORKER, encoding="utf-8")
    provider = SubprocessWorkerProvider("stderr-first", str(script), deadline_s=30.0)
    model = provider.load(_descriptor(), device="cpu")
    batch = _big_batch()
    request = worker_request_payload(
        batch, model.descriptor, InferenceContext(run_id="d1709"), None)
    assert len(request) > 65536, "请求必须超过管道缓冲才是死锁场景"

    box: dict = {}

    def _run():
        try:
            box["out"] = provider.infer(
                model, batch, InferenceContext(run_id="d1709"))
        except Exception as exc:  # noqa: BLE001 — 看门狗线程里转交主线程断言
            box["exc"] = exc

    watcher = threading.Thread(target=_run, daemon=True)
    watcher.start()
    watcher.join(timeout=60)
    assert not watcher.is_alive(), "infer 死锁（stdin 写入 / stderr 读取互卡）"
    assert "exc" not in box, box.get("exc")
    out = box["out"]
    out.validate_for(batch)
    # 亮 chip → object（响应确实被解析，不是空跑）
    assert int(out.class_probabilities[0].argmax(axis=0)[0, 0]) == 1
    provider.unload(model)


def test_unbounded_stderr_writer_killed_typed(tmp_path):
    """无界 stderr 写手 → drain 上界守卫：kill + typed ProviderError
    （宿主内存有界；与 stdout bomb 同纪律）。"""
    from app.lib.modelops.errors import ProviderError
    from app.services.modelops.providers.subprocess_adapter import (
        SubprocessWorkerProvider,
    )

    script = tmp_path / "unbounded_stderr_worker.py"
    script.write_text(UNBOUNDED_STDERR_WORKER, encoding="utf-8")
    provider = SubprocessWorkerProvider("unbounded", str(script), deadline_s=30.0)
    model = provider.load(_descriptor(), device="cpu")
    with pytest.raises(ProviderError, match="stderr exceeds"):
        provider.infer(model, _big_batch(), InferenceContext(run_id="d1709b"))
    provider.unload(model)


def test_normal_worker_still_roundtrips(tmp_path):
    """守序 worker（先读 stdin 再响应）行为不变（回归锚）。"""
    from app.services.modelops.providers.subprocess_adapter import (
        SubprocessWorkerProvider,
    )

    worker = r'''
import base64, json, sys
import numpy as np
request = json.loads(sys.stdin.readline())
shape = request["shape"]
pixels = np.frombuffer(base64.b64decode(request["pixels_b64"]), dtype=np.float32).reshape(shape)
mean = pixels.mean(axis=1, keepdims=True)
bright = (mean > 0.5).astype(np.float32)
p_obj = np.clip(bright, 0.02, 0.98)
probs = np.concatenate([1.0 - p_obj, p_obj], axis=1).astype(np.float32)
sys.stdout.write(json.dumps({
    "class_probabilities_b64": base64.b64encode(probs.tobytes()).decode("ascii"),
    "class_probabilities_b64_shape": list(probs.shape),
}))
'''
    script = tmp_path / "orderly_worker.py"
    script.write_text(worker, encoding="utf-8")
    provider = SubprocessWorkerProvider("orderly", str(script), deadline_s=30.0)
    model = provider.load(_descriptor(), device="cpu")
    batch = _big_batch()
    out = provider.infer(model, batch, InferenceContext(run_id="d1709c"))
    out.validate_for(batch)
    assert int(out.class_probabilities[0].argmax(axis=0)[0, 0]) == 1
    provider.unload(model)
