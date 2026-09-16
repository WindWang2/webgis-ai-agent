"""RS Temporal Cube 端到端 pipeline —— cube→feature→fusion→product。

定位（本方向 Oracle 主链路）：

1. **对齐**（``rs_alignment.align_acquisitions``）：跨模态配对计划 +
   类型化缺口账（网格恒等 typed 拒绝在前，绝不静默重采样）；
2. **特征**（``rs_features``/``phenology``）：光学/SAR 各自的时序特征包
   （质量掩膜先于特征：quality=0 切片进特征前置 NaN——缺测/质量差
   **不充当 0 或有效观测**）；
3. **融合**（``rs_fusion``）：特征级联合栈 + 晚期证据融合（两模态
   change_z 的描述性加权与符号一致性）；
4. **产品**（MapProduct 语义）：栅格图层 + chart/table 通道 + 工件链
   （``ArtifactDescriptor`` lineage 贯通两 cube——谱系可解释）；
5. 可选**样本**：多边形挂接 + 块折 split（leakage 不变量进 product 表）。

诚实边界：本 pipeline **不落盘任何栅格**——生产路径的图层 payload 经
raster_store / fabric ref 通道提供，这里只组装有界摘要 + 图层统计 +
chart/table 通道（refs-only 纪律）；不训练模型；融合是描述性的。
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence

import numpy as np

from app.lib.geo_analysis import rs_fusion, rs_features, rs_gaps
from app.lib.geo_analysis import rs_alignment
from app.lib.geo_analysis import rs_samples
from app.lib.geo_analysis.rs_cube_descriptor import (
    TemporalRasterCubeDescriptor,
)

#: 摘要通道各图表序列的点位上限（refs-only 有界纪律）。
_CHART_MAX_POINTS = 32


def _check_stack_inputs(
    stack: np.ndarray,
    times: np.ndarray,
    descriptor: TemporalRasterCubeDescriptor,
    what: str,
) -> np.ndarray:
    arr = np.asarray(stack, dtype=float)
    if arr.ndim != 3:
        raise ValueError(f"{what} 栈须为 (T,H,W)，got {arr.shape}")
    t = np.asarray(times, dtype=float)
    if t.ndim != 1 or len(t) != arr.shape[0]:
        raise ValueError(
            f"{what} times_sec 与栈时间轴不等长（{arr.shape[0]} vs {len(t)}）"
            "——缺测切片必须从栈与时间轴中一致缺席（或以 gap_code 声明），"
            "不允许错位对齐")
    # payload ↔ descriptor 一致性：栈时刻必须都能在描述符同模态资产中找到
    # （有 payload 无 ref = 来源不明的切片，typed 拒绝——refs-only 纪律）
    from app.lib.geo_analysis.rs_cube_descriptor import parse_time_iso

    desc_times = {
        round(parse_time_iso(a.time_iso), 3)
        for a in descriptor.assets if a.role == (
            "optical" if what == "optical" else "sar")
    }
    for ts in np.unique(t):
        if round(float(ts), 3) not in desc_times:
            raise ValueError(
                f"{what} 栈时刻 {ts!r} 在描述符 {descriptor.cube_id} 的"
                f"{what}资产中不存在——payload 与 refs 漂移（先修 ref）")
    return arr


def _layer_stats(arr: np.ndarray) -> Dict[str, Any]:
    a = np.asarray(arr, dtype=float)
    finite = np.isfinite(a)
    out: Dict[str, Any] = {
        "shape": list(a.shape),
        "valid_ratio": round(float(finite.sum()) / a.size, 6) if a.size else 0.0,
    }
    if finite.any():
        vals = a[finite]
        out["quantiles"] = [round(float(np.quantile(vals, q)), 6)
                            for q in (0.05, 0.5, 0.95)]
    else:
        out["quantiles"] = [None, None, None]
    return out


def _artifact(artifact_type: str, *, data_ref: str = "",
              source_capability: str = "rs_temporal_cube",
              producer_algorithm: str = "", producer_tool: str = "",
              lineage: Sequence[str] = (), properties: Optional[dict] = None,
              crs: str = ""):
    from app.lib.gis.artifacts import ArtifactDescriptor

    return ArtifactDescriptor(
        artifact_type=artifact_type,
        geometry_kind="raster" if artifact_type == "raster_surface" else "table",
        data_ref=data_ref,
        source_capability=source_capability,
        producer_algorithm=producer_algorithm,
        producer_tool=producer_tool,
        lineage=list(lineage)[:16],
        properties=dict(properties or {}),
        crs=crs,
    )


def run_temporal_cube_pipeline(
    optical: TemporalRasterCubeDescriptor,
    sar: TemporalRasterCubeDescriptor,
    *,
    optical_stack: np.ndarray,
    optical_times_sec: np.ndarray,
    sar_stack: np.ndarray,
    sar_times_sec: np.ndarray,
    optical_quality: Optional[np.ndarray] = None,
    optical_gap_codes: Optional[Sequence[Optional[str]]] = None,
    sar_gap_codes: Optional[Sequence[Optional[str]]] = None,
    tolerance_days: int = 3,
    polygons: Optional[Sequence[Mapping[str, Any]]] = None,
    grid: Optional[Mapping[str, Any]] = None,
    product_id: str = "rs_temporal_product",
) -> Dict[str, Any]:
    """对齐 → 特征 → 融合 → 产品工件链（含可选样本 split）。

    - ``optical_quality``（T,H,W ∈ [0,1]）先于特征：quality=0 切片置 NaN
      （质量差不充当观测）；
    - 声明缺口（``*_gap_codes``）同时进入槽位账与覆盖卡；
    - 返回 dict：alignment/coverage_card/slot_table/*_features/fusion/
      product/samples/summary——摘要有界，图层 payload 只在 product.layers。
    """
    # ① 对齐（网格恒等 typed 拒绝最先发生——失败不付任何计算代价）
    plan = rs_alignment.align_acquisitions(
        optical, sar, tolerance_days=int(tolerance_days))
    coverage_card = rs_gaps.build_coverage_card(plan)
    slot_table = rs_gaps.joint_slot_table(plan)

    # ② 光学特征（quality 先行；声明缺口切片置 NaN——缺口不是观测）
    opt_arr = _check_stack_inputs(
        optical_stack, optical_times_sec, optical, "optical")
    opt_invalid = ~np.isfinite(opt_arr)
    if optical_quality is not None:
        q = np.asarray(optical_quality, dtype=float)
        if q.shape != opt_arr.shape:
            raise ValueError(
                f"optical_quality 形状 {q.shape} 与栈 {opt_arr.shape} 不一致")
        opt_invalid |= ~(q > 0.0)
    if optical_gap_codes:
        if len(optical_gap_codes) != opt_arr.shape[0]:
            raise ValueError(
                "optical_gap_codes 长度须与光学栈时间轴一致")
        declared_ids = rs_gaps.GAP_CODE_IDS
        for i, code in enumerate(optical_gap_codes):
            if code is not None:
                if code not in declared_ids:
                    raise ValueError(
                        f"gap_code {code!r} 不在 GAP_CODES 封闭词表")
                opt_invalid[i] = True      # 声明缺口 = 该切片不作观测
    opt_values = np.where(opt_invalid, np.nan, opt_arr)
    optical_features = rs_features.temporal_feature_pack(
        opt_values, optical_times_sec)

    # ③ SAR 特征
    sar_arr = _check_stack_inputs(sar_stack, sar_times_sec, sar, "sar")
    sar_invalid = ~np.isfinite(sar_arr)
    if sar_gap_codes:
        if len(sar_gap_codes) != sar_arr.shape[0]:
            raise ValueError("sar_gap_codes 长度须与 SAR 栈时间轴一致")
        for i, code in enumerate(sar_gap_codes):
            if code is not None:
                sar_invalid[i] = True
    sar_values = np.where(sar_invalid, np.nan, sar_arr)
    sar_features = rs_features.temporal_feature_pack(
        sar_values, sar_times_sec)

    # ④ 特征级融合（p50 + sen_slope 双侧）
    def _feat_pack(pack, prefix):
        feats = pack["features"]
        return {
            f"{name}": feats[name]
            for name in ("p50", "sen_slope") if name in feats
        }

    fusion = rs_fusion.build_joint_feature_stack(
        {f"optical::{k}": v for k, v in _feat_pack(optical_features, "optical").items()},
        {f"sar::{k}": v for k, v in _feat_pack(sar_features, "sar").items()},
    )

    # ⑤ 晚期证据融合：两模态 change_z（phenology.temporal_anomaly；需 T≥4）
    late = None
    if optical_features["meta"]["n_time_slices"] >= 4 and \
            sar_features["meta"]["n_time_slices"] >= 4:
        from app.lib.geo_analysis import phenology, temporal_cube as tc

        opt_cube = tc.build_cube(opt_values, optical_times_sec, label="optical")
        sar_cube = tc.build_cube(sar_values, sar_times_sec, label="sar")
        with np.errstate(invalid="ignore"):
            opt_change = phenology.temporal_anomaly(
                opt_cube)["features"]["change_z"]
            sar_change = phenology.temporal_anomaly(
                sar_cube)["features"]["change_z"]
        late = rs_fusion.late_evidence_fusion(
            opt_change, sar_change,
            weights={"optical": 1.0, "sar": 1.0})

    # ⑥ 产品组装（图层 + 图/表 + 工件链；不落盘——refs-only）
    fused_trend = late["fused"] if late is not None else (
        fusion["features"].get("optical::p50"))
    layers: Dict[str, Any] = {}
    for name, arr in (
        ("optical_p50", optical_features["features"]["p50"]),
        ("sar_p50", sar_features["features"]["p50"]),
        ("fused_trend", fused_trend),
        ("joint_coverage", fusion["coverage"].astype(float)),
    ):
        layers[name] = _layer_stats(arr)

    charts = []
    dt_points = [
        {"t": p.optical_time_iso, "dt_days": p.dt_days}
        for p in plan.pairs[:_CHART_MAX_POINTS]
    ]
    charts.append({
        "type": "line", "id": "pair_dt_days",
        "title": "光学×SAR 配对时距（天）",
        "points": dt_points,
    })
    charts.append({
        "type": "bar", "id": "gap_codes",
        "title": "缺口计数（按类型化码）",
        "counts": coverage_card["gap_counts"],
    })
    tables = [
        {"type": "stats_table", "id": "coverage_card",
         "rows": [coverage_card]},
        {"type": "stats_table", "id": "slot_table",
         "rows": [dict(s) for s in slot_table]},
    ]

    artifacts = [
        _artifact(
            "rs_cube_descriptor",
            producer_algorithm="remote.cube.describe",
            lineage=(optical.cube_id, sar.cube_id),
            data_ref=f"ref:rs-cube/{plan.cube_id}",
            properties={"coverage": coverage_card},
            crs=optical.grid.crs,
        ),
        _artifact(
            "raster_surface",
            producer_algorithm="remote.cube.fusion",
            lineage=(optical.cube_id, sar.cube_id),
            properties={"layer": "fused_trend", **{
                k: layers["fused_trend"][k]
                for k in ("valid_ratio", "quantiles")}},
            crs=optical.grid.crs,
        ),
        _artifact(
            "stats_table",
            producer_algorithm="remote.cube.align",
            lineage=(plan.cube_id,),
            properties={"coverage": coverage_card},
        ),
        _artifact(
            "chart_spec",
            producer_algorithm="remote.cube.features",
            lineage=(plan.cube_id,),
            properties={"charts": [c["id"] for c in charts]},
        ),
    ]

    # ⑦ 可选样本（多边形挂接 + 块折 split；leakage 不变量进表）
    samples = None
    if polygons:
        if grid is None:
            raise ValueError("polygons 需要同时提供 grid（栅格化底座）")
        attached = rs_samples.attach_polygon_samples(
            {"ndvi_p50": optical_features["features"]["p50"]},
            grid, list(polygons))
        matrix = rs_samples.build_sample_matrix(
            attached["records"],
            ["ndvi_p50"], min_valid_features=1)
        split = None
        if matrix["xy"].size and np.isfinite(matrix["xy"]).all():
            split = rs_samples.geographic_block_split(matrix["xy"], folds=2)
            tables.append({
                "type": "stats_table", "id": "split_report",
                "rows": [{
                    "n_samples": split["meta"]["n_samples"],
                    "fold_counts": split["meta"]["fold_counts"],
                    "invariant": split["meta"]["invariant"],
                    "disclosure": split["meta"]["disclosure"],
                }],
            })
        samples = {"attach": attached["meta"], "matrix": matrix,
                   "split": (split["meta"] if split else None)}
        artifacts.append(_artifact(
            "stats_table",
            producer_algorithm="remote.cube.samples",
            lineage=(plan.cube_id,),
            properties={"n_samples": matrix["meta"]["n_samples"],
                        "excluded": matrix["meta"]["n_excluded_insufficient"]},
        ))

    summary = {
        "product_id": product_id,
        "cube_id": plan.cube_id,
        "n_pairs": plan.coverage["n_pairs"],
        "pair_rate": plan.coverage["pair_rate"],
        "joint_missing_slots": plan.coverage["joint_missing_slots"],
        "gap_counts": coverage_card["gap_counts"],
        "n_layers": len(layers),
        "n_charts": len(charts),
        "n_tables": len(tables),
        "n_artifacts": len(artifacts),
        "late_fusion_applied": late is not None,
        "n_samples": (samples["matrix"]["meta"]["n_samples"]
                      if samples else 0),
    }
    return {
        "summary": summary,
        "alignment": plan,
        "coverage_card": coverage_card,
        "slot_table": slot_table,
        "optical_features": optical_features,
        "sar_features": sar_features,
        "fusion": fusion,
        "late_fusion": late,
        "product": {
            "product_id": product_id,
            "layers": layers,
            "charts": charts,
            "tables": tables,
            "artifacts": artifacts,
        },
        "samples": samples,
    }
