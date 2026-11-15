"""G47 测试夹具：真实 lon/lat 面要素、真实冻结源、真实计划。

刻意**不 mock 领域逻辑**：几何走 shapely/pyproj，数量走真实属性表，这样
"面积∝数量""邻接来自共享边"才是在测产品行为而不是测测试替身。

网格原点取四川盆地一带（104°E, 30.4°N），单元边长 0.25°：跨度为 0.5°–1.0°，
既不触发 ``AXIS_ORDER_SUSPECT``（x 全 > 90）也不触发 ``UNSUPPORTED_GEOGRAPHY``
（跨度 < 180°、纬度 < 66.5°）。投影后共享边长度约 24–28 km，远高于
``min_shared_length_m=10`` 的下限，因此"真邻接"与"角点相触"可以区分开。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from app.services.cartogram_authoring.contract import AuthoringPlan
from app.services.cartogram_authoring.snapshot import FrozenSource, content_fingerprint

ORIGIN: Tuple[float, float] = (104.0, 30.4)
CELL = 0.25
#: 默认数量表：3×3 网格，值 100..900（避开 0 与缺失，专测"正常面积比例"）。
DEFAULT_VALUES: Tuple[int, ...] = (100, 200, 300, 400, 500, 600, 700, 800, 900)


def ring(x0: float, y0: float, side: float) -> List[List[float]]:
    """逆时针闭合环（GeoJSON 右手规则）。"""
    return [[x0, y0], [x0 + side, y0], [x0 + side, y0 + side],
            [x0, y0 + side], [x0, y0]]


def square(
    fid: str, x0: float, y0: float, side: float = CELL,
    props: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "type": "Feature",
        "id": fid,
        "geometry": {"type": "Polygon", "coordinates": [ring(x0, y0, side)]},
        "properties": dict(props or {}),
    }


def grid_features(
    rows: int = 3,
    cols: int = 3,
    *,
    value_field: str = "pop",
    values: Optional[Sequence[Any]] = None,
    origin: Tuple[float, float] = ORIGIN,
    cell: float = CELL,
    extra_props: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """行优先的 ``rows×cols`` 网格面要素；id = ``r{row}c{col}``。

    ``values`` 必须与单元数一致 —— 不循环复用，否则"第 10 个单元拿到第 1 个
    值"这种静默错位会让断言看起来通过。
    """
    seq = list(values) if values is not None else list(DEFAULT_VALUES)
    expected = rows * cols
    if len(seq) != expected:
        raise ValueError(f"values 需要 {expected} 个（rows×cols），收到 {len(seq)} 个")
    feats: List[Dict[str, Any]] = []
    idx = 0
    for r in range(rows):
        for c in range(cols):
            fid = f"r{r}c{c}"
            props: Dict[str, Any] = {
                "name": f"{fid.upper()}",
                value_field: seq[idx],
            }
            if extra_props:
                props.update(extra_props)
            feats.append(square(fid, origin[0] + c * cell, origin[1] + r * cell,
                                cell, props))
            idx += 1
    return feats


def frozen(
    features: List[Dict[str, Any]], *, ref_id: str = "ref:g47-fixture",
    revision: int = 7,
) -> FrozenSource:
    """构造同代冻结源（与生产 ``freeze_source`` 返回同构）。"""
    return FrozenSource(
        ref_id=ref_id, content_revision=revision,
        content_fingerprint=content_fingerprint(features),
        features=features, feature_count=len(features))


def plan(
    *,
    layer_id: str = "layer-g47",
    field: str = "pop",
    unit: str = "人",
    meaning: str = "count",
    max_radius: float = 36.0,
    extent: float = 1000.0,
    max_iterations: int = 400,
    max_features: int = 400,
    max_seconds: float = 30.0,
    title: Optional[str] = None,
    include_ids: Optional[List[str]] = None,
    exclude_ids: Optional[List[str]] = None,
    exclude_reasons: Optional[Dict[str, str]] = None,
    source_anchor: Optional[Dict[str, Any]] = None,
    overrides: Optional[Dict[str, Any]] = None,
) -> AuthoringPlan:
    body: Dict[str, Any] = {
        "schema_version": 1,
        "mode": "dorling_circle",
        "layer_id": layer_id,
        "quantity": {"field": field, "unit": unit, "meaning": meaning,
                     "aggregation": "sum"},
        "scope": {
            "include_ids": include_ids,
            "exclude_ids": list(exclude_ids or []),
            "exclude_reasons": dict(exclude_reasons or {}),
        },
        "solver": {"max_iterations": max_iterations},
        "area_scale": {"max_radius": max_radius, "extent": extent},
        "budgets": {"max_features": max_features, "max_seconds": max_seconds},
        "title": title,
        "source_anchor": source_anchor,
    }
    if overrides:
        body.update(overrides)
    return AuthoringPlan(**body)


def two_cell_features(
    *, value_field: str = "pop", left: Any = 100, right: Any = 400,
) -> List[Dict[str, Any]]:
    """并排两格（共享一条边），用于最小可读的邻接/重叠断言。"""
    return [
        square("a", ORIGIN[0], ORIGIN[1], CELL, {"name": "A", value_field: left}),
        square("b", ORIGIN[0] + CELL, ORIGIN[1], CELL,
               {"name": "B", value_field: right}),
    ]


def area_of_ring(coords: Sequence[Sequence[float]]) -> float:
    """鞋带公式（用于独立核对参考轮廓是否保留相对位置/尺度）。"""
    pts = list(coords)
    if len(pts) < 3:
        return 0.0
    acc = 0.0
    for i in range(len(pts) - 1):
        x1, y1 = float(pts[i][0]), float(pts[i][1])
        x2, y2 = float(pts[i + 1][0]), float(pts[i + 1][1])
        acc += x1 * y2 - x2 * y1
    return abs(acc) / 2.0


__all__ = [
    "ORIGIN", "CELL", "DEFAULT_VALUES", "ring", "square", "grid_features",
    "frozen", "plan", "two_cell_features", "area_of_ring",
]
