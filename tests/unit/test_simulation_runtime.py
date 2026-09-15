"""时空动态仿真运行时单元测试（ADR-0192 / docs/dev/spatial-simulation-spec.md）。

覆盖三组核心契约：
1. 数值完整性 —— 水文浸润的质量守恒恒等式（≤1e-9 相对残差）、无负水深、
   无发散溢出、CFL 稳定域拒绝；
2. 蔓延机理 —— 路网注入瓶颈后拥堵指数按步长向上游相邻边蔓延（水平队列
   spillback），车辆质量全程守恒；
3. 多时相产物 —— T0..TN 时间片产物 + MapSpec v1.2 ``layout.frames`` 真实
   schema 校验（``MapSpecDocument.model_validate``）。

golden 值来源：守恒/几何对称性是解析恒等式（非经验数）；蔓延次序由水平
队列模型的服务/存容关系决定（见 spec §4.2）。全部离线（conftest 钉死
memory:// broker），不依赖 DB / Redis / 网络。
"""

import numpy as np
import pytest
from pydantic import ValidationError

from app.services.simulation import (
    BottleneckSpec,
    GridSpec,
    HydroBoundaryCondition,
    HydroSimulationParams,
    InMemoryTemporalSink,
    InvalidSimulationTransition,
    NetworkEdgeSpec,
    RasterFieldSpec,
    RasterStateMatrix,
    SimulationCancelledError,
    SimulationConfigError,
    SimulationError,
    SimulationModelKind,
    SimulationRunConfig,
    SimulationRuntime,
    SimulationStateError,
    SimulationStabilityError,
    TrafficBoundaryCondition,
    TrafficSimulationParams,
    build_law,
    build_mapspec_bundle,
    run_simulation,
)
from app.services.simulation.laws import DynamicPropagationLaw, StepAccounting
from app.services.simulation.state import GraphTopology

# ── 构造辅助 ──────────────────────────────────────────────────────────────


def _grid(w=8, h=8, dx=10.0):
    return GridSpec(
        width=w, height=h, cell_size_m=dx, origin_x=0.0, origin_y=0.0,
        crs="EPSG:32650",
    )


def _field(value=None, values=None):
    if values is not None:
        return RasterFieldSpec(kind="array", values=values)
    return RasterFieldSpec(kind="constant", value=value if value is not None else 0.0)


def _hydro_params(
    grid=None,
    elevation=0.0,
    elevation_values=None,
    manning=0.015,
    h0=0.0,
    rain=0.0,
    rain_duration_s=None,
    drain=0.0,
    calib=1.0,
):
    return HydroSimulationParams(
        grid=grid or _grid(),
        elevation_m=_field(elevation, elevation_values),
        manning_n=_field(manning),
        initial_water_depth_m=_field(h0),
        boundary=HydroBoundaryCondition(
            rainfall_rate_m_per_s=_field(rain),
            rainfall_duration_s=rain_duration_s,
            drainage_rate_m_per_s=drain,
        ),
        diffusivity_calibration=calib,
    )


def _hydro_cfg(n=20, dt=5.0, stride=10):
    return SimulationRunConfig(dt_seconds=dt, n_steps=n, output_stride=stride)


def _line_traffic(n_edges=4, length=500.0, cap=1800.0, demand=1200.0,
                  bottleneck=True, cap_factor=0.02):
    edges = [
        NetworkEdgeSpec(
            edge_id=f"e{i + 1}", tail=f"n{i}", head=f"n{i + 1}",
            length_m=length, capacity_veh_h=cap, free_flow_speed_kmh=40.0,
        )
        for i in range(n_edges)
    ]
    return TrafficSimulationParams(
        edges=edges,
        boundary=TrafficBoundaryCondition(
            demand_veh_h={"e1": demand},
            bottleneck=(
                BottleneckSpec(
                    edge_ids=[f"e{n_edges}"], capacity_factor=cap_factor,
                )
                if bottleneck
                else None
            ),
        ),
    )


def _traffic_cfg(n=60, dt=10.0, stride=20):
    return SimulationRunConfig(dt_seconds=dt, n_steps=n, output_stride=stride)


# ── Contracts：参数 / 边界 / 配置校验 ─────────────────────────────────────


