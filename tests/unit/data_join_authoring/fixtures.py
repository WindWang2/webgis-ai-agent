"""G38 测试夹具 —— 可人工推导的 oracle 数据（J01–J14、J24/J25 同源）。"""
from __future__ import annotations

from typing import Any, Dict, List


def _poly(rings: List[List[List[float]]]) -> Dict[str, Any]:
    return {"type": "Polygon", "coordinates": rings}


def square(x0: float, y0: float, x1: float, y1: float) -> Dict[str, Any]:
    return _poly([[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]])


# ── 旗舰 A：行政区边界（矢量）× 人口表 ──────────────────────────────

ADMIN_BOUNDARIES: Dict[str, Any] = {
    "type": "FeatureCollection",
    "crs": "EPSG:4326",
    "features": [
        {"type": "Feature", "properties": {"_wfid": "wf1-a", "adcode": "110101",
                                           "name": "东城区"},
         "geometry": square(116.40, 39.88, 116.46, 39.94)},
        {"type": "Feature", "properties": {"_wfid": "wf1-b", "adcode": "110102",
                                           "name": "西城区"},
         "geometry": square(116.34, 39.88, 116.40, 39.94)},
        {"type": "Feature", "properties": {"_wfid": "wf1-c", "adcode": "110105",
                                           "name": "朝阳区"},
         "geometry": square(116.46, 39.88, 116.56, 39.96)},
        # J01：左关联无匹配区（adcode 无人口行）
        {"type": "Feature", "properties": {"_wfid": "wf1-d", "adcode": "110107",
                                           "name": "石景山区"},
         "geometry": square(116.18, 39.88, 116.24, 39.94)},
    ],
}

POPULATION_TABLE: Dict[str, Any] = {
    "rows": [
        {"adcode": "110101", "pop": 708_829, "year": 2020},
        {"adcode": "110102", "pop": 1_106_214, "year": 2020},
        {"adcode": "110105", "pop": 3_452_460, "year": 2020},
        # J04：复合键场景 —— 同名县不同省（见 COMPOSITE_*）
        # J06：右键重复 —— 见 DUPLICATE_RIGHT_TABLE
        # 未匹配右行（无边界）：J02 对账
        {"adcode": "110999", "pop": 1, "year": 2020},
    ]
}


def population_table() -> Dict[str, Any]:
    import copy
    return copy.deepcopy(POPULATION_TABLE)


def admin_boundaries() -> Dict[str, Any]:
    import copy
    return copy.deepcopy(ADMIN_BOUNDARIES)


# ── 复合键（J04）：省代码+县代码，同县名跨省 ─────────────────────────

COMPOSITE_LEFT_ROWS = {
    "rows": [
        {"prov": "13", "county": "正定县", "value": 1},
        {"prov": "41", "county": "正定县", "value": 2},   # 同名县不同省
        {"prov": "13", "county": "无匹配县", "value": 3},
    ]
}
COMPOSITE_RIGHT_ROWS = {
    "rows": [
        {"p": "13", "c": "正定县", "region": "河北"},
        {"p": "41", "c": "正定县", "region": "河南"},     # 同名县 —— 单键会串
        {"p": "13", "c": "别县", "region": "河北2"},
    ]
}


# ── 键类型/空值（J05）───────────────────────────────────────────────

TYPES_LEFT_ROWS = {
    "rows": [
        {"k": "001"}, {"k": "1"}, {"k": 1}, {"k": True},
        {"k": ""}, {"k": None}, {},                # missing 键
    ]
}
TYPES_RIGHT_ROWS = {
    "rows": [
        {"k": "001", "v": "s001"}, {"k": "1", "v": "s1"},
        {"k": 1, "v": "n1"}, {"k": 1.0, "v": "n1.0"},  # 1 == 1.0（数值族）
        {"k": True, "v": "bT"}, {"k": "", "v": "empty"},
        {"k": None, "v": "null"}, {"k": False, "v": "bF"},
    ]
}


