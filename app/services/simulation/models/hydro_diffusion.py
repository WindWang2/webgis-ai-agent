"""暴雨内涝淹没扩散模型（ADR-0192 D4 / spec §4.1）。

基于 DEM 高程梯度与地表粗糙度（Manning 系数）的线性化扩散波：

    w_i    = Z_i + h_i                        # 水面高程
    k_ij   = calib · h_act^(5/3) / n̄ / dx²    # 成对传导率 [1/s]
    Δ_ij   = dt · k_ij · (w_i − w_j)          # 成对通量（i→j 为正）
    s_i    = min(1, 0.45·h_i / Σout_i)        # 发送方限制器
    h_i   ←= h_i + Σin Δ − Σout Δ·s_i + dt·rain_i − drained_i

数值完整性（本模型的验收契约）：
- **成对通量**：一个格元失去的量 = 邻元获得的量 → 内部交换严格守恒；
- **发送方限制器**只缩放"发出多少"，不破坏成对守恒，且保证
  ``h_new ≥ 0.55·h_old ≥ 0``（负水深硬保证；限制器是保险丝不是常态路径）；
- **稳定域**：``dt·max_i Σ_j k_ij ≤ 0.9``（图拉普拉斯显式格式的
  最大值原理条件），违例在 advance 前被 :meth:`validate_stability` 拒绝；
- 源（降雨）/汇（排水）显式记账，排水仅作用于湿格元（对干格抽水会
  产生伪负深）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from app.services.simulation.errors import SimulationStabilityError
from app.services.simulation.laws import DynamicPropagationLaw, StepAccounting
from app.services.simulation.state import RasterStateMatrix

if TYPE_CHECKING:
    from app.services.simulation.contracts import HydroSimulationParams
    from app.services.simulation.state import SpatialStateMatrix

#: 湿润界面最小水深 [m]（允许湿地向干格扩散的数值下限）。
_H_MIN = 1e-3
#: 发送方流出上限占可用水深比例（0.45 → 单步至少保留 55% 水深）。
_OUT_CAP = 0.45
#: 显式格式的稳定域安全系数（dt_max = _CFL_SAFETY / max_i Σ_j k_ij）。
_CFL_SAFETY = 0.9


class HydroDiffusionModel(DynamicPropagationLaw):
    """DEM + Manning 线性化扩散波（守恒变量：水深体积，m³）。"""

    conserved_variable = "water_depth_m"
    product_variable = "water_depth_m"
    product_threshold = 1e-4
    layer_kind = "raster_grid"

    def __init__(self, params: "HydroSimulationParams"):
        super().__init__(params)
        shape = params.grid.shape
        self._z = params.elevation_m.materialize(shape)
        self._n = params.manning_n.materialize(shape)
        self._rain = params.boundary.rainfall_rate_m_per_s.materialize(shape)
        self._drain = float(params.boundary.drainage_rate_m_per_s)
        self._rain_duration = params.boundary.rainfall_duration_s
        self._calib = float(params.diffusivity_calibration)
        self._dx2 = params.grid.cell_size_m**2
        self._cell_area = params.grid.cell_area_m2

    # ── law 接口 ──────────────────────────────────────────────────────────

    def build_initial_state(self) -> RasterStateMatrix:
        h0 = self.params.initial_water_depth_m.materialize(
            self.params.grid.shape)
        state = RasterStateMatrix(self.params.grid, {"water_depth_m": h0})
        state.validate()
        return state

    def validate_stability(self, state, dt_seconds: float) -> float:
        """dt_max = 0.9 / max_i Σ_j k_ij（k 随水深变化，每步重算）。"""
        ksum = self._neighbor_conductance_sum(
            state.variables[self.conserved_variable])
        if ksum.max() <= 0:
            return float("inf")
        dt_max = _CFL_SAFETY / float(ksum.max())
        if dt_seconds > dt_max:
            raise SimulationStabilityError(
                f"dt={dt_seconds:.6g}s exceeds stability envelope "
                f"dt_max={dt_max:.6g}s (explicit diffusive-wave CFL guard)",
                context={"dt": float(dt_seconds), "dt_max": dt_max},
            )
        return dt_max

    def advance(
        self, state: SpatialStateMatrix, dt_seconds: float, tick: int,
    ) -> tuple[RasterStateMatrix, StepAccounting]:
        h = state.variables[self.conserved_variable]
        rain_gain = self._rain_gain(dt_seconds, tick)

        out_r, out_l, out_d, out_u = self._pairwise_outflows(h, dt_seconds)
        # 发送方限制器：按可用水深等比缩放各向流出（成对守恒不被破坏）
        out_total = np.zeros_like(h)
        out_total[:, :-1] += out_r
        out_total[:, 1:] += out_l
        out_total[:-1, :] += out_d
        out_total[1:, :] += out_u
        scale = np.ones_like(h)
        hot = out_total > 0
        scale[hot] = np.minimum(1.0, _OUT_CAP * h[hot] / out_total[hot])

        sr = out_r * scale[:, :-1]
        sl = out_l * scale[:, 1:]
        sd = out_d * scale[:-1, :]
        su = out_u * scale[1:, :]
        out_scaled_total = np.zeros_like(h)
        out_scaled_total[:, :-1] += sr
        out_scaled_total[:, 1:] += sl
        out_scaled_total[:-1, :] += sd
        out_scaled_total[1:, :] += su

        dh = np.zeros_like(h)
        dh[:, :-1] -= sr
        dh[:, 1:] += sr
        dh[:, 1:] -= sl
        dh[:, :-1] += sl
        dh[:-1, :] -= sd
        dh[1:, :] += sd
        dh[1:, :] -= su
        dh[:-1, :] += su

        # 排水受"流出后余量"封顶：drained ≤ h − Σout·s →
        # h_new = h − Σout·s − drained + rain ≥ rain ≥ 0（负水深硬保证在
        # 排水与出流并发时依然成立；记账与扣减用同一 drained，守恒不变）。
        drained = np.minimum(
            np.maximum(h - out_scaled_total, 0.0),
            dt_seconds * self._drain,
        )

        new_h = h + dh + rain_gain - drained
        new_state = RasterStateMatrix(
            self.params.grid, {self.conserved_variable: new_h})
        accounting = StepAccounting(
            sources={"rainfall": float(rain_gain.sum()) * self._cell_area},
            sinks={"drainage": float(drained.sum()) * self._cell_area},
        )
        return new_state, accounting

    def derived_metrics(self, state: SpatialStateMatrix) -> dict[str, float]:
        depth = state.variables[self.conserved_variable]
        wet = depth > self.product_threshold
        return {
            "max_depth_m": float(depth.max()),
            "wet_cell_count": float(wet.sum()),
            "wet_area_km2": float(wet.sum()) * self._cell_area / 1e6,
        }

    # ── 内部数值 ──────────────────────────────────────────────────────────

    def _rain_gain(self, dt_seconds: float, tick: int) -> np.ndarray:
        """本步降雨增量 [m]；雨强窗口关闭后为零（与记账口径一致）。"""
        if self._rain_duration is not None:
            if (tick - 1) * dt_seconds >= self._rain_duration:
                return np.zeros_like(self._rain)
        return self._rain * dt_seconds

    def _pair_conductance(
        self,
        h_a: np.ndarray,
        h_b: np.ndarray,
        n_a: np.ndarray,
        n_b: np.ndarray,
    ) -> np.ndarray:
        """相邻格元对的传导率 k = calib·h_act^(5/3)/n̄/dx² [1/s]。"""
        h_act = np.maximum(np.maximum(h_a, h_b), _H_MIN)
        n_bar = 0.5 * (n_a + n_b)
        return self._calib * h_act ** (5.0 / 3.0) / n_bar / self._dx2

    def _pairwise_outflows(self, h: np.ndarray, dt: float):
        """水平/垂直邻接对的有向流出量（>0 = 注释方向的可流出量）。

        返回 (out_r, out_l, out_d, out_u)：
        out_r[i,j] = (i,j)→(i,j+1) 的流出（发送方 = 左格），其余同理。
        """
        w = self._z + h
        flux_x = dt * self._pair_conductance(
            h[:, :-1], h[:, 1:], self._n[:, :-1], self._n[:, 1:]
        ) * (w[:, :-1] - w[:, 1:])
        flux_y = dt * self._pair_conductance(
            h[:-1, :], h[1:, :], self._n[:-1, :], self._n[1:, :]
        ) * (w[:-1, :] - w[1:, :])
        return (
            np.maximum(flux_x, 0.0),   # 发送方 = 左格
            np.maximum(-flux_x, 0.0),  # 发送方 = 右格
            np.maximum(flux_y, 0.0),   # 发送方 = 上格
            np.maximum(-flux_y, 0.0),  # 发送方 = 下格
        )

    def _neighbor_conductance_sum(self, h: np.ndarray) -> np.ndarray:
        """每格元与 4 邻的传导率之和（CFL 守卫的分母）。"""
        ksum = np.zeros_like(h)
        k_x = self._pair_conductance(
            h[:, :-1], h[:, 1:], self._n[:, :-1], self._n[:, 1:])
        ksum[:, :-1] += k_x
        ksum[:, 1:] += k_x
        k_y = self._pair_conductance(
            h[:-1, :], h[1:, :], self._n[:-1, :], self._n[1:, :])
        ksum[:-1, :] += k_y
        ksum[1:, :] += k_y
        return ksum


__all__ = ["HydroDiffusionModel"]