class TestContracts:
    def test_grid_rejects_zero_cell_size(self):
        with pytest.raises(ValidationError):
            _grid(dx=0.0)

    def test_negative_rainfall_rejected(self):
        with pytest.raises(ValidationError):
            _hydro_params(rain=-1e-4)

    def test_manning_out_of_physical_range_rejected(self):
        with pytest.raises(ValidationError):
            _hydro_params(manning=2.0)

    def test_ragged_field_array_rejected(self):
        with pytest.raises(ValidationError):
            _field(values=[[1.0, 2.0], [3.0]])

    def test_nonfinite_field_array_rejected(self):
        with pytest.raises(ValidationError):
            _field(values=[[float("nan"), 0.0], [0.0, 0.0]])

    def test_bottleneck_capacity_factor_must_reduce(self):
        with pytest.raises(ValidationError):
            BottleneckSpec(edge_ids=["e1"], capacity_factor=1.0)

    def test_unknown_demand_edge_rejected(self):
        bad = TrafficBoundaryCondition(demand_veh_h={"ghost": 100.0})
        with pytest.raises(ValidationError):
            TrafficSimulationParams(edges=[
                NetworkEdgeSpec(
                    edge_id="e1", tail="n0", head="n1", length_m=100.0,
                    capacity_veh_h=1800.0, free_flow_speed_kmh=40.0,
                )
            ], boundary=bad)

    def test_duplicate_edge_id_rejected(self):
        dup = NetworkEdgeSpec(
            edge_id="e1", tail="n0", head="n1", length_m=100.0,
            capacity_veh_h=1800.0, free_flow_speed_kmh=40.0,
        )
        with pytest.raises(ValidationError):
            TrafficSimulationParams(
                edges=[dup, dup.model_copy()],
                boundary=TrafficBoundaryCondition(demand_veh_h={}),
            )

    def test_frame_cap_guard_rejects_unviewable_stride(self):
        # 600/10 + 1 = 61 个切片 > MapSpec MAX_SPEC_FRAMES(50) → 构造期拒绝
        with pytest.raises(ValidationError):
            SimulationRunConfig(dt_seconds=10.0, n_steps=600, output_stride=10)

    def test_negative_n_steps_rejected(self):
        with pytest.raises(ValidationError):
            SimulationRunConfig(dt_seconds=1.0, n_steps=0, output_stride=1)

    def test_model_kind_discriminant(self):
        assert _hydro_params().model_kind == SimulationModelKind.HYDRO_DIFFUSION
        assert _line_traffic().model_kind == (
            SimulationModelKind.TRAFFIC_PROPAGATION
        )


# ── State Matrices ────────────────────────────────────────────────────────


class TestStateMatrices:
    def test_raster_total_and_copy_isolation(self):
        state = RasterStateMatrix(
            _grid(), {"water_depth_m": np.full((8, 8), 0.05)},
        )
        # 守恒总量 = 体积口径（Σ深度 × 像元面积），见 spec §2 量纲约定
        assert state.total("water_depth_m") == pytest.approx(
            8 * 8 * 0.05 * 100.0)
        clone = state.copy()
        clone.variables["water_depth_m"][0, 0] = 99.0
        assert state.variables["water_depth_m"][0, 0] == pytest.approx(0.05)

    def test_raster_validate_rejects_nonfinite(self):
        bad = np.full((4, 4), 0.01)
        bad[1, 1] = float("inf")
        with pytest.raises(SimulationStateError):
            RasterStateMatrix(_grid(4, 4), {"water_depth_m": bad}).validate()

    def test_negative_depth_rejected_by_validate(self):
        bad = np.zeros((4, 4))
        bad[2, 2] = -0.5
        with pytest.raises(SimulationStateError):
            RasterStateMatrix(_grid(4, 4), {"water_depth_m": bad}).validate()

    def test_graph_topology_adjacency_and_egress(self):
        params = _line_traffic(n_edges=3, bottleneck=False)
        topo = GraphTopology.from_edges(params.edges)
        assert topo.edge_ids == ["e1", "e2", "e3"]
        # n1 的出边只有 e2；n0 的入边为空
        assert [topo.edge_ids[i] for i in topo.out_edges_of_node(1)] == ["e2"]
        assert topo.in_edges_of_node(0).tolist() == []
        # 末端边（头节点无出边）= egress
        assert topo.is_egress(2)
        assert not topo.is_egress(0)


# ── 水文浸润模型：守恒 / 稳定 / 物理合理性 ────────────────────────────────


