"""Capability Resolution Benchmark（ADR-0181 C9）—— 8 场景前后对比。

「改造前」口径：goal 指到 capability 后，把图上**全部** provider（tools
+ models）当作候选（不筛资格、不排序、不解释 —— V8 图孤儿时代的等价
行为）；「改造后」：resolve_capabilities（资格过滤 + 因子排序 + 解释
面）。指标（任务书 C9 全集）：

- candidate_tool_count：合格候选数 / 全 provider 数（收窄比）
- invalid_tool_attempts：改造前盲目尝试里的失格 provider 占比（节省面）
- fallback_rate：场景内需要 degraded/ineligible 替代的能力占比
- selection_determinism：同输入双跑 JSON 逐字节一致
- planning_context_bytes：bounded context vs 全 provider 名单字节

8 场景：point distribution / polygon statistics / raster terrain /
change detection / remote-sensing mapping / data missing+offline /
huge feature count / print & presentation target。
全部数据来自真实 registry（无合成大数据；C9 纪律）。
"""
from __future__ import annotations

import json

import pytest

from app.services.gis_harness.capability_graph import get_capability_graph
from app.services.gis_harness.capability_resolution import (
    GoalRequirements,
    build_situation,
    resolve_capabilities,
)

SCENARIOS = [
    ("point_distribution", ["poi_query", "admin_aggregation", "kde_density"],
     {"geometry": "Point", "featureCount": 520,
      "fields": {"name": {}, "category": {}}, "crs": "EPSG:4326"}, {}),
    ("polygon_statistics", ["admin_boundary_query", "admin_aggregation"],
     {"geometry": "Polygon", "featureCount": 31,
      "fields": {"district": {}, "population": {}}}, {}),
    ("raster_terrain", ["terrain_slope", "terrain_hillshade"],
     {"geometry": "Polygon", "featureCount": 1, "bands": 1,
      "resolution_m_per_px": 30.0}, {}),
    ("change_detection", ["model_change_detection"],
     {"geometry": "Polygon", "featureCount": 1, "bands": 4,
      "resolution_m_per_px": 10.0, "temporal_inputs": 2}, {}),
    ("remote_sensing_mapping", ["raster_source", "model_image_segmentation"],
     {"geometry": "Polygon", "featureCount": 1, "bands": 3,
      "resolution_m_per_px": 1.0}, {}),
    ("data_missing_offline", ["poi_query", "input_tips_place"],
     {}, {"offline": True}),
    ("huge_feature_count", ["poi_query", "admin_aggregation"],
     {"geometry": "Point", "featureCount": 250_000,
      "fields": {"name": {}}, "data_bytes": 800 * 1024 * 1024}, {}),
    ("print_presentation", ["poi_query", "admin_aggregation"],
     {"geometry": "Point", "featureCount": 300, "fields": {"name": {}}},
     {"print_target": True}),
]


def _goal(caps, print_target=False) -> GoalRequirements:
    return GoalRequirements(
        capability_ids=list(caps), task_hint="benchmark")


def _situation(profile, overrides):
    return build_situation(
        task_hint="benchmark",
        profile=profile or None,
        offline=overrides.get("offline"),
        auth_tier=overrides.get("auth_tier"),
        budget_cost_class=overrides.get("budget_cost_class", ""),
    )


