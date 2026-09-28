"""Worker placement envelope projection（H06 DoD2）单元测试。

锁定三件事：

1. rg.v1 ``as_dict()`` 形状 → 守卫键的真实投影（修复形状失配导致的
   守卫静默 no-op）；unknown 维不设约束（placement fail-open，governor
   仍是 unknown≠0 记账权威）；
2. cluster 层 ``min_*`` 形状逐字节兼容透传；
3. plan-node ``ExecutionNode.estimate`` → executor 派发 envelope；守卫
   在内存不足时真实拒绝（retry → PLACEMENT_MISMATCH）。
"""
import types

import pytest

from app.services.geocompute.envelope import (
    effective_dispatch_envelope,
    envelope_from_node_estimate,
    project_worker_envelope,
)


def _rgv1(mem_expected=None, mem_certainty="estimated", mem_max=None,
          gpu_required=None, resource_class="heavy"):
    mem = {"certainty": mem_certainty}
    if mem_expected is not None:
        mem["expected"] = mem_expected
    if mem_max is not None:
        mem["max"] = mem_max
    mem["source"] = "toolcost:heavy"
    return {
        "schema_version": "rg.v1",
        "subsystem": "tool_dispatch",
        "resource_class": resource_class,
        "dims": {"memory_bytes": mem},
        "raster_window": None,
        "cpu_class": "unknown",
        "io_class": "unknown",
        "gpu_required": gpu_required,
        "browser_required": None,
        "confidence": 0.4,
        "source": "class_prior",
        "reason": "test",
    }


_MIB = 1024 * 1024


# --------------------------------------------------------------------------- #
# project_worker_envelope —— rg.v1 形状
# --------------------------------------------------------------------------- #
def test_rgv1_memory_expected_projected():
    out = project_worker_envelope(_rgv1(mem_expected=1.5 * 1024 * _MIB))
    assert out["min_mem_mb"] == 1536
    assert out["min_cpu"] == 0
    assert out["gpu"] == 0
    assert out["source"].startswith("rg.v1:")


def test_rgv1_memory_max_fallback_when_expected_missing():
    out = project_worker_envelope(_rgv1(mem_expected=None, mem_max=2048 * _MIB))
    assert out["min_mem_mb"] == 2048


def test_rgv1_unknown_memory_sets_no_constraint():
    out = project_worker_envelope(_rgv1(mem_certainty="unknown"))
    assert out["min_mem_mb"] == 0
    out2 = project_worker_envelope(_rgv1(mem_certainty="unavailable"))
    assert out2["min_mem_mb"] == 0


def test_rgv1_gpu_required_projected():
    out = project_worker_envelope(_rgv1(gpu_required=True))
    assert out["gpu"] == 1


def test_flat_min_shape_passthrough_byte_compatible():
    env = {"min_cpu": 2, "min_mem_mb": 4096, "gpu": 1}
    assert project_worker_envelope(env) == env
    # 缺键补 0，不抛
    assert project_worker_envelope({"min_mem_mb": 8}) == {
        "min_cpu": 0, "min_mem_mb": 8, "gpu": 0}


def test_unknown_shape_and_garbage_fail_open():
    assert project_worker_envelope({"foo": 1}) == {}
    assert project_worker_envelope(None) == {}
    assert project_worker_envelope("nope") == {}


def test_projection_kill_switch(monkeypatch):
    monkeypatch.setenv("WEBGIS_PLACEMENT_ENVELOPE_PROJECTION", "0")
    assert project_worker_envelope(_rgv1(mem_expected=2 * _MIB)) == {}
    # min_* 透传不受开关影响
    assert project_worker_envelope({"min_mem_mb": 8})["min_mem_mb"] == 8


# --------------------------------------------------------------------------- #
# envelope_from_node_estimate / effective_dispatch_envelope
# --------------------------------------------------------------------------- #
class _Est:
    def __init__(self, memory_mb=None):
        self.memory_mb = memory_mb


def test_node_estimate_memory_projected():
    out = envelope_from_node_estimate(_Est(memory_mb=512.4))
    assert out["min_mem_mb"] == 513  # ceil，宁可高估
    assert out["source"] == "plan.node_estimate"