class TestHydroDiffusion:
    def test_flat_pool_is_stationary(self):
        """静水平坦DEM：无梯度 → 无通量，水面纹丝不动（无伪流动）。"""
        res = run_simulation(
            _hydro_params(h0=0.05), _hydro_cfg(n=20, stride=20))
        final = res.final_state.variables["water_depth_m"]
        assert np.allclose(final, 0.05, atol=1e-12)
        assert res.conservation_report.residual_relative <= 1e-12

    def test_mass_conservation_with_rain_and_drain(self):
        """状态守恒测试：降雨注入 + 排水汇全程记账，总量恒等式 ≤1e-9。"""
        res = run_simulation(
            _hydro_params(rain=5e-6, drain=2e-6), _hydro_cfg(n=30, stride=5))
        rep = res.conservation_report
        assert rep.residual_relative <= 1e-9
        assert rep.passed is True
        # 期望总量 = 初始 + 净源汇（runtime 独立累积，与模型内部无关）
        assert rep.expected_final == pytest.approx(rep.final_total, rel=1e-9)

    def test_rain_duration_window_accounting_is_exact(self):
        """雨强窗口：只下 5 步 → 净源量解析值 = rate·面积·dt·5。"""
        rate, dt = 5e-6, 5.0
        res = run_simulation(
            _hydro_params(rain=rate, rain_duration_s=5 * dt),
            _hydro_cfg(n=20, stride=20),
        )
        analytic = rate * (10.0 ** 2) * 8 * 8 * dt * 5
        assert res.conservation_report.net_sources == pytest.approx(
            analytic, rel=1e-12)
        assert res.conservation_report.residual_relative <= 1e-9

    def test_no_negative_depth_and_no_divergence(self):
        """暴雨 + 接近CFL的步长：水深非负、全程有限、总量有界（不发散）。"""
        res = run_simulation(
            _hydro_params(rain=2e-5, calib=5.0), _hydro_cfg(n=40, dt=20.0))
        depth = res.final_state.variables["water_depth_m"]
        assert np.all(np.isfinite(depth))
        assert depth.min() >= 0.0
        rep = res.conservation_report
        # 总水量不可能超过 初始 + 全部降雨（无生成项）
        rain_total = 2e-5 * 100.0 * 64 * 20.0 * 40
        assert rep.final_total <= rep.initial_total + rain_total + 1e-6
        assert res.conservation_report.residual_relative <= 1e-9

    def test_water_flows_downhill(self):
        """坡面 DEM：水体向低处净迁移。"""
        w = h = 8
        slope = np.tile(np.linspace(1.0, 0.0, w), (h, 1))
        res = run_simulation(
            _hydro_params(elevation_values=slope.tolist(), h0=0.05),
            _hydro_cfg(n=20, stride=20),
        )
        depth = res.final_state.variables["water_depth_m"]
        low_side = depth[:, -1].mean()
        high_side = depth[:, 0].mean()
        assert low_side > high_side
        assert high_side < 0.05  # 高处被抽走
        assert res.conservation_report.residual_relative <= 1e-9

    def test_depression_accumulates_water(self):
        """洼地聚水：碗状DEM + 均匀初始水体 → 中心水深远大于角部。"""
        w = h = 9
        yy, xx = np.mgrid[0:h, 0:w]
        bowl = 0.5 * ((xx - 4) ** 2 + (yy - 4) ** 2) / 16.0
        res = run_simulation(
            _hydro_params(grid=_grid(w, h), elevation_values=bowl.tolist(),
                          h0=0.05, calib=0.1),
            _hydro_cfg(n=60, dt=10.0, stride=60),
        )
        depth = res.final_state.variables["water_depth_m"]
        assert depth[4, 4] > 3.0 * depth[0, 0]
        assert res.conservation_report.residual_relative <= 1e-9

    def test_higher_roughness_slows_spread(self):
        """Manning 粗糙度越大，同脉冲同步数的润湿范围越小。"""

        def wet_count(n_val: float) -> int:
            params = _hydro_params(manning=n_val)
            grid = params.grid
            h0 = np.zeros((grid.height, grid.width))
            h0[4, 4] = 0.1
            p = params.model_copy(deep=True)
            p.initial_water_depth_m = _field(values=h0.tolist())
            res = run_simulation(p, _hydro_cfg(n=10, stride=10))
            return int((res.final_state.variables["water_depth_m"] > 1e-4).sum())

        assert wet_count(0.01) > wet_count(0.2)

    def test_drain_with_outflow_never_negative(self):
        """审查修复回归：排水与出流并发（退水期薄水膜）时 h≥0 硬保证不被击穿。"""
        w = h = 8
        slope = np.tile(np.linspace(1.0, 0.0, w), (h, 1))
        res = run_simulation(
            _hydro_params(elevation_values=slope.tolist(), h0=0.01,
                          drain=2e-4),
            _hydro_cfg(n=30, dt=30.0, stride=30),
        )
        depth = res.final_state.variables["water_depth_m"]
        assert depth.min() >= 0.0
        assert res.conservation_report.passed is True
        # 排水是纯汇：总水量单调不增（相对初始）
        assert res.conservation_report.final_total <= (
            res.conservation_report.initial_total + 1e-9)

    def test_cfl_guard_rejects_oversized_dt(self):
        rt = SimulationRuntime(_hydro_params(rain=1e-4), _hydro_cfg(n=5, dt=1e5))
        with pytest.raises(SimulationStabilityError) as ei:
            rt.run()
        # 证据必须是有限的正 dt_max（而非消息文本匹配）
        dt_max = ei.value.context.get("dt_max")
        assert dt_max is not None and 0 < float(dt_max) < 1e5
        assert rt.status.value == "failed"

    def test_initial_state_respects_grid_shape(self):
        with pytest.raises(ValidationError):
            # 标量场与网格解耦（constant），但数组场形状必须匹配 4x4 vs 8x8
            _hydro_params(
                grid=_grid(4, 4),
                elevation_values=np.zeros((8, 8)).tolist(),
            )