# ── 右键重复（J06）/ 一对多展开（J07）/ many-to-many 爆炸（J08）────

DUPLICATE_RIGHT_TABLE = {
    "rows": [
        {"adcode": "110101", "pop": 100, "year": 2010},
        {"adcode": "110101", "pop": 200, "year": 2020},   # 右键重复
        {"adcode": "110102", "pop": 300, "year": 2020},
    ]
}

FANOUT_LEFT_ROWS = {"rows": [{"code": "A"}, {"code": "B"}]}
FANOUT_RIGHT_ROWS = {"rows": [{"code": "A", "v": 1}, {"code": "A", "v": 2},
                              {"code": "B", "v": 3}]}


def explosion_fixture(n_dup: int = 1200) -> Dict[str, Any]:
    """双侧重复 → 乘积远超 pair 预算（J08 分配前拦截）。"""
    return {
        "left": {"rows": [{"k": i % 5} for i in range(n_dup)]},
        "right": {"rows": [{"k": i % 5, "v": i} for i in range(n_dup)]},
    }


# ── 旗舰 B：监测点 × 区域（空间） ───────────────────────────────────

def regions_fc() -> Dict[str, Any]:
    """四个面：R1/R2 重叠（多命中）、R3 带洞、R4 独立（边界 oracle）。"""
    r1 = square(0.0, 0.0, 10.0, 10.0)
    r2 = square(5.0, 0.0, 15.0, 10.0)              # 与 R1 重叠 [5,10]×[0,10]
    r3 = _poly([[[20.0, 0.0], [30.0, 0.0], [30.0, 10.0], [20.0, 10.0], [20.0, 0.0]],
                [[23.0, 3.0], [27.0, 3.0], [27.0, 7.0], [23.0, 7.0], [23.0, 3.0]]])
    return {
        "type": "FeatureCollection",
        "crs": "EPSG:4326",
        "features": [
            {"type": "Feature", "properties": {"_wfid": "wf1-r1", "rid": "R1",
                                               "priority": 2},
             "geometry": r1},
            {"type": "Feature", "properties": {"_wfid": "wf1-r2", "rid": "R2",
                                               "priority": 1},
             "geometry": r2},
            {"type": "Feature", "properties": {"_wfid": "wf1-r3", "rid": "R3",
                                               "priority": 5},
             "geometry": r3},
            # R4：独立面（无重叠）—— P7 恰在其西边界 x=35（J11 边界 oracle）
            {"type": "Feature", "properties": {"_wfid": "wf1-r4", "rid": "R4",
                                               "priority": 9},
             "geometry": square(35.0, 0.0, 45.0, 10.0)},
        ],
    }


def points_fc() -> Dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "crs": "EPSG:4326",
        "features": [
            # 仅 R1 内
            {"type": "Feature", "properties": {"id": "P1", "pid": "P1"},
             "geometry": {"type": "Point", "coordinates": [2.0, 5.0]}},
            # R1∩R2 重叠区（J10 多命中）
            {"type": "Feature", "properties": {"id": "P2", "pid": "P2"},
             "geometry": {"type": "Point", "coordinates": [7.0, 5.0]}},
            # x=10：R1 东边界（=R2 内部）—— within 下命中 R2
            {"type": "Feature", "properties": {"id": "P3", "pid": "P3"},
             "geometry": {"type": "Point", "coordinates": [10.0, 5.0]}},
            # R3 的洞内（J11：不算面内）
            {"type": "Feature", "properties": {"id": "P4", "pid": "P4"},
             "geometry": {"type": "Point", "coordinates": [25.0, 5.0]}},
            # R3 实体环带内
            {"type": "Feature", "properties": {"id": "P5", "pid": "P5"},
             "geometry": {"type": "Point", "coordinates": [21.0, 5.0]}},
            # 恰在 R4 西边界 x=30（J11 边界：within 不命中 / covers 命中 R4）
            {"type": "Feature", "properties": {"id": "P7", "pid": "P7"},
             "geometry": {"type": "Point", "coordinates": [35.0, 5.0]}},
            # 任何面外
            {"type": "Feature", "properties": {"id": "P6", "pid": "P6"},
             "geometry": {"type": "Point", "coordinates": [50.0, 5.0]}},
        ],
    }


