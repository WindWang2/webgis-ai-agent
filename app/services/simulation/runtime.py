"""仿真运行时：生命周期、Tick 调度与守恒对账（ADR-0192 D3 / spec §5）。

``SimulationRuntime`` 是时空状态机的执行体：

    初始化（law.build_initial_state + T0 产物）
      → 每步：稳定域守卫 → advance → 守恒对账（fail-loud）→ 诊断
      → 产物发射（tick % stride == 0 或末步）→ 进度回调 / 取消检查
      → 终态（completed / failed / cancelled）+ 守恒报告

设计红线：
- 运行时不 import Celery / DB —— 纯同步计算体；异步流转由 tasks.py 包装
  （durable job），eager 直调路径仅供测试；
- 守恒破坏是 **fail-loud** 的模型 bug（残差 > 1e-6 相对 → SimulationStateError），
  绝不静默输出发散结果；
- 确定性：同参数同步数逐元素一致（检查点续跑等价性 / 内容寻址 run_id）。
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Callable, Optional

import numpy as np

from app.services.simulation.contracts import (
    CONSERVATION_FAIL_REL,
    CONSERVATION_PASS_REL,
    MAX_SLICE_FEATURES,
    ConservationReport,
    SimulationModelKind,
    SimulationParams,
    SimulationRunConfig,
    SimulationStatus,
    SimulationStep,
    SimulationStepDiagnostics,
    TemporalLayerProduct,
    parse_params,
)
from app.services.simulation.errors import (
    InvalidSimulationTransition,
    SimulationBudgetError,
    SimulationCancelledError,
    SimulationStateError,
)
from app.services.simulation.laws import DynamicPropagationLaw, build_law
from app.services.simulation.layers import (
    InMemoryTemporalSink,
    LayerDraft,
    TemporalSink,
)
from app.services.simulation.state import (
    SpatialStateMatrix,
    restore_state_arrays,
)

ProgressCallback = Callable[[int, int, str], None]


@dataclass
class SimulationResult:
    """一次推演的完整结果（final_state 是内存对象，不进线协议）。"""

    run_id: str
    model_kind: SimulationModelKind
    status: SimulationStatus
    n_steps: int
    dt_seconds: float
    steps: list[SimulationStep] = field(default_factory=list)
    products: list[TemporalLayerProduct] = field(default_factory=list)
    conservation_report: Optional[ConservationReport] = None
    final_metrics: dict[str, float] = field(default_factory=dict)
    final_state: Optional[SpatialStateMatrix] = None


class SimulationRuntime:
    """时空动态仿真运行时（生命周期 + Tick Scheduler + 守恒对账）。"""

    _ALLOWED_TRANSITIONS = {
        SimulationStatus.PENDING: {
            SimulationStatus.RUNNING,
            SimulationStatus.CANCELLED,
        },
        SimulationStatus.RUNNING: {
            SimulationStatus.COMPLETED,
            SimulationStatus.FAILED,
            SimulationStatus.CANCELLED,
        },
    }

    def __init__(
        self,
        params: SimulationParams,
        config: SimulationRunConfig,
        *,
        law: Optional[DynamicPropagationLaw] = None,
        sink: Optional[TemporalSink] = None,
        run_id: Optional[str] = None,
    ):
        self.params = params
        self.config = config
        self.law = law if law is not None else build_law(params)
        self.sink = sink if sink is not None else InMemoryTemporalSink()
        self.run_id = run_id or self._derive_run_id(params, config)
        self.status = SimulationStatus.PENDING
        self._state: Optional[SpatialStateMatrix] = None
        self._steps: list[SimulationStep] = []
        self._products: list[TemporalLayerProduct] = []
        self._initial_total = 0.0
        self._expected_total = 0.0
        self._net_sources = 0.0
        self._net_sinks = 0.0
        self._tick = 0
        self._cancelled = False
        self.events: list[str] = []

    # ── 公共面 ────────────────────────────────────────────────────────────

    def cancel(self) -> None:
        """请求取消（协作式：run 循环在下一个检查点收敛）。"""
        self._cancelled = True
        self.events.append("cancel_requested")

    def run(
        self, progress_callback: Optional[ProgressCallback] = None,
    ) -> SimulationResult:
        """执行到 config.n_steps；从检查点恢复时继续推进剩余步。"""
        if SimulationStatus.RUNNING not in self._ALLOWED_TRANSITIONS.get(
            self.status, set(),
        ):
            raise InvalidSimulationTransition(
                f"cannot run from status '{self.status.value}'",
                context={"from": self.status.value, "to": "running"},
            )
        self.status = SimulationStatus.RUNNING
        self.events.append("running")
        started = time.monotonic()
        try:
            if self._cancelled:
                raise SimulationCancelledError()
            if self._state is None:
                self._initialize()
            while self._tick < self.config.n_steps:
                self._ensure_not_cancelled()
                self._ensure_budget(started)
                self.step()
                if progress_callback is not None:
                    progress_callback(
                        self._tick, self.config.n_steps, "simulate",
                    )
            report = self._conservation_report()
            self._transition(SimulationStatus.COMPLETED)
            self.events.append("completed")
            return self._build_result(report)
        except SimulationCancelledError:
            self._transition(SimulationStatus.CANCELLED)
            self.events.append("cancelled")
            raise
        except Exception as exc:
            from app.lib.cancellation import OperationCancelled

            if isinstance(exc, OperationCancelled):
                # durable job 取消令牌在推演循环内触发：运行时与 job 行的
                # 终态语义对齐（用户取消 ≠ 执行失败）。
                self._transition(SimulationStatus.CANCELLED)
                self.events.append("cancelled")
                raise
            self._transition(SimulationStatus.FAILED)
            self.events.append("failed")
            raise

    def step(self) -> SimulationStep:
        """推进单个时间步（Tick Scheduler 的最小单位；可被测试单步驱动）。

        只允许在 ``run()`` 建立的 RUNNING 态内调用——绕过状态机的直接
        推演被显式拒绝（PENDING 直调 / 终态加跑都是契约违例）。
        """
        if self.status is not SimulationStatus.RUNNING:
            raise InvalidSimulationTransition(
                f"cannot step from status '{self.status.value}'",
                context={"from": self.status.value, "to": "running"},
            )
        self._ensure_not_cancelled()
        if self._state is None:
            self._initialize()
        dt = self.config.dt_seconds
        tick = self._tick + 1
        var = self.law.conserved_variable

        dt_max = self.law.validate_stability(self._state, dt)
        t0 = time.perf_counter()
        new_state, accounting = self.law.advance(self._state, dt, tick)
        new_state.validate()

        self._expected_total += accounting.net()
        self._net_sources += sum(accounting.sources.values())
        self._net_sinks += sum(accounting.sinks.values())
        total = new_state.total(var)
        residual = total - self._expected_total
        rel = abs(residual) / max(1.0, abs(self._expected_total))
        if rel > CONSERVATION_FAIL_REL:
            raise SimulationStateError(
                f"conservation accounting failed at tick {tick}: "
                f"total={total!r} expected={self._expected_total!r} "
                f"(rel={rel:.3e})",
                context={
                    "tick": tick,
                    "total": total,
                    "expected": self._expected_total,
                    "relative_error": rel,
                },
            )

        wall_ms = (time.perf_counter() - t0) * 1000.0
        timestamp = self._timestamp_for(tick)
        product = None
        if tick % self.config.output_stride == 0 or tick == self.config.n_steps:
            product = self._emit_product(tick)
        diagnostics = SimulationStepDiagnostics(
            tick=tick,
            sim_seconds=tick * dt,
            timestamp=timestamp,
            conserved_total=total,
            expected_total=self._expected_total,
            mass_error=residual,
            mass_error_relative=rel,
            min_value=new_state.minmax(var)[0],
            max_value=new_state.minmax(var)[1],
            finite=True,
            stability_dt_max=dt_max if np.isfinite(dt_max) else None,
            wall_ms=wall_ms,
            metrics=self.law.derived_metrics(new_state),
        )
        sim_step = SimulationStep(
            tick=tick,
            sim_seconds=tick * dt,
            timestamp=timestamp,
            diagnostics=diagnostics,
            layer=product,
        )
        self._state = new_state
        self._tick = tick
        self._steps.append(sim_step)
        return sim_step

    def to_checkpoint(self) -> dict[str, Any]:
        """可序列化检查点（状态数组 + 记账累计 + 已产产物）。"""
        if self._state is None:
            raise SimulationStateError(
                "cannot checkpoint before initialization",
                context={"phase": "checkpoint"},
            )
        return {
            "run_id": self.run_id,
            "model_kind": self.params.model_kind.value,
            "params": self.params.model_dump(mode="json"),
            "config": self.config.model_dump(mode="json"),
            "status": self.status.value,
            "tick": self._tick,
            "initial_total": self._initial_total,
            "expected_total": self._expected_total,
            "net_sources": self._net_sources,
            "net_sinks": self._net_sinks,
            "state_variables": {
                k: v.tolist() for k, v in self._state.variables.items()
            },
            "steps": [s.model_dump(mode="json") for s in self._steps],
            "products": [p.model_dump(mode="json") for p in self._products],
            "events": list(self.events),
        }

    @classmethod
    def from_checkpoint(
        cls, checkpoint: dict[str, Any], config: SimulationRunConfig,
    ) -> "SimulationRuntime":
        """从检查点恢复运行时（config 可换目标步数以续跑）。"""
        params = parse_params(checkpoint["params"])
        law = build_law(params)
        # 可续跑 = PENDING/RUNNING/COMPLETED（completed 是"到达配置视界"的
        # 合法续跑点——换更长目标步数续推正是检查点的用途）；
        # failed/cancelled 携带中断语义，状态完整性无保证 → 拒绝。
        saved_status = SimulationStatus(
            checkpoint.get("status", SimulationStatus.PENDING.value))
        if saved_status in (SimulationStatus.FAILED, SimulationStatus.CANCELLED):
            raise SimulationStateError(
                f"cannot resume from '{saved_status.value}' checkpoint "
                "(interrupted states carry no integrity guarantee)",
                context={"status": saved_status.value},
            )
        rt = cls(params, config, law=law, run_id=checkpoint["run_id"])
        state = law.build_initial_state()
        restore_state_arrays(state, checkpoint["state_variables"])
        rt._state = state
        rt._tick = int(checkpoint["tick"])
        rt._initial_total = float(checkpoint["initial_total"])
        rt._expected_total = float(checkpoint["expected_total"])
        rt._net_sources = float(checkpoint["net_sources"])
        rt._net_sinks = float(checkpoint["net_sinks"])
        rt._products = [
            TemporalLayerProduct.model_validate(p)
            for p in checkpoint["products"]
        ]
        rt._steps = [
            SimulationStep.model_validate(s) for s in checkpoint.get("steps", [])
        ]
        rt.events = list(checkpoint.get("events", []))
        return rt

    # ── 内部 ──────────────────────────────────────────────────────────────

    @staticmethod
    def _derive_run_id(
        params: SimulationParams, config: SimulationRunConfig,
    ) -> str:
        payload = json.dumps(
            {
                "p": params.model_dump(mode="json"),
                "c": config.model_dump(mode="json"),
            },
            sort_keys=True, ensure_ascii=False, default=str,
        )
        return "sim" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:10]

    def _initialize(self) -> None:
        self._state = self.law.build_initial_state()
        self._state.validate()
        var = self.law.conserved_variable
        self._initial_total = self._state.total(var)
        self._expected_total = self._initial_total
        self.events.append("state_initialized")
        self._emit_product(0)

    def _ensure_not_cancelled(self) -> None:
        if self._cancelled:
            raise SimulationCancelledError()

    def _ensure_budget(self, started: float) -> None:
        budget = self.config.max_wall_seconds
        if budget is not None and (time.monotonic() - started) > budget:
            raise SimulationBudgetError(
                f"simulation exceeded wall-clock budget ({budget:.3g}s)",
                context={"budget_seconds": float(budget)},
            )

    def _timestamp_for(self, tick: int) -> Optional[str]:
        if self.config.start_time is None:
            return None
        ts = self.config.start_time + timedelta(
            seconds=tick * self.config.dt_seconds)
        return ts.isoformat()

    def _crs(self) -> Optional[str]:
        grid = getattr(self.params, "grid", None)
        return grid.crs if grid is not None else None

    def _emit_product(self, tick: int) -> TemporalLayerProduct:
        extras = self.law.feature_extras(self._state)
        features, bbox, truncated = self._state.to_features(
            self.law.product_variable,
            threshold=self.law.product_threshold,
            max_features=MAX_SLICE_FEATURES,
            extras=extras,
        )
        draft = LayerDraft(
            product_id=f"sim-{self.run_id}-t{tick}",
            tick=tick,
            sim_seconds=tick * self.config.dt_seconds,
            timestamp=self._timestamp_for(tick),
            layer_kind=self.law.layer_kind,
            crs=self._crs(),
            features=features,
            bbox=bbox,
            truncated=truncated,
        )
        product = self.sink.emit(draft)
        self._products.append(product)
        return product

    def _conservation_report(self) -> ConservationReport:
        var = self.law.conserved_variable
        final_total = self._state.total(var)
        expected = self._expected_total
        residual = final_total - expected
        rel = abs(residual) / max(1.0, abs(expected))
        return ConservationReport(
            variable=var,
            initial_total=self._initial_total,
            final_total=final_total,
            net_sources=self._net_sources,
            net_sinks=self._net_sinks,
            expected_final=expected,
            residual=residual,
            residual_relative=rel,
            passed=bool(rel <= CONSERVATION_PASS_REL),
        )

    def _build_result(self, report: ConservationReport) -> SimulationResult:
        return SimulationResult(
            run_id=self.run_id,
            model_kind=self.params.model_kind,
            status=self.status,
            n_steps=self._tick,
            dt_seconds=self.config.dt_seconds,
            steps=list(self._steps),
            products=list(self._products),
            conservation_report=report,
            final_metrics=self.law.derived_metrics(self._state),
            final_state=self._state,
        )

    def _transition(self, target: SimulationStatus) -> None:
        allowed = self._ALLOWED_TRANSITIONS.get(self.status, set())
        if target not in allowed:
            raise InvalidSimulationTransition(
                f"illegal lifecycle transition "
                f"'{self.status.value}' → '{target.value}'",
                context={"from": self.status.value, "to": target.value},
            )
        self.status = target


__all__ = ["SimulationResult", "SimulationRuntime"]