# ── 交通潮汐模型：瓶颈蔓延 / 车辆守恒 / 重力分配 ─────────────────────────


class TestTrafficPropagation:
    def test_bottleneck_congestion_spreads_upstream(self):
        """状态守恒语义下的蔓延测试：e4 注入瓶颈后，拥堵按步长逐边上溯。"""
        res = run_simulation(_line_traffic(), _traffic_cfg(n=60, stride=30))
        edge = res.final_state
        assert edge.variable_names == ["queue_veh"]
        topo = edge.topology
        q = edge.variables["queue_veh"]
        idx = {eid: i for i, eid in enumerate(topo.edge_ids)}
        c = q / np.array([
            topo.storage_veh[idx[e]] for e in topo.edge_ids
        ])
        # 末端瓶颈最堵；拥堵已蔓延到上游 e2/e1；次序单调
        assert c[idx["e4"]] >= 0.9
        assert c[idx["e3"]] >= 0.5
        assert c[idx["e2"]] > 0.1
        assert c[idx["e4"]] >= c[idx["e3"]] >= c[idx["e2"]] - 1e-9
        # 时间维度：早期 e3 尚未受染，后期显著拥堵（随步长蔓延）
        early = run_simulation(_line_traffic(), _traffic_cfg(n=10, stride=10))
        q_early = early.final_state.variables["queue_veh"]
        assert q_early[idx["e3"]] / topo.storage_veh[idx["e3"]] < 0.5

    def test_vehicle_mass_conservation(self):
        """车辆质量守恒：注入 = 存量 + 驶出，残差 ≤1e-9。"""
        res = run_simulation(_line_traffic(), _traffic_cfg(n=60, stride=60))
        rep = res.conservation_report
        assert rep.variable == "queue_veh"
        assert rep.residual_relative <= 1e-9
        assert rep.passed is True

    def test_queue_respects_storage_bound(self):
        res = run_simulation(_line_traffic(), _traffic_cfg(n=60, stride=60))
        topo = res.final_state.topology
        q = res.final_state.variables["queue_veh"]
        assert np.all(q <= topo.storage_veh + 1e-9)
        for step in res.steps:
            assert step.diagnostics.max_value <= float(
                topo.storage_veh.max()) + 1e-9

    def test_free_flow_no_congestion_growth(self):
        """无瓶颈 + 欠饱和需求：拥堵指数近零，绝大多数车辆驶出。"""
        res = run_simulation(
            _line_traffic(bottleneck=False), _traffic_cfg(n=60, stride=60))
        q = res.final_state.variables["queue_veh"]
        topo = res.final_state.topology
        assert np.all(q / topo.storage_veh < 0.1)
        rep = res.conservation_report
        assert rep.net_sinks / rep.net_sources > 0.9

    def test_gravity_turn_weights_renormalized(self):
        from app.services.simulation.models.traffic_propagation import (
            TrafficPropagationModel,
        )

        weights = TrafficPropagationModel.gravity_turn_weights(
            capacities=[1800.0, 900.0], travel_times=[60.0, 60.0], gamma=1.0,
        )
        assert weights[0] == pytest.approx(2 / 3)
        assert weights[1] == pytest.approx(1 / 3)
        assert sum(weights.values()) == pytest.approx(1.0)

    def test_branched_network_no_negative_and_conserved(self):
        """审查修复回归：分岔路网（节点多出边）下浮点归一化残差不产生负队列。

        欠饱和需求使服务每步清空队列（served == q）——这正是权重 ulp 残差
        透支发送方的必现路径；修复后必须保持非负且严格守恒。
        """
        edges = [
            NetworkEdgeSpec(
                edge_id="a", tail="n0", head="n1", length_m=300.0,
                capacity_veh_h=1800.0, free_flow_speed_kmh=40.0,
            ),
            NetworkEdgeSpec(
                edge_id="b1", tail="n1", head="n2", length_m=300.0,
                capacity_veh_h=1800.0, free_flow_speed_kmh=40.0,
            ),
            NetworkEdgeSpec(
                edge_id="b2", tail="n1", head="n3", length_m=300.0,
                capacity_veh_h=900.0, free_flow_speed_kmh=40.0,
            ),
        ]
        params = TrafficSimulationParams(
            edges=edges,
            boundary=TrafficBoundaryCondition(demand_veh_h={"a": 600.0}),
        )
        res = run_simulation(params, _traffic_cfg(n=50, dt=5.0, stride=50))
        q = res.final_state.variables["queue_veh"]
        assert q.min() >= 0.0
        assert res.conservation_report.passed is True
        assert res.conservation_report.residual_relative <= 1e-9

    def test_stability_guard_rejects_unresolvable_dt(self):
        # L=500m @40km/h → 行程时间 45s；dt=60s 超出模型有效性包络
        rt = SimulationRuntime(_line_traffic(), _traffic_cfg(n=5, dt=60.0))
        with pytest.raises(SimulationStabilityError):
            rt.run()

    def test_bottleneck_window_can_expire(self):
        """瓶颈有时限：窗口关闭后容量恢复，排队开始消散（对照永久瓶颈）。"""
        expiring = _line_traffic()
        expiring.boundary.bottleneck.duration_ticks = 10
        res_expire = run_simulation(expiring, _traffic_cfg(n=80, stride=80))
        res_perm = run_simulation(
            _line_traffic(), _traffic_cfg(n=80, stride=80))
        topo = res_expire.final_state.topology
        idx = topo.edge_ids.index("e4")
        q_expire = res_expire.final_state.variables["queue_veh"]
        q_perm = res_perm.final_state.variables["queue_veh"]
        # 窗口关闭后 e4 在消化：队列显著低于永久瓶颈，也低于存容
        assert q_expire[idx] < q_perm[idx]
        assert q_expire[idx] < 0.5 * topo.storage_veh[idx]
        assert q_perm[idx] >= 0.9 * topo.storage_veh[idx]