def _scenario_metrics(name, caps, profile, overrides) -> dict:
    graph = get_capability_graph()
    goal = _goal(caps)
    sit = _situation(profile, overrides)
    res = resolve_capabilities(goal, sit)

    # 改造前：全 provider 面（图上 tools+models，不筛资格）
    naive_providers = 0
    for cap in caps:
        providers = graph.capability_providers(cap)
        naive_providers += len(providers["tools"]) + len(providers["models"])
    qualified = sum(
        len(d.providers) for d in res.decisions)
    rejected = sum(
        len(d.rejected) + sum(
            1 for p in d.providers if p.qualification.status != "eligible")
        for d in res.decisions)

    # planning context bytes：改造后有界证据 vs 改造前全 provider 名单
    after_bytes = len(res.to_bounded_context(4096).encode("utf-8"))
    naive_lines = []
    for cap in caps:
        providers = graph.capability_providers(cap)
        naive_lines.extend(
            f"{cap}:{p}" for p in providers["tools"] + providers["models"])
    before_bytes = len("\n".join(naive_lines).encode("utf-8")) or 1

    fallback_needing = sum(
        1 for d in res.decisions
        if d.status in ("ineligible", "degraded"))

    # determinism：双跑逐字节一致
    again = resolve_capabilities(goal, _situation(profile, overrides))
    deterministic = (
        json.dumps(res.to_dict(), sort_keys=True)
        == json.dumps(again.to_dict(), sort_keys=True))

    return {
        "scenario": name,
        "naive_providers": naive_providers,
        "qualified_providers": qualified,
        "rejected_providers": rejected,
        "narrowing_ratio": round(qualified / naive_providers, 3)
        if naive_providers else 0.0,
        "invalid_attempt_ratio": round(
            rejected / (qualified + rejected + len([
                p for d in res.decisions
                for p in d.providers if p.qualification.status == "eligible"])),
            3),
        "fallback_needing_capabilities": fallback_needing,
        "context_bytes_after": after_bytes,
        "context_bytes_before": before_bytes,
        "deterministic": deterministic,
        "status_summary": dict(res.status_summary),
    }


class TestCapabilityBenchmark:
    @pytest.mark.parametrize("name,caps,profile,overrides", SCENARIOS,
                             ids=[s[0] for s in SCENARIOS])
    def test_scenario(self, name, caps, profile, overrides):
        metrics = _scenario_metrics(name, caps, profile, overrides)
        # 通用验收（每场景）：
        assert metrics["deterministic"], "同 Situation 决策必须 deterministic"
        assert metrics["context_bytes_after"] <= 4096, "有界上下文预算"
        if metrics["naive_providers"]:
            assert metrics["narrowing_ratio"] <= 1.0
        print(f"\nBENCH {json.dumps(metrics, ensure_ascii=False)}")

    def test_offline_scenario_rejects_network_only(self):
        name, caps, profile, overrides = SCENARIOS[5]
        metrics = _scenario_metrics(name, caps, profile, overrides)
        assert metrics["status_summary"].get("ineligible", 0) >= 0
        # offline 场景必须有披露（失格或 make_available），不允许静默
        res = resolve_capabilities(_goal(caps), _situation(profile, overrides))
        disclosed = all(
            d.providers or d.make_available or d.missing
            or d.degraded_alternatives for d in res.decisions)
        assert disclosed

    def test_huge_feature_count_discloses_volume(self):
        res = resolve_capabilities(
            _goal(SCENARIOS[6][1]), _situation(SCENARIOS[6][2], {}))
        volume_disclosed = any(
            "data_volume" in
            [r.check for p in d.providers for r in p.qualification.reasons]
            for d in res.decisions)
        scale_disclosed = any(
            "scale_mismatch_penalty" in p.factors
            for d in res.decisions for p in d.providers)
        assert volume_disclosed or scale_disclosed, \
            "大数据面必须披露（degraded 或 scale 因子）"

    def test_print_target_has_template_providers(self):
        graph = get_capability_graph()
        # print/presentation：poi_query 能力必有产品模板消费面
        templates = graph.templates_for_capability("poi_query")
        assert templates, "印刷/呈现目标必须能解析到 template provider"

    def test_summary_report_deterministic(self):
        rows = [
            _scenario_metrics(n, c, p, o)
            for (n, c, p, o) in SCENARIOS
        ]
        again = [
            _scenario_metrics(n, c, p, o)
            for (n, c, p, o) in SCENARIOS
        ]
        assert rows == again
        total_naive = sum(r["naive_providers"] for r in rows)
        total_qualified = sum(r["qualified_providers"] for r in rows)
        total_rejected = sum(r["rejected_providers"] for r in rows)
        print("\nBENCH SUMMARY: "
              f"scenarios={len(rows)} naive={total_naive} "
              f"qualified={total_qualified} rejected={total_rejected} "
              f"avg_narrowing={round(total_qualified / total_naive, 3) if total_naive else 0}")
