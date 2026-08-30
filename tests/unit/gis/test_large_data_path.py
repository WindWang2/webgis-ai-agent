"""大数据数据路径回归测试（V2 §18 / Scenario D / §26）。

保证 150k features 不进入 LLM context / Agent payload，且观测与画像
复杂度 O(层/字段) 而非 O(features)：
- slim_tool_result：150k FC → ref 摘要（有界字节、无坐标数组）；
- DatasetProfile.from_ref_descriptor：与 feature_count 无关的 O(1) 投影；
- quality evidence 有界（不随 feature 数增长）。
"""
import json
import time

from app.lib.gis.dataset_profile import DatasetProfile
from app.lib.geo_analysis.evidence import build_quality_evidence
from app.services.llm_result_formatter import slim_tool_result


def _big_fc(n=150_000):
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [104.0 + (i % 300) * 0.001, 30.0 + (i // 300) * 0.001]},
             "properties": {"id": i, "cat": f"c{i % 7}", "v": float(i)}}
            for i in range(n)
        ],
    }


def _payload_bytes(payload) -> int:
    return len(payload.encode("utf-8")) if isinstance(payload, str) else len(json.dumps(payload, default=str))


def test_150k_direct_fc_slims_to_summary():
    fc = _big_fc()
    result_str = json.dumps(fc)  # 全量序列化的假想 payload
    assert len(result_str) > 5_000_000  # 前提：原始 >5MB
    slim = slim_tool_result(fc, result_str, session_geojson_ref="ref:geojson-big")
    assert _payload_bytes(slim) < 64_000, "slim 后载荷必须有界（<64KB）"
    assert "ref:geojson-big" in slim
    assert "150000" in slim.replace(",", "") or "150,000" in slim
    # 绝不携带原始坐标数组
    assert '"coordinates": [104' not in slim and "104.0, 30.0" not in slim


def test_150k_wrapped_result_slims_via_summary_branch():
    fc = _big_fc()
    result = {"success": True, "summary": "h3 聚合完成", "data": fc,
              "quality_evidence": build_quality_evidence(
                  input_count=150000, output_count=300, working_crs="EPSG:4326")}
    slim = slim_tool_result(result, "", session_geojson_ref="ref:geojson-agg")
    assert _payload_bytes(slim) < 16_000
    assert "ref:geojson-agg" in slim
    assert "quality_evidence" in slim  # V2 P9：证据经 summary 分支存活


def test_slim_preserves_summary_branch_budget_for_pathological_summary():
    result = {"success": True, "summary": "x" * 500_000}
    slim = slim_tool_result(result, "", None)
    assert _payload_bytes(slim) < 64_000  # #439 总量闸


def test_profile_construction_is_o1_regardless_of_feature_count():
    """同一 descriptor 形状下，小/大数据集画像构建耗时同阶（无特征扫描）。"""
    def _desc(n):
        return {
            "ref_id": "ref:geojson-x",
            "feature_count": n,
            "geometry_types": ["Point"],
            "bbox": [104.0, 30.0, 106.0, 32.0],
            "field_schema": {f"f{i}": {"type": "number"} for i in range(32)},
        }

    t0 = time.perf_counter()
    for _ in range(200):
        DatasetProfile.from_ref_descriptor(_desc(1_000))
    small = time.perf_counter() - t0
    t0 = time.perf_counter()
    for _ in range(200):
        DatasetProfile.from_ref_descriptor(_desc(150_000))
    big = time.perf_counter() - t0
    # 构造器不接触 features 数组 —— 耗时与 feature_count 无关（宽松 3x smoke guard，
    # 真正确定性由「descriptor 输入形状相同 → 输出结构相同」保证）。
    assert big < max(small * 3.0, small + 0.05)


def test_quality_evidence_bounded():
    ev = build_quality_evidence(
        input_count=150_000, output_count=300,
        extra={f"k{i}": ("s" * 200) if i % 2 else i for i in range(40)},
    )
    assert len(ev) <= 16
    assert all(not isinstance(v, str) or len(v) <= 64 for v in ev.values())
    assert ev["input_count"] == 150_000