# ── Runtime：生命周期 / 调度 / 检查点 / 取消 ───────────────────────────────


class TestRuntime:
    def test_lifecycle_and_step_diagnostics(self):
        rt = SimulationRuntime(
            _hydro_params(rain=1e-5), _hydro_cfg(n=12, stride=6))
        res = rt.run()
        assert rt.status.value == "completed"
        assert len(res.steps) == 12
        t0 = res.steps[0].diagnostics
        assert t0.tick == 1 and t0.sim_seconds == pytest.approx(5.0)
        assert t0.mass_error_relative <= 1e-9
        assert t0.finite is True
        assert res.steps[-1].diagnostics.sim_seconds == pytest.approx(60.0)
        # 诊断单调时间轴
        secs = [s.diagnostics.sim_seconds for s in res.steps]
        assert secs == sorted(secs)

    def test_temporal_products_t0_to_tn(self):
        """多时相产物：T0 与每个 stride（含末步）各一片。"""
        res = run_simulation(
            _hydro_params(rain=1e-5), _hydro_cfg(n=12, stride=4))
        ticks = [p.tick for p in res.products]
        assert ticks == [0, 4, 8, 12]
        assert [p.product_id for p in res.products] == [
            f"sim-{res.run_id}-t{t}" for t in ticks
        ]
        assert res.products[0].sim_seconds == 0.0
        assert res.products[-1].sim_seconds == pytest.approx(60.0)

    def test_product_stride_one_includes_every_tick(self):
        res = run_simulation(_hydro_params(), _hydro_cfg(n=3, stride=1))
        assert [p.tick for p in res.products] == [0, 1, 2, 3]

    def test_law_registry_dispatch(self):
        law = build_law(_hydro_params())
        assert isinstance(law, DynamicPropagationLaw)
        assert law.conserved_variable == "water_depth_m"
        law_t = build_law(_line_traffic())
        assert law_t.conserved_variable == "queue_veh"

    def test_checkpoint_resume_equivalence(self):
        full = run_simulation(_hydro_params(rain=1e-5), _hydro_cfg(n=10))
        part = SimulationRuntime(_hydro_params(rain=1e-5), _hydro_cfg(n=4))
        part.run()
        ck = part.to_checkpoint()
        resumed = SimulationRuntime.from_checkpoint(ck, _hydro_cfg(n=10))
        res = resumed.run()
        assert len(res.steps) == 10
        assert res.conservation_report.final_total == pytest.approx(
            full.conservation_report.final_total, rel=1e-12)
        assert np.allclose(
            res.final_state.variables["water_depth_m"],
            full.final_state.variables["water_depth_m"], rtol=1e-12,
        )

    def test_conservation_violation_fails_loud(self):
        class _LeakyLaw(DynamicPropagationLaw):
            conserved_variable = "water_depth_m"
            product_variable = "water_depth_m"
            product_threshold = 1e-4
            layer_kind = "raster_grid"

            def __init__(self, params):
                self.params = params

            def build_initial_state(self):
                g = self.params.grid
                return RasterStateMatrix(
                    g, {"water_depth_m": np.full((g.height, g.width), 0.01)})

            def validate_stability(self, state, dt_seconds):
                return dt_seconds * 1e6

            def advance(self, state, dt_seconds, tick):
                nxt = state.copy()
                nxt.variables["water_depth_m"] *= 0.9  # 凭空消灭水量
                return nxt, StepAccounting(sources={}, sinks={})

        rt = SimulationRuntime(
            _hydro_params(h0=0.01), _hydro_cfg(n=5), law=_LeakyLaw(
                _hydro_params(h0=0.01)))
        with pytest.raises(SimulationStateError):
            rt.run()
        assert rt.status.value == "failed"

    def test_cancel_before_run(self):
        rt = SimulationRuntime(_hydro_params(), _hydro_cfg(n=5))
        rt.cancel()
        with pytest.raises(SimulationCancelledError):
            rt.run()
        assert rt.status.value == "cancelled"

    def test_cancel_mid_run_via_progress_callback(self):
        rt = SimulationRuntime(
            _hydro_params(rain=1e-5), _hydro_cfg(n=50, stride=50))

        def bail(tick, total, phase):
            if tick >= 3:
                rt.cancel()

        with pytest.raises(SimulationCancelledError):
            rt.run(progress_callback=bail)
        assert rt.status.value == "cancelled"
        assert len(rt.events) >= 1

    def test_progress_callback_invoked_per_tick(self):
        seen = []
        rt = SimulationRuntime(
            _hydro_params(rain=1e-5), _hydro_cfg(n=6, stride=6))
        rt.run(progress_callback=lambda t, n, p: seen.append((t, n)))
        assert seen[-1] == (6, 6)
        assert len(seen) == 6

    def test_invalid_lifecycle_transition_rejected(self):
        rt = SimulationRuntime(_hydro_params(), _hydro_cfg(n=2))
        rt.run()
        from app.services.simulation.errors import InvalidSimulationTransition
        with pytest.raises(InvalidSimulationTransition):
            rt.run()  # completed → running 非法

    def test_step_outside_running_state_rejected(self):
        """审查修复回归：step() 不得绕过生命周期状态机。"""
        rt = SimulationRuntime(_hydro_params(), _hydro_cfg(n=3))
        with pytest.raises(InvalidSimulationTransition):
            rt.step()  # PENDING 直调
        rt.run()
        with pytest.raises(InvalidSimulationTransition):
            rt.step()  # completed 后加跑

    def test_terminal_checkpoint_resume_rejected(self):
        """审查修复回归：failed/cancelled 检查点不可续跑（completed 可续）。"""
        # cancelled 检查点：中断语义，状态完整性无保证
        rt = SimulationRuntime(
            _hydro_params(rain=1e-5), _hydro_cfg(n=50, stride=50))

        def bail(tick, total, phase):
            if tick >= 3:
                rt.cancel()

        with pytest.raises(SimulationCancelledError):
            rt.run(progress_callback=bail)
        ck = rt.to_checkpoint()
        assert ck["status"] == "cancelled"
        with pytest.raises(SimulationStateError):
            SimulationRuntime.from_checkpoint(ck, _hydro_cfg(n=6))
        # completed 检查点 = 到达配置视界的合法续跑点（既有等价性测试覆盖）