def test_node_estimate_without_memory_yields_no_mem_constraint():
    out = envelope_from_node_estimate(_Est(memory_mb=None))
    assert out["min_mem_mb"] == 0


def test_effective_dispatch_envelope_precedence():
    explicit = {"min_cpu": 1, "min_mem_mb": 100, "gpu": 0}
    assert effective_dispatch_envelope(explicit, _Est(memory_mb=999)) is explicit
    proj = effective_dispatch_envelope(None, _Est(memory_mb=256))
    assert proj["min_mem_mb"] == 256
    assert effective_dispatch_envelope(None, None) is None


def test_effective_dispatch_envelope_kill_switch(monkeypatch):
    monkeypatch.setenv("WEBGIS_PLACEMENT_ENVELOPE_PROJECTION", "0")
    assert effective_dispatch_envelope(None, _Est(memory_mb=256)) is None


# --------------------------------------------------------------------------- #
# _placement_guard —— 真实拒绝行为（内存不足 → retry → failed）
# --------------------------------------------------------------------------- #
class _FakeCap:
    def __init__(self, cpu=4, mem_mb=2048, gpu=0):
        self.cpu_cores, self.mem_mb, self.gpu_count = cpu, mem_mb, gpu

    def satisfies(self, *, min_cpu=0, min_mem_mb=0, gpu=0):
        if min_cpu > 0 and self.cpu_cores < min_cpu:
            return False
        if min_mem_mb > 0 and self.mem_mb < min_mem_mb:
            return False
        if gpu > 0 and self.gpu_count < gpu:
            return False
        return True


class _FakeRequest:
    def __init__(self, retries=0):
        self.retries = retries
        self.kwargs = {"run_id": "r-1"}


class _FakeTask:
    def __init__(self, retries=0):
        self.request = _FakeRequest(retries)

    def retry(self, **kwargs):  # celery retry 语义：自身抛 Retry（不带 kwargs）
        import celery.exceptions
        raise celery.exceptions.Retry()


class _FakeNode:
    node_id = "n-1"


def _guard_with_cap(monkeypatch, cap, envelope):
    from app.services.geocompute import tasks as tasks_mod
    monkeypatch.setattr(tasks_mod, "_local_capability", lambda: cap)
    return tasks_mod._placement_guard(
        _FakeTask(), _FakeNode(), envelope, "worker-test")


def test_guard_rejects_rgv1_envelope_exceeding_worker_memory(monkeypatch):
    # workflow driver 生产形状（修复前恒放行；现在真实拒绝）
    env = _rgv1(mem_expected=4 * 1024 * _MIB)  # 4GiB 期望 > 2GiB worker
    assert _guard_with_cap(monkeypatch, _FakeCap(mem_mb=2048), env) == "retry"


def test_guard_rejects_exhausted_retries_to_failed(monkeypatch):
    env = _rgv1(mem_expected=4 * 1024 * _MIB)
    task = _FakeTask(retries=99)  # 重投耗尽
    from app.services.geocompute import tasks as tasks_mod
    monkeypatch.setattr(tasks_mod, "_local_capability", lambda: _FakeCap())
    ret = tasks_mod._placement_guard(task, _FakeNode(), env, "worker-test")
    assert ret == "failed"  # 调用方落 PLACEMENT_MISMATCH 终态


def test_guard_passes_satisfying_rgv1_envelope(monkeypatch):
    env = _rgv1(mem_expected=1 * _MIB)
    assert _guard_with_cap(monkeypatch, _FakeCap(mem_mb=8192), env) is None


def test_guard_unknown_memory_still_passes(monkeypatch):
    env = _rgv1(mem_certainty="unknown")
    assert _guard_with_cap(monkeypatch, _FakeCap(), env) is None


def test_guard_gpu_required_needs_gpu_worker(monkeypatch):
    env = _rgv1(gpu_required=True)
    assert _guard_with_cap(monkeypatch, _FakeCap(gpu=0), env) == "retry"
    assert _guard_with_cap(
        monkeypatch, _FakeCap(gpu=2), env) is None
