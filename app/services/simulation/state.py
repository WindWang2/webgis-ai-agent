"""时空状态矩阵（ADR-0192 D2 三抽象之二）。

仿真状态的内存载体：规则网格（RasterStateMatrix）或有向路网
（GraphStateMatrix），多命名变量 numpy 数组 + 几何规格。统一职责：

- ``total(var)``：守恒总量（水文 = 体积 m³；交通 = 车辆数 veh）；
- ``validate()``：有限性 + 非负域校验（破坏即 :class:`SimulationStateError`）；
- ``copy()``：深拷贝隔离（law.advance 必须返回新状态，不原地改写）；
- ``to_features()``：载荷展平，供时态产物层消费。

本模块是纯数值层：不 import DB / Redis / Celery / app.tools。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

import numpy as np

from app.services.simulation.errors import SimulationStateError

if TYPE_CHECKING:  # 循环规避：仅类型标注用
    from app.services.simulation.contracts import GridSpec, NetworkEdgeSpec


class SpatialStateMatrix(ABC):
    """状态矩阵抽象：命名变量 + 几何绑定 + 守恒总量口径。"""

    #: 变量名 → 数组（栅格 (H,W) / 图 (E,)），子类保持插入序稳定。
    variables: dict[str, np.ndarray]

    @property
    def variable_names(self) -> list[str]:
        return list(self.variables.keys())

    @abstractmethod
    def total(self, variable: str) -> float:
        """守恒总量（自然量纲：水文体积 m³ / 交通车辆数 veh）。"""

    @abstractmethod
    def minmax(self, variable: str) -> tuple[float, float]:
        """变量极值（原始量纲）。"""

    @abstractmethod
    def validate(self) -> None:
        """有限性 + 非负域校验；破坏抛 :class:`SimulationStateError`。"""

    @abstractmethod
    def copy(self) -> "SpatialStateMatrix":
        """深拷贝（数组独立，几何规格共享只读）。"""

    @abstractmethod
    def to_features(
        self,
        variable: str,
        *,
        threshold: float = 0.0,
        max_features: int = 20_000,
        extras: Optional[dict[str, np.ndarray]] = None,
    ) -> tuple[list[dict[str, Any]], Optional[list[float]], bool]:
        """展平为 GeoJSON features。

        Returns:
            (features, bbox, truncated)：bbox = [w, s, e, n]；
            truncated=True 表示超过 max_features 已截断（按值降序取前 N）。
        """


def _raise_not_finite(variable: str) -> None:
    raise SimulationStateError(
        f"state variable '{variable}' contains non-finite values",
        context={"variable": variable},
    )


def _raise_negative(variable: str) -> None:
    raise SimulationStateError(
        f"state variable '{variable}' has negative values "
        "(non-negative domain violated)",
        context={"variable": variable},
    )


# ── 栅格实现 ──────────────────────────────────────────────────────────────


class RasterStateMatrix(SpatialStateMatrix):
    """规则网格状态（如 DEM 水深场）。"""

    def __init__(
        self,
        grid: "GridSpec",
        variables: dict[str, np.ndarray],
    ):
        expected = grid.shape
        for name, arr in variables.items():
            if arr.shape != expected:
                raise SimulationStateError(
                    f"variable '{name}' shape {arr.shape} != grid {expected}",
                    context={"variable": name},
                )
        self.grid = grid
        self.variables = dict(variables)

    def total(self, variable: str) -> float:
        return float(self.variables[variable].sum()) * self.grid.cell_area_m2

    def minmax(self, variable: str) -> tuple[float, float]:
        arr = self.variables[variable]
        return float(arr.min()), float(arr.max())

    def validate(self) -> None:
        for name, arr in self.variables.items():
            if not np.all(np.isfinite(arr)):
                _raise_not_finite(name)
            if arr.min() < 0:
                _raise_negative(name)

    def copy(self) -> "RasterStateMatrix":
        return RasterStateMatrix(
            self.grid,
            {k: v.copy() for k, v in self.variables.items()},
        )

    def to_features(self, variable, *, threshold=0.0, max_features=20_000,
                    extras=None):
        arr = self.variables[variable]
        rows, cols = np.nonzero(arr > threshold)
        if rows.size == 0:
            return [], None, False
        values = arr[rows, cols]
        if rows.size > max_features:
            order = np.argsort(values)[::-1][:max_features]
            rows, cols, values = rows[order], cols[order], values[order]
            truncated = True
        else:
            truncated = False
        dx = self.grid.cell_size_m
        ox, oy = self.grid.origin_x, self.grid.origin_y
        features: list[dict[str, Any]] = []
        for i, j, v in zip(rows.tolist(), cols.tolist(), values.tolist()):
            x0, y0 = ox + j * dx, oy + i * dx
            x1, y1 = x0 + dx, y0 + dx
            props: dict[str, Any] = {
                "cell_value": v,
                "row": i,
                "col": j,
            }
            for extra_name, extra_arr in (extras or {}).items():
                props[extra_name] = float(extra_arr[i, j])
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[x0, y0], [x1, y0], [x1, y1], [x0, y1],
                                    [x0, y0]],
                },
                "properties": props,
            })
        bbox = [
            ox + cols.min() * dx,
            oy + rows.min() * dx,
            ox + (cols.max() + 1) * dx,
            oy + (rows.max() + 1) * dx,
        ]
        return features, bbox, truncated


# ── 路网拓扑 ──────────────────────────────────────────────────────────────


@dataclass
class GraphTopology:
    """有向路网拓扑（预计算邻接索引；纯拓扑，不含状态）。"""

    edge_ids: list[str]
    tail: np.ndarray  # (E,) 节点索引
    head: np.ndarray  # (E,)
    lengths_m: np.ndarray  # (E,)
    lanes: np.ndarray  # (E,)
    free_flow_speed_kmh: np.ndarray  # (E,)
    capacity_veh_h: np.ndarray  # (E,)
    storage_veh: np.ndarray  # (E,) 存容 = length·lanes·jam_density
    edge_geometries: list[Optional[list[list[float]]]]
    node_count: int
    node_ids: list[str]
    first_in_edge: np.ndarray  # (N,) 每节点首条入边索引（-1 = 无入边）
    _out_edges: list[np.ndarray] = field(repr=False, default_factory=list)
    _in_edges: list[np.ndarray] = field(repr=False, default_factory=list)
    edge_index: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_edges(
        cls,
        edges: list["NetworkEdgeSpec"],
        jam_density_veh_per_km: float = 150.0,
    ) -> "GraphTopology":
        """铸拓扑：节点按首次出现顺序编号；邻接/egress/存容一次算好。"""
        node_ids: list[str] = []
        node_pos: dict[str, int] = {}

        def pos(node: str) -> int:
            if node not in node_pos:
                node_pos[node] = len(node_ids)
                node_ids.append(node)
            return node_pos[node]

        tail = np.array([pos(e.tail) for e in edges], dtype=np.int64)
        head = np.array([pos(e.head) for e in edges], dtype=np.int64)
        lengths = np.array([e.length_m for e in edges], dtype=float)
        lanes = np.array([e.lanes for e in edges], dtype=float)
        speeds = np.array([e.free_flow_speed_kmh for e in edges], dtype=float)
        caps = np.array([e.capacity_veh_h for e in edges], dtype=float)
        storage = (lengths / 1000.0) * lanes * jam_density_veh_per_km
        n = len(edges)
        out_edges: list[list[int]] = [[] for _ in range(len(node_ids))]
        in_edges: list[list[int]] = [[] for _ in range(len(node_ids))]
        for i in range(n):
            out_edges[int(tail[i])].append(i)  # 出边 = 从 tail 发出的边
            in_edges[int(head[i])].append(i)   # 入边 = 汇入 head 的边
        # 每节点首条入边（按边序，确定性；示意布局查表用，避免逐边全表扫描）
        first_in_edge = np.full(len(node_ids), -1, dtype=np.int64)
        for i in range(n):
            h = int(head[i])
            if first_in_edge[h] < 0:
                first_in_edge[h] = i
        return cls(
            edge_ids=[e.edge_id for e in edges],
            tail=tail,
            head=head,
            lengths_m=lengths,
            lanes=lanes,
            free_flow_speed_kmh=speeds,
            capacity_veh_h=caps,
            storage_veh=storage,
            edge_geometries=[e.geometry for e in edges],
            node_count=len(node_ids),
            node_ids=node_ids,
            first_in_edge=first_in_edge,
            _out_edges=[np.array(x, dtype=np.int64) for x in out_edges],
            _in_edges=[np.array(x, dtype=np.int64) for x in in_edges],
            edge_index={eid: i for i, eid in enumerate(e.edge_id for e in edges)},
        )

    def out_edges_of_node(self, node: int) -> np.ndarray:
        return self._out_edges[node]

    def in_edges_of_node(self, node: int) -> np.ndarray:
        return self._in_edges[node]

    def is_egress(self, edge_idx: int) -> bool:
        """头节点无出边 → 该边是网络出口（车辆驶出口径，spec §4.2）。"""
        return self._out_edges[int(self.head[edge_idx])].size == 0

    def schematic_geometry(self, edge_idx: int) -> list[list[float]]:
        """无显式 geometry 时的确定性示意布局。

        节点里程 = 沿边序到该节点首条入边的累计长度（对链状/树状路网自然，
        一般图仅作占位可视化，不参与任何数值计算）。查表 O(1)。
        """
        f = int(self.first_in_edge[int(self.tail[edge_idx])])
        tail_x = float(self.lengths_m[: f + 1].sum()) if f >= 0 else 0.0
        length = float(self.lengths_m[edge_idx])
        return [[tail_x, 0.0], [tail_x + length, 0.0]]


# ── 路网实现 ──────────────────────────────────────────────────────────────


class GraphStateMatrix(SpatialStateMatrix):
    """有向路网状态（变量按边对齐，如排队车辆数）。"""

    def __init__(self, topology: GraphTopology, variables: dict[str, np.ndarray]):
        n = len(topology.edge_ids)
        for name, arr in variables.items():
            if arr.shape != (n,):
                raise SimulationStateError(
                    f"variable '{name}' shape {arr.shape} != edge count ({n},)",
                    context={"variable": name},
                )
        self.topology = topology
        self.variables = dict(variables)

    def total(self, variable: str) -> float:
        return float(self.variables[variable].sum())

    def minmax(self, variable: str) -> tuple[float, float]:
        arr = self.variables[variable]
        return float(arr.min()), float(arr.max())

    def validate(self) -> None:
        for name, arr in self.variables.items():
            if not np.all(np.isfinite(arr)):
                _raise_not_finite(name)
            if arr.min() < 0:
                _raise_negative(name)

    def copy(self) -> "GraphStateMatrix":
        return GraphStateMatrix(
            self.topology,
            {k: v.copy() for k, v in self.variables.items()},
        )

    def _edge_geometry(self, idx: int) -> list[list[float]]:
        geom = self.topology.edge_geometries[idx]
        if geom:
            return [list(p) for p in geom]
        return self.topology.schematic_geometry(idx)

    def to_features(self, variable, *, threshold=0.0, max_features=20_000,
                    extras=None):
        arr = self.variables[variable]
        idxs = np.nonzero(arr > threshold)[0]
        if idxs.size == 0:
            return [], None, False
        if idxs.size > max_features:
            idxs = idxs[:max_features]
            truncated = True
        else:
            truncated = False
        features: list[dict[str, Any]] = []
        xs: list[float] = []
        ys: list[float] = []
        for i in idxs.tolist():
            coords = self._edge_geometry(i)
            for x, y in coords:
                xs.append(x)
                ys.append(y)
            props: dict[str, Any] = {
                "edge_id": self.topology.edge_ids[i],
                "cell_value": float(arr[i]),
            }
            for extra_name, extra_arr in (extras or {}).items():
                props[extra_name] = float(extra_arr[i])
            features.append({
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": coords},
                "properties": props,
            })
        if not xs:
            return features, None, truncated
        bbox = [min(xs), min(ys), max(xs), max(ys)]
        return features, bbox, truncated


def restore_state_arrays(
    state: SpatialStateMatrix, arrays: dict[str, list]
) -> None:
    """检查点还原：把序列化数组写回 state（形状以现有 state 为准）。"""
    for name, values in arrays.items():
        if name not in state.variables:
            raise SimulationStateError(
                f"checkpoint variable '{name}' not in state",
                context={"variable": name},
            )
        restored = np.asarray(values, dtype=float)
        if restored.shape != state.variables[name].shape:
            raise SimulationStateError(
                f"checkpoint variable '{name}' shape mismatch",
                context={"variable": name},
            )
        state.variables[name] = restored


__all__ = [
    "GraphStateMatrix",
    "GraphTopology",
    "RasterStateMatrix",
    "SpatialStateMatrix",
    "restore_state_arrays",
]