# ── 异 CRS（J12）：点 4326 × 面 3857 ────────────────────────────────

def points_4326_fc() -> Dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "crs": "EPSG:4326",
        "features": [
            {"type": "Feature", "properties": {"id": "Q1", "pid": "Q1"},
             "geometry": {"type": "Point", "coordinates": [1.0, 1.0]}},
        ],
    }


def regions_3857_fc() -> Dict[str, Any]:
    """EPSG:3857 下 [0,0]-[2,0]-[2,2]-[0,2]（≈经纬 [0,0]-[1.8,1.8]）—— Q1 落内。"""
    return {
        "type": "FeatureCollection",
        "crs": "EPSG:3857",
        "features": [
            {"type": "Feature", "properties": {"rid": "Z1"},
             "geometry": square(0.0, 0.0, 200000.0, 200000.0)},
        ],
    }


def loaded_from_fc(side: str, fc: Dict[str, Any], fingerprint: str = "fp"):
    """FC → LoadedSource（executor 测试用；crs 取 FC 顶层声明）。"""
    from app.services.data_join_authoring.executor import LoadedSource

    rows, geoms = [], []
    for f in fc.get("features") or []:
        rows.append(dict(f.get("properties") or {}))
        geoms.append(f.get("geometry"))
    return LoadedSource(
        side=side, label=side, is_table=False,
        crs=fc.get("crs"), content_fingerprint=fingerprint,
        rows=rows, geometries=geoms,
        declared_fields=sorted(rows[0].keys()) if rows else [],
    )


def loaded_from_rows(side: str, rows_doc: Dict[str, Any], fingerprint: str = "fp"):
    from app.services.data_join_authoring.executor import LoadedSource

    rows = rows_doc["rows"]
    return LoadedSource(
        side=side, label=side, is_table=True, crs=None,
        content_fingerprint=fingerprint, rows=rows,
        declared_fields=sorted(rows[0].keys()) if rows else [],
    )


def attr_plan(**overrides) -> Dict[str, Any]:
    plan: Dict[str, Any] = {
        "mode": "attribute",
        "title": "人口按区代码关联",
        "left": {"kind": "session_ref", "session_id": "s", "ref": "ref:admin"},
        "right": {"kind": "table_rows", "session_id": "s", "ref": "ref:pop"},
        "keys": [{"left_field": "adcode", "right_field": "adcode"}],
        "keep_side": "left",
        "output_cardinality": "many_to_one",
        "output_name": "人口富集区",
    }
    # 旗舰默认投影：右 adcode 已由键消费，不重复输出（避免同名冲突）。
    # 覆盖 keys/field_map 的用例按各自字段显式给投影。
    if "keys" not in overrides and "field_map" not in overrides:
        plan["field_map"] = [
            {"side": "left", "field": "adcode"},
            {"side": "left", "field": "name"},
            {"side": "right", "field": "pop"},
            {"side": "right", "field": "year"},
        ]
    plan.update(overrides)
    return plan


def spatial_plan(**overrides) -> Dict[str, Any]:
    plan: Dict[str, Any] = {
        "mode": "spatial_point_in_polygon",
        "title": "监测点归属区域",
        "left": {"kind": "session_ref", "session_id": "s", "ref": "ref:points"},
        "right": {"kind": "session_ref", "session_id": "s", "ref": "ref:regions"},
        "keep_side": "left",
        "output_cardinality": "many_to_one",
        "spatial": {"predicate": "within", "multi_match": "require_unique"},
        "output_name": "点归属面",
    }
    plan.update(overrides)
    return plan
