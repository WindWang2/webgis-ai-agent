"""移动对象轨迹 域算法包（GOAL-20261127-02；ADR-0099 §34 domain packs）。

本模块是 moving_object 域的唯一事实源：轨迹构建（测地分段）、驻留识别
（半径+时长+点数阈值法）、区域事件（enter/exit/cross/dwell 决策表）与
质量诊断。实现位于 app/services/moving_object/，工具层只做薄包装 +
证据块。

科学纪律：全部距离/方位为 WGS84 测地量（pyproj.Geod）；速度为观测区间
估计（非瞬时值）；enter/exit 时刻只给观测区间（inferred），cross 不伪造
时刻；区域谓词为 lon/lat 拓扑（plate carrée），结果内披露该约定。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.algorithm_registry import AlgorithmDescriptor

ALGORITHMS: List[AlgorithmDescriptor] = [
    AlgorithmDescriptor(
        id="moving_object.trajectory_build", name="轨迹构建",
        category="spatiotemporal_analysis",
        capabilities=["trajectory_build"],
        input_artifact_types=["point_feature_set"],
        output_artifact_type="line_feature_set",
        geometry_requirements=["point"],
        required_fields=["entity_id", "timestamp"],
        optional_fields=["source_row", "accuracy_m"],
        crs_requirements="EPSG:4326 lon/lat only",
        unit_requirements="distances_meters, speeds_km_per_h",
        max_features_hint=500_000,
        complexity="O(n log n) ingest sort + O(n) segmentation",
        cpu_cost="medium", memory_cost="medium", io_cost="low",
        preferred_execution_policy="THREAD",
        tool_candidates=["trajectory_build"],
        algorithm_family="trajectory_construction",
        assumptions=[
            "时间戳带时区或按 declared assumed_timezone 解释（DST fold=0）",
            "轨迹身份 = entity_id（稳定字符串），绝不使用数组索引",
            "速度/方向为相邻观测间区间估计；dt=0 对不产生速度（无假零/inf）",
            "断点（时隙/速度跳变/距离跳变/同时刻位置矛盾）显式分段并可溯源",
        ],
        limitations=[
            "稀疏采样导致的混叠跳变按断点披露，不做地图匹配或插值补齐",
            "采样间隔断裂处轨迹语义是断开的，连接两段只是显示约定",
            "同采样时刻不同位置是数据矛盾：分段而非猜测",
        ],
        crs_class="GEODESIC",
        random_seed_policy="deterministic",
        scientific_status="VALIDATED",
        conformance_tests=[
            "tests/unit/moving_object/test_motion.py::test_straight_line_oracle_builds_single_segment",
            "tests/unit/moving_object/test_motion.py::test_gap_breaks_segment_with_traceable_rows",
            "tests/unit/moving_object/test_motion.py::test_zero_dt_positional_conflict_breaks",
        ],
    ),
    AlgorithmDescriptor(
        id="moving_object.trajectory_dwell", name="驻留识别",
        category="spatiotemporal_analysis",
        capabilities=["trajectory_dwell"],
        input_artifact_types=["point_feature_set"],
        output_artifact_type="point_feature_set",
        geometry_requirements=["point"],
        required_fields=["entity_id", "timestamp"],
        unit_requirements="radius_meters, durations_seconds",
        max_features_hint=500_000,
        complexity="O(n) anchor-radius run split",
        cpu_cost="low", memory_cost="low", io_cost="low",
        preferred_execution_policy="THREAD",
        tool_candidates=["trajectory_build"],
        algorithm_family="dwell_detection",
        assumptions=[
            "驻留 = 锚定首修复径内连续观测游程，时长≥min_dwell_s 且≥min_points",
            "参数（半径/时长/点数）全部显式声明，可验证可复现",
        ],
        limitations=[
            "GPS 漂移或稀疏采样会把一次物理停留拆成多个驻留（显式披露，不自动合并）",
            "驻时刻为观测粒度（到达/离开发生在观测间隙时只给区间）",
        ],
        crs_class="GEODESIC",
        random_seed_policy="deterministic",
        scientific_status="VALIDATED",
        conformance_tests=[
            "tests/unit/moving_object/test_motion.py::test_static_noise_produces_one_dwell",
            "tests/unit/moving_object/test_motion.py::test_dwell_below_min_duration_is_near_miss",
        ],
    ),
    AlgorithmDescriptor(
        id="moving_object.region_events", name="区域时空事件",
        category="spatiotemporal_analysis",
        capabilities=["region_events"],
        input_artifact_types=["point_feature_set", "polygon_feature_set"],
        output_artifact_type="event_table",
        geometry_requirements=["point", "polygon"],
        required_fields=["entity_id", "timestamp", "region_id"],
        unit_requirements="times_utc_iso, durations_seconds",
        max_features_hint=500_000,
        complexity="O(fixes × candidate_regions) prepared-geometry walk",
        cpu_cost="medium", memory_cost="medium", io_cost="low",
        preferred_execution_policy="THREAD",
        tool_candidates=["trajectory_build"],
        algorithm_family="spatiotemporal_event_detection",
        assumptions=[
            "inside 谓词按边界规则 closed/open 判定（covers/contains），结果内披露",
            "enter/exit 时刻只给相邻观测构成推断区间（inferred=true）；绝不伪造精确时刻",
            "两端均在区域外的观测段若穿越区域 → cross（只给区间）",
            "区域级 dwell 由连续 inside 修复段合并，界为确切观测时刻",
            "反经线：解绕 ±360 并对区域三帧测试；跨日界线区域（经度跨幅>180°）类型化拒绝",
            "区域谓词为 lon/lat 拓扑约定（plate carrée），与测地运动量并行披露",
        ],
        limitations=[
            "全发生在两个观测之间的多次进出不可分辨（采样假设，披露）",
            "观测谓词失败按 unknown 计数，状态保留至下一可判定修复",
        ],
        crs_class="GEODESIC",
        random_seed_policy="deterministic",
        scientific_status="VALIDATED",
        conformance_tests=[
            "tests/unit/moving_object/test_events.py::test_enter_exit_brackets_are_inferred_intervals",
            "tests/unit/moving_object/test_events.py::test_sparse_skip_yields_cross_only",
            "tests/unit/moving_object/test_events.py::test_hole_pass_through_events",
            "tests/unit/moving_object/test_events.py::test_dateline_crossing_track",
        ],
    ),
    AlgorithmDescriptor(
        id="moving_object.trajectory_quality", name="轨迹质量诊断",
        category="spatiotemporal_analysis",
        capabilities=["trajectory_quality"],
        input_artifact_types=["point_feature_set"],
        output_artifact_type="stats_table",
        geometry_requirements=["point"],
        required_fields=["entity_id", "timestamp"],
        max_features_hint=500_000,
        complexity="O(n log n)",
        cpu_cost="low", memory_cost="low", io_cost="low",
        preferred_execution_policy="THREAD",
        tool_candidates=["trajectory_inspect", "trajectory_build"],
        algorithm_family="data_quality_diagnostics",
        assumptions=[
            "诊断与清洗建议分离：清洗只给有界预览，应用需重新摄取到新 ref",
            "缺失 accuracy 绝不插补；无 accuracy 的行对精度过滤不可见并在预览披露",
        ],
        limitations=[
            "清洗预览不改变数据；应用=以合并后的配置重建新版本",
        ],
        crs_class="CRS_AGNOSTIC",
        random_seed_policy="deterministic",
        scientific_status="VALIDATED",
        conformance_tests=[
            "tests/unit/moving_object/test_motion.py::test_quality_report_separates_diagnostics",
            "tests/unit/moving_object/test_motion.py::test_cleaning_proposal_is_preview_only",
        ],
    ),
]
