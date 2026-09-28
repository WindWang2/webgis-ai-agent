"""SpecialistDispatcher 归一化矩阵与 runtime 异常折算（ADR-0187 §3）。

``normalize_receipt`` 是「Zero Big Data in Context」第二道强制点：任意
runtime 返回值 → 合法提货券（词表合法化 / 非 ref 剔除 / 期望产出零券
降级 / 字段截断），且**绝不向上抛**。``SpecialistDispatcher.dispatch``
把 runtime 异常折算为诚实 FAILED 券（取消除外）。
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

import pytest

from app.services.agent_swarm.delegation_contracts import (
    MAX_ERROR_CHARS,
    MAX_REFS_PER_RECEIPT,
    MAX_SUMMARY_CHARS,
    SpecialistAssignment,
    SubagentReceipt,
    SwarmErrorCode,
    SwarmReceiptStatus,
    SwarmSpecialistRole,
    SwarmTaskDescriptor,
    WorldStateProjection,
)
from app.services.agent_swarm.dispatcher import (
    SpecialistDispatcher,
    SubagentDispatcherRuntime,
    _compose_task_text,
    _role_name,
    normalize_receipt,
)


def _task(**kw: Any) -> SwarmTaskDescriptor:
    kw.setdefault("task_id", "t1")
    kw.setdefault("role", SwarmSpecialistRole.DATA_HUNTER)
    kw.setdefault("goal", "探测任务")
    return SwarmTaskDescriptor(**kw)


def _assignment(**kw: Any) -> SpecialistAssignment:
    return SpecialistAssignment(
        assignment_id=kw.pop("assignment_id", "asg-1"),
        task=kw.pop("task", _task(**kw)),
        projection=WorldStateProjection(session_id="s1"),
    )


class _ObjWithToDict:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def to_dict(self) -> dict[str, Any]:
        return self._data


class _OpaqueObject:
    """无 to_dict 的任意对象（归一化必须兜底为 FAILED，不抛）。"""


class TestNormalizeReceiptMatrix:
    """任意返回值 → 合法券的归一化矩阵。"""

    def test_receipt_instance_passthrough(self):
        raw = SubagentReceipt(
            assignment_id="asg-1",
            task_id="t1",
            role=SwarmSpecialistRole.DATA_HUNTER,
            status=SwarmReceiptStatus.SUCCEEDED,
            produced_refs=["ref:a"],
        )
        out = normalize_receipt(_assignment(), raw)
        assert out.status == SwarmReceiptStatus.SUCCEEDED
        assert out.produced_refs == ["ref:a"]

    def test_dict_input_mapped(self):
        out = normalize_receipt(
            _assignment(),
            {"status": "succeeded", "produced_refs": ["ref:a"], "summary": "ok"},
        )
        assert out.status == SwarmReceiptStatus.SUCCEEDED
        assert out.summary == "ok"
        assert out.role == SwarmSpecialistRole.DATA_HUNTER

    def test_to_dict_object_accepted(self):
        out = normalize_receipt(
            _assignment(), _ObjWithToDict({"status": "succeeded", "produced_refs": ["ref:a"]})
        )
        assert out.status == SwarmReceiptStatus.SUCCEEDED
        assert out.produced_refs == ["ref:a"]

    def test_opaque_object_fails_closed(self):
        out = normalize_receipt(_assignment(), _OpaqueObject())
        assert out.status == SwarmReceiptStatus.FAILED
        assert out.produced_refs == []
        assert out.assignment_id == "asg-1"

    def test_non_ref_outputs_stripped(self):
        out = normalize_receipt(
            _assignment(),
            {"status": "succeeded", "produced_refs": ["ref:a", "inline-geojson", 42]},
        )
        assert out.produced_refs == ["ref:a"]

    def test_produced_refs_capped(self):
        out = normalize_receipt(
            _assignment(),
            {"status": "succeeded", "produced_refs": [f"ref:r{i}" for i in range(20)]},
        )
        assert len(out.produced_refs) == MAX_REFS_PER_RECEIPT

    def test_succeeded_without_refs_degrades_honestly(self):
        out = normalize_receipt(
            _assignment(expected_outputs=("d1",)), {"status": "succeeded"}
        )
        assert out.status == SwarmReceiptStatus.DEGRADED
        assert out.degraded is True
        assert out.error_code == "non_compliant"

    def test_succeeded_with_refs_keeps_status(self):
        out = normalize_receipt(
            _assignment(expected_outputs=("d1",)),
            {"status": "succeeded", "produced_refs": ["ref:a"]},
        )
        assert out.status == SwarmReceiptStatus.SUCCEEDED
        assert out.degraded is False

    def test_no_expected_outputs_means_no_degradation(self):
        out = normalize_receipt(_assignment(), {"status": "succeeded"})
        assert out.status == SwarmReceiptStatus.SUCCEEDED

    def test_illegal_role_falls_back_to_task_role(self):
        out = normalize_receipt(
            _assignment(), {"status": "succeeded", "role": "wizard_king"}
        )
        assert out.role == SwarmSpecialistRole.DATA_HUNTER

    def test_illegal_status_fails_closed(self):
        out = normalize_receipt(_assignment(), {"status": "quantum"})
        assert out.status == SwarmReceiptStatus.FAILED

    def test_missing_status_fails_closed(self):
        out = normalize_receipt(_assignment(), {"summary": "no status here"})
        assert out.status == SwarmReceiptStatus.FAILED

    def test_summary_and_error_clipped(self):
        out = normalize_receipt(
            _assignment(),
            {"status": "failed", "summary": "s" * 900, "error": "e" * 900},
        )
        assert len(out.summary) == MAX_SUMMARY_CHARS
        assert len(out.error) == MAX_ERROR_CHARS

    def test_identity_fields_fall_back_to_assignment(self):
        out = normalize_receipt(_assignment(assignment_id="asg-fallback"), {})
        assert out.assignment_id == "asg-fallback"
        assert out.task_id == "t1"

    def test_numeric_fields_coerced_from_strings(self):
        out = normalize_receipt(
            _assignment(),
            {
                "status": "succeeded",
                "attempts": "3",
                "heartbeats": "7",
                "wall_time_s": "1.5",
                "finished_at": "99.0",
            },
        )
        assert out.attempts == 3
        assert out.heartbeats == 7
        assert out.wall_time_s == 1.5
        assert out.finished_at == 99.0

    def test_garbage_numeric_fields_default_zero(self):
        out = normalize_receipt(
            _assignment(),
            {"status": "succeeded", "attempts": None, "wall_time_s": ""},
        )
        assert out.attempts == 1
        assert out.wall_time_s == 0.0


class TestDispatchExceptionFolding:
    """runtime 异常 → 诚实 FAILED 券（取消除外）。"""

    async def test_runtime_exception_folds_to_retryable_receipt(self):
        class _Exploding:
            async def execute(self, assignment, *, on_heartbeat=None):
                raise RuntimeError("subprocess crashed")

        receipt = await SpecialistDispatcher(_Exploding()).dispatch(_assignment())
        assert receipt.status == SwarmReceiptStatus.FAILED
        assert receipt.error_code == SwarmErrorCode.RETRYABLE
        assert receipt.error.startswith("RuntimeError: subprocess crashed")

    async def test_timeout_folds_to_timeout_receipt(self):
        class _Slow:
            async def execute(self, assignment, *, on_heartbeat=None):
                raise TimeoutError("deadline")

        receipt = await SpecialistDispatcher(_Slow()).dispatch(_assignment())
        assert receipt.status == SwarmReceiptStatus.FAILED
        assert receipt.error_code == SwarmErrorCode.TIMEOUT

    async def test_cancellation_propagates(self):
        class _Cancelling:
            async def execute(self, assignment, *, on_heartbeat=None):
                raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await SpecialistDispatcher(_Cancelling()).dispatch(_assignment())

    async def test_happy_path_receipt_roundtrip(self):
        class _Ok:
            async def execute(self, assignment, *, on_heartbeat=None):
                return SubagentReceipt(
                    assignment_id=assignment.assignment_id,
                    task_id=assignment.task.task_id,
                    role=assignment.task.role,
                    status=SwarmReceiptStatus.SUCCEEDED,
                    produced_refs=["ref:ok"],
                )

        receipt = await SpecialistDispatcher(_Ok()).dispatch(_assignment())
        assert receipt.status == SwarmReceiptStatus.SUCCEEDED
        assert receipt.produced_refs == ["ref:ok"]

    async def test_heartbeat_callback_wired_through(self):
        seen: list[str] = []

        class _Beating:
            async def execute(self, assignment, *, on_heartbeat=None):
                if on_heartbeat is not None:
                    on_heartbeat("halfway")
                return {"status": "succeeded", "produced_refs": ["ref:a"]}

        receipt = await SpecialistDispatcher(_Beating()).dispatch(
            _assignment(), on_heartbeat=seen.append
        )
        assert seen == ["halfway"]
        assert receipt.status == SwarmReceiptStatus.SUCCEEDED

    async def test_default_runtime_is_production_adapter(self):
        dispatcher = SpecialistDispatcher()
        assert isinstance(dispatcher._runtime, SubagentDispatcherRuntime)


# ─────────────────────────── 生产适配器（SubagentDispatcher 包装） ───────────────────────────


class _FakeRunResult:
    def __init__(self, *, success: bool, refs=None, summary: str = "", error: str = ""):
        self.success = success
        self.refs = refs or []
        self.summary = summary
        self.error = error


class _FakeSubagentDispatcher:
    """替身：记录 task/role，返回脚本化结果。"""

    instances: list["_FakeSubagentDispatcher"] = []

    def __init__(self, registry: Any, session_id: str) -> None:
        self.registry = registry
        self.session_id = session_id
        self.calls: list[dict[str, Any]] = []
        _FakeSubagentDispatcher.instances.append(self)

    async def run(self, *, task: str, role: Optional[str] = None, **kwargs: Any):
        self.calls.append({"task": task, "role": role, **kwargs})
        return _FakeSubagentDispatcher.result


_FakeSubagentDispatcher.result = _FakeRunResult(success=True, refs=["ref:sub"])


@pytest.fixture
def fake_subagent(monkeypatch):
    """把生产 SubagentDispatcher 换成进程内替身（不打 LLM）。"""
    _FakeSubagentDispatcher.instances = []
    _FakeSubagentDispatcher.result = _FakeRunResult(
        success=True, refs=["ref:sub"], summary="subagent done"
    )
    monkeypatch.setattr(
        "app.services.subagent.SubagentDispatcher", _FakeSubagentDispatcher
    )
    return _FakeSubagentDispatcher


class TestSubagentDispatcherRuntime:
    async def test_success_path_builds_succeeded_receipt(self, fake_subagent):
        runtime = SubagentDispatcherRuntime()
        receipt = await runtime.execute(_assignment())
        assert receipt.status == SwarmReceiptStatus.SUCCEEDED
        assert receipt.produced_refs == ["ref:sub"]
        assert receipt.summary == "subagent done"
        assert receipt.wall_time_s >= 0.0
        assert fake_subagent.instances[0].session_id == "s1"

    async def test_task_text_carries_bounded_projection(self, fake_subagent):
        assignment = _assignment()
        assignment.projection.facts = ["事实一", "事实二"]
        assignment.projection.relevant_refs = ["ref:up-1", "ref:up-2"]
        await SubagentDispatcherRuntime().execute(assignment)
        task_text = fake_subagent.instances[0].calls[0]["task"]
        assert task_text.startswith("探测任务")
        assert "上下文事实: 事实一 | 事实二" in task_text
        assert "可用上游产物券: ref:up-1 ref:up-2" in task_text

    async def test_honest_failure_folds_to_non_retryable(self, fake_subagent):
        fake_subagent.result = _FakeRunResult(success=False, error="role denied")
        receipt = await SubagentDispatcherRuntime().execute(_assignment())
        assert receipt.status == SwarmReceiptStatus.FAILED
        assert receipt.error_code == SwarmErrorCode.NON_RETRYABLE
        assert receipt.error == "role denied"

    async def test_missing_error_gets_default_message(self, fake_subagent):
        fake_subagent.result = _FakeRunResult(success=False)
        receipt = await SubagentDispatcherRuntime().execute(_assignment())
        assert receipt.error == "subagent honest failure"

    async def test_exception_folds_to_retryable(self, fake_subagent, monkeypatch):
        def _boom(self, *args, **kwargs):
            raise RuntimeError("llm gateway down")

        monkeypatch.setattr(fake_subagent, "__init__", _boom)
        receipt = await SubagentDispatcherRuntime().execute(_assignment())
        assert receipt.status == SwarmReceiptStatus.FAILED
        assert receipt.error_code == SwarmErrorCode.RETRYABLE
        assert "llm gateway down" in receipt.error

    async def test_timeout_folds_to_timeout_code(self, fake_subagent, monkeypatch):
        def _boom(self, *args, **kwargs):
            raise TimeoutError("llm deadline")

        monkeypatch.setattr(fake_subagent, "__init__", _boom)
        receipt = await SubagentDispatcherRuntime().execute(_assignment())
        assert receipt.error_code == SwarmErrorCode.TIMEOUT

    async def test_refs_capped_and_summary_clipped(self, fake_subagent):
        fake_subagent.result = _FakeRunResult(
            success=True,
            refs=[f"ref:r{i}" for i in range(30)],
            summary="s" * 900,
        )
        receipt = await SubagentDispatcherRuntime().execute(_assignment())
        assert len(receipt.produced_refs) == MAX_REFS_PER_RECEIPT
        assert len(receipt.summary) == MAX_SUMMARY_CHARS

    async def test_error_message_clipped(self, fake_subagent):
        fake_subagent.result = _FakeRunResult(success=False, error="e" * 900)
        receipt = await SubagentDispatcherRuntime().execute(_assignment())
        assert len(receipt.error) == MAX_ERROR_CHARS


class TestRoleResolution:
    """角色名解析：注册角色透传，未知角色降级无角色委派。"""

    def test_registered_specialist_role_resolves(self):
        from app.services.agent_swarm.registry import ensure_subagent_roles_registered

        ensure_subagent_roles_registered()  # 幂等；隔离运行本文件时自举
        assert _role_name(SwarmSpecialistRole.CARTOGRAPHY_SPECIALIST) == (
            "cartography_specialist"
        )

    def test_unregistered_role_degrades_to_none(self):
        assert _role_name(SwarmSpecialistRole.DATA_HUNTER) is None

    def test_compose_task_text_bounded(self):
        assignment = _assignment()
        assignment.projection.facts = [f"f{i}" for i in range(20)]
        assignment.projection.relevant_refs = [f"ref:r{i}" for i in range(30)]
        text = _compose_task_text(assignment)
        assert len(text) <= 2000
        assert text.splitlines()[0] == "探测任务"

    def test_compose_task_text_minimal(self):
        text = _compose_task_text(_assignment())
        assert text == "探测任务"