# ── 时态产物与 MapSpec 兼容 ───────────────────────────────────────────────


class TestTemporalProducts:
    def test_memory_sink_payload_network(self):
        sink = InMemoryTemporalSink()
        # 四条边都有入口需求（欠容）→ 快照时刻每条边都持有排队车辆
        edges = [
            NetworkEdgeSpec(
                edge_id=f"e{i + 1}", tail=f"n{i}", head=f"n{i + 1}",
                length_m=500.0, capacity_veh_h=1800.0,
                free_flow_speed_kmh=40.0,
            )
            for i in range(4)
        ]
        params = TrafficSimulationParams(
            edges=edges,
            boundary=TrafficBoundaryCondition(demand_veh_h={
                "e1": 7200.0, "e2": 7200.0, "e3": 7200.0, "e4": 7200.0,
            }),
        )
        res = run_simulation(params, _traffic_cfg(n=5, stride=5), sink=sink)
        last = res.products[-1]
        feats = sink.payloads[last.product_id]
        assert len(feats) == 4
        assert all(f["properties"]["congestion_index"] <= 1.0 for f in feats)
        assert feats[0]["geometry"]["type"] == "LineString"
        assert last.layer_kind == "network_edges"

    def test_memory_sink_payload_raster(self):
        sink = InMemoryTemporalSink()
        res = run_simulation(
            _hydro_params(rain=1e-5), _hydro_cfg(n=10, stride=10), sink=sink)
        feats = sink.payloads[res.products[-1].product_id]
        assert all(f["geometry"]["type"] == "Polygon" for f in feats)
        assert res.products[-1].layer_kind == "raster_grid"

    def test_mapspec_bundle_validates_against_mapspec_document(self):
        """兼容性以真实 MapSpec v1.2 schema 校验为准（frames ≤50）。"""
        from app.lib.cartography.mapspec_schema import MapSpecDocument

        res = run_simulation(
            _line_traffic(), _traffic_cfg(n=20, stride=10))
        bundle = build_mapspec_bundle(res)
        doc = MapSpecDocument.model_validate(bundle)
        assert doc.version == "1.2"
        assert doc.layout is not None and doc.layout.frames is not None
        assert len(doc.layout.frames) == len(res.products) == 3
        # 逐帧只点亮当期图层，其余图层隐去
        visible = [
            [lid for lid, ov in fr.layerOverrides.items() if ov.visible]
            for fr in doc.layout.frames
        ]
        assert all(len(v) == 1 for v in visible)
        assert visible[0][0] != visible[1][0]
        # source 数 = 产物数
        assert len(doc.sources) == len(res.products)

    def test_mapspec_bundle_raster_variant(self):
        from app.lib.cartography.mapspec_schema import MapSpecDocument

        res = run_simulation(_hydro_params(rain=1e-5), _hydro_cfg(n=5, stride=5))
        doc = MapSpecDocument.model_validate(build_mapspec_bundle(res))
        layer = doc.layers[0]
        assert layer.type == "fill"

    def test_geoparquet_sink_roundtrip(self, tmp_path):
        from app.services.data_fabric.vector_carrier import arrow_available

        if not arrow_available():
            pytest.skip("optional pyarrow dependency not installed")
        from app.services.simulation import GeoParquetTemporalSink

        sink = GeoParquetTemporalSink(output_dir=tmp_path)
        res = run_simulation(_line_traffic(), _traffic_cfg(n=10, stride=10),
                             sink=sink)
        prod = res.products[-1]
        assert prod.format == "geoparquet" and prod.uri is not None
        from app.services.data_fabric.vector_carrier import (
            geoparquet_to_features,
        )

        assert len(geoparquet_to_features(prod.uri)) == 4

    def test_geoparquet_sink_honest_degradation_without_pyarrow(
        self, tmp_path,
    ):
        from app.services.data_fabric.vector_carrier import arrow_available

        if arrow_available():
            pytest.skip("pyarrow present; degradation path not exercisable")
        from app.services.simulation import GeoParquetTemporalSink

        sink = GeoParquetTemporalSink(output_dir=tmp_path)
        res = run_simulation(_line_traffic(), _traffic_cfg(n=5, stride=5),
                             sink=sink)
        prod = res.products[-1]
        assert prod.format == "memory"
        assert prod.uri is None
        assert prod.degraded_note  # 诚实降级必须留痕


