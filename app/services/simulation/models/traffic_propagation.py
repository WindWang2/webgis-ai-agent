"""交通潮汐动态扩散模型（ADR-0192 D5 / spec §4.2）。

基于路网拓扑图的**水平队列 + 重力模型转向分配**：

    注入   q_e += min(D_e·dt, storage_e − q_e)          # 入口需求（source 记账）
    服务   o_e = min(q_e, C_e(t)·dt)                    # C_e(t) 含瓶颈折减
    转向   w(er→es) ∝ C_s / (1+t_s)^γ，逐发送者归一化     # 重力模型
    节流   接收量超出下游存容余量时按比例退回上游          # spillback
    驶出   egress 边（头节点无出边）服务量计入 sinks
    派生   c_e = q_e / storage_e ∈ [0,1]                # 拥堵指数（占用率）

选型理由（ADR-0192 D5）：状态变量取排队车辆数（天然守恒），拥堵蔓延由
下游存容节流**内生**产生——瓶颈塞死后未被放行的车辆物理地滞留在上游边，
拥堵指数因此按步长向上游相邻边蔓延。这比直接对拥堵指数做图卷积扩散更
可辩护：clamp 会破坏守恒，而溢流语义在这里是动力学的一部分而非补丁。

守恒恒等式（每步严格）：``Σq_new = Σq_old + injected − exited``。
模型无条件数值稳定（全部 min/比例封顶）；``validate_stability`` 强制的
是**模型有效性包络**：dt 不得超过最小行程时间 / 最小存容服务时间的
1/4——时间分辨率不足以分辨队列动力学时诚实拒绝，不输出噪声。

已知边界：逐边 Python 循环（确定性优先）；万边级路网 × 万步级推演应在
Celery worker 内运行并接受相应墙钟成本，更大规模需向量化迭代器（后续 ADR）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Sequence

import numpy as np

from app.services.simulation.errors import SimulationStabilityError
from app.services.simulation.laws import DynamicPropagationLaw, StepAccounting
from app.services.simulation.state import GraphStateMatrix, GraphTopology

if TYPE_CHECKING:
    from app.services.simulation.contracts import TrafficSimulationParams
    from app.services.simulation.state import SpatialStateMatrix

#: 拥堵速度折减的线性化 BPR 系数：v = v_f / (1 + _BPR_A·c)。
_BPR_A = 0.8
#: 模型有效性包络的安全系数（dt ≤ 0.25·min(行程时间, 存容服务时间)）。
_DT_ENVELOPE_SAFETY = 0.25


class TrafficPropagationModel(DynamicPropagationLaw):
    """水平队列拥堵蔓延模型（守恒变量：排队车辆数，veh）。"""

    conserved_variable = "queue_veh"
    product_variable = "queue_veh"
    product_threshold = 0.0
    layer_kind = "network_edges"

    def __init__(self, params: "TrafficSimulationParams"):
        super().__init__(params)
        self.topology = GraphTopology.from_edges(
            params.edges, jam_density_veh_per_km=params.jam_density_veh_per_km,
        )
        topo = self.topology
        n = len(topo.edge_ids)
        self._demand = np.zeros(n)
        for edge_id, veh_h in params.boundary.demand_veh_h.items():
            self._demand[topo.edge_index[edge_id]] = veh_h
        self._capacity = topo.capacity_veh_h.copy()
        self._v_free_ms = topo.free_flow_speed_kmh / 3.6
        self._gamma = float(params.gravity_gamma)
        bottleneck = params.boundary.bottleneck
        self._bottleneck_edges = (
            [topo.edge_index[e] for e in bottleneck.edge_ids]
            if bottleneck else []
        )
        self._bottleneck_factor = (
            float(bottleneck.capacity_factor) if bottleneck else 1.0
        )
        self._bottleneck_start = (
            int(bottleneck.start_tick) if bottleneck else 1
        )
        self._bottleneck_duration = (
            int(bottleneck.duration_ticks)
            if bottleneck is not None and bottleneck.duration_ticks is not None
            else None
        )

    # ── law 接口 ──────────────────────────────────────────────────────────

    def build_initial_state(self) -> GraphStateMatrix:
        zeros = np.zeros(len(self.topology.edge_ids))
        state = GraphStateMatrix(self.topology, {self.conserved_variable: zeros})
        state.validate()
        return state

    def validate_stability(self, state, dt_seconds: float) -> float:
        """模型有效性包络：dt ≤ 0.25·min(最小行程时间, 最小存容服务时间)。"""
        topo = self.topology
        travel_s = topo.lengths_m / np.maximum(self._v_free_ms, 1e-9)
        service_s = topo.storage_veh / np.maximum(self._capacity, 1e-9) * 3600.0
        dt_max = _DT_ENVELOPE_SAFETY * float(min(travel_s.min(), service_s.min()))
        if dt_seconds > dt_max:
            raise SimulationStabilityError(
                f"dt={dt_seconds:.6g}s exceeds model-validity envelope "
                f"dt_max={dt_max:.6g}s (queue dynamics unresolvable)",
                context={"dt": float(dt_seconds), "dt_max": dt_max},
            )
        return dt_max

    def advance(
        self, state: SpatialStateMatrix, dt_seconds: float, tick: int,
    ) -> tuple[GraphStateMatrix, StepAccounting]:
        topo = self.topology
        q = state.variables[self.conserved_variable].copy()
        dt_h = dt_seconds / 3600.0
        cap_eff = self._effective_capacity(tick)

        # 1) 入口需求注入（存容节流：网络已满时需求在网外排队，不入账）
        room = np.maximum(topo.storage_veh - q, 0.0)
        inject = np.minimum(self._demand * dt_h, room)
        q += inject

        # 2) 逐边服务 + 重力转向 + 溢流节流（确定性：按边索引升序）
        exited = 0.0
        for e in range(len(q)):
            served = min(float(q[e]), float(cap_eff[e]) * dt_h)
            if served <= 0.0:
                continue
            q[e] -= served
            outs = topo.out_edges_of_node(int(topo.head[e]))
            if outs.size == 0:
                exited += served  # egress：驶出网络（sink 记账）
                continue
            weights = self.gravity_turn_weights(
                capacities=cap_eff[outs].tolist(),
                travel_times=self._congested_travel_times(q, outs).tolist(),
                gamma=self._gamma,
            )
            want = np.array([served * weights[i] for i in range(outs.size)])
            room_out = np.maximum(topo.storage_veh[outs] - q[outs], 0.0)
            allowed = np.minimum(want, room_out)
            total_allowed = float(allowed.sum())
            if total_allowed > served:
                # 重力权重归一化的 ulp 级残差会让 Σallowed 微超 served ——
                # 等比收回，保证发送方不被透支成负队列（分岔路网必现路径）
                allowed *= served / total_allowed
            q[outs] += allowed
            q[e] += max(served - float(allowed.sum()), 0.0)  # 未放行：退回上游排队

        new_state = GraphStateMatrix(topo, {self.conserved_variable: q})
        accounting = StepAccounting(
            sources={"demand_injection": float(inject.sum())},
            sinks={"egress_exit": exited},
        )
        return new_state, accounting

    def feature_extras(self, state: SpatialStateMatrix) -> dict[str, np.ndarray]:
        q = state.variables[self.conserved_variable]
        return {
            "congestion_index": q / np.maximum(self.topology.storage_veh, 1e-9),
        }

    def derived_metrics(self, state: SpatialStateMatrix) -> dict[str, float]:
        c = self.feature_extras(state)["congestion_index"]
        return {
            "max_congestion_index": float(c.max()),
            "mean_congestion_index": float(c.mean()),
        }

    # ── 转向权重与容量 ─────────────────────────────────────────────────────

    @staticmethod
    def gravity_turn_weights(
        capacities: Sequence[float],
        travel_times: Sequence[float],
        gamma: float = 1.0,
    ) -> dict[int, float]:
        """重力模型转向权重：w ∝ C/(1+t)^γ，逐发送者归一化到 1。

        全零容量（极端瓶颈）时退化为均匀权重——转向选择必须有出路，
        否则队列会因除零而静默丢车。
        """
        raw = {
            i: (c / (1.0 + t) ** gamma) if c > 0 else 0.0
            for i, (c, t) in enumerate(zip(capacities, travel_times))
        }
        total = sum(raw.values())
        if total <= 0:
            n = len(raw)
            return {i: 1.0 / n for i in raw}
        return {i: v / total for i, v in raw.items()}

    def _congested_travel_times(self, q: np.ndarray, outs: np.ndarray) -> np.ndarray:
        """下游边的拥堵行程时间：v = v_f/(1+0.8·c)（重力模型的阻抗输入）。"""
        topo = self.topology
        occupancy = np.clip(
            q[outs] / np.maximum(topo.storage_veh[outs], 1e-9), 0.0, 1.0,
        )
        v = self._v_free_ms[outs] / (1.0 + _BPR_A * occupancy)
        return topo.lengths_m[outs] / np.maximum(v, 1e-6)

    def _effective_capacity(self, tick: int) -> np.ndarray:
        """瓶颈时间窗内的折减容量（窗口关闭后恢复，排队开始消化）。"""
        cap = self._capacity
        if not self._bottleneck_edges:
            return cap
        if tick < self._bottleneck_start:
            return cap
        if (
            self._bottleneck_duration is not None
            and tick >= self._bottleneck_start + self._bottleneck_duration
        ):
            return cap
        cap = cap.copy()
        cap[self._bottleneck_edges] *= self._bottleneck_factor
        return cap


__all__ = ["TrafficPropagationModel"]