# ── Celery 异步流转 ───────────────────────────────────────────────────────


class TestCeleryTask:
    def test_task_eager_direct_call(self):
        """eager/直调路径（job_id=None）：任务体同步产出有界摘要。"""
        from app.services.simulation.tasks import run_simulation_forecast

        out = run_simulation_forecast(
            _hydro_params(rain=1e-5).model_dump(mode="json"),
            _hydro_cfg(n=8, stride=4).model_dump(mode="json"),
        )
        assert out["status"] == "completed"
        assert out["n_steps"] == 8
        assert len(out["products"]) == 3
        assert out["conservation"]["residual_relative"] <= 1e-9

    def test_task_module_registered_in_celery_include(self):
        import app.services.simulation.tasks as sim_tasks
        from app.services.task_queue import celery_app

        assert "app.services.simulation.tasks.run_simulation_forecast" in (
            celery_app.tasks
        )
        declared = list(celery_app.conf.include or [])
        assert "app.services.simulation.tasks" in declared
        assert hasattr(sim_tasks, "run_simulation_forecast")

    def test_task_rejects_invalid_params_with_typed_error(self):
        from app.services.simulation.tasks import run_simulation_forecast

        bad = _hydro_params(rain=1e-5).model_dump(mode="json")
        bad["grid"]["cell_size_m"] = -1.0
        with pytest.raises(SimulationConfigError):
            run_simulation_forecast(bad, _hydro_cfg(n=2).model_dump(mode="json"))


# ── 工具能力面 ────────────────────────────────────────────────────────────


class TestSimulationTool:
    def test_tool_registered_in_registry(self):
        from app.tools.registry import ToolRegistry
        from app.tools.simulation_tools import register_simulation_tools

        reg = ToolRegistry()
        register_simulation_tools(reg)
        assert "run_spatial_simulation" in reg.list_tools()

    def test_tool_impl_invalid_params_returns_std_error(self):
        from app.tools.simulation_tools import run_spatial_simulation_impl

        out = run_spatial_simulation_impl(
            model_kind="hydro_diffusion",
            params={"grid": {"width": -1}},
            dt_seconds=5.0, n_steps=2, output_stride=1, run_async=False,
        )
        assert out["success"] is False
        assert out["code"] == "VALIDATION_ERROR"

    def test_tool_impl_inline_run(self):
        from app.tools.simulation_tools import run_spatial_simulation_impl

        params = _hydro_params(rain=1e-5).model_dump(mode="json")
        out = run_spatial_simulation_impl(
            model_kind="hydro_diffusion", params=params,
            dt_seconds=5.0, n_steps=4, output_stride=2, run_async=False,
        )
        assert out["success"] is True
        data = out["data"]
        assert data["status"] == "completed"
        assert data["n_steps"] == 4
        assert data["conservation"]["passed"] is True
        assert data["product_count"] == 3
        assert data["mapspec_frames"] == 3

    def test_tool_impl_async_path_returns_job_envelope(self, monkeypatch):
        import app.tools.simulation_tools as mod

        captured = {}

        def fake_enqueue(params, config, **kw):
            captured["params"] = params
            return {"status": "analysis_task_started", "job_id": "1",
                    "task_id": "t", "idempotent_reuse": False,
                    "message": "ok"}

        monkeypatch.setattr(mod, "enqueue_simulation", fake_enqueue)
        out = mod.run_spatial_simulation_impl(
            model_kind="traffic_propagation",
            params=_line_traffic().model_dump(mode="json"),
            dt_seconds=10.0, n_steps=5, output_stride=5, run_async=True,
        )
        assert out["success"] is True
        assert out["data"]["status"] == "analysis_task_started"
        assert captured["params"].model_kind == (
            SimulationModelKind.TRAFFIC_PROPAGATION
        )

    def test_registry_dispatch_tier3_gate_and_inline_run(self):
        """registry 真实分发：tier-3 确认门 + inline 推演 + 守恒通过。"""
        import asyncio

        from app.tools.registry import ToolRegistry, confirm_tier3
        from app.tools.simulation_tools import register_simulation_tools

        reg = ToolRegistry()
        register_simulation_tools(reg)
        params = _line_traffic(n_edges=2).model_dump(mode="json")
        args = {"model_kind": "traffic_propagation", "params": params,
                "dt_seconds": 10.0, "n_steps": 2, "output_stride": 1,
                "run_async": False}
        # 未经 tier-3 确认 → 平台策略拦截（typed 响应，不抛栈）
        blocked = asyncio.run(reg.dispatch(
            "run_spatial_simulation", dict(args)))
        assert blocked["success"] is False
        assert blocked["code"] == "TIER3_CONFIRMATION_REQUIRED"
        # 确认后 inline 跑通，守恒通过
        with confirm_tier3():
            out = asyncio.run(reg.dispatch(
                "run_spatial_simulation", dict(args)))
        assert out["success"] is True
        assert out["data"]["status"] == "completed"
        assert out["data"]["conservation"]["passed"] is True
        assert out["data"]["product_count"] == 3


# ── 公共入口与确定性 ──────────────────────────────────────────────────────


class TestApiEntrypoint:
    def test_run_simulation_is_deterministic(self):
        a = run_simulation(_line_traffic(), _traffic_cfg(n=15, stride=15))
        b = run_simulation(_line_traffic(), _traffic_cfg(n=15, stride=15))
        assert np.allclose(
            a.final_state.variables["queue_veh"],
            b.final_state.variables["queue_veh"], rtol=0, atol=0,
        )
        assert a.run_id == b.run_id  # 内容寻址（同参数同 run_id）

    def test_run_id_changes_with_params(self):
        a = run_simulation(_line_traffic(), _traffic_cfg(n=5))
        b = run_simulation(_line_traffic(demand=2400.0), _traffic_cfg(n=5))
        assert a.run_id != b.run_id

    def test_wall_clock_budget_guard(self):
        cfg = SimulationRunConfig(
            dt_seconds=5.0, n_steps=50, output_stride=50, max_wall_seconds=1e-9,
        )
        with pytest.raises(SimulationError):
            run_simulation(_hydro_params(rain=1e-5), cfg)
