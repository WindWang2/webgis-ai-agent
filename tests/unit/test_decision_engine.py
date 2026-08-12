"""
Unit and Integration Tests for DecisionEngine and ScenarioComparisonEngine.
Verifies Data-Grounded Spatial Decision Intelligence V2 end-to-end.
"""
import pytest
from app.services.spatial_decision.models import (
    SpatialDecisionResult,
    ScenarioComparisonResult,
)
from app.services.spatial_decision.engine import DecisionEngine
from app.services.spatial_decision.comparison_engine import ScenarioComparisonEngine
from app.services.spatial_decision.target_resolver import TargetAreaResolver


class _FakeGeocodeProvider:
    """确定性的离线地理编码替身（替换 Tianditu/Amap 在线调用）。

    测试环境没有可信的外网，真实 provider 的 HTTP 重试会让整组测试挂到超时。
    已知行政区名解析为固定几何，其余返回空 —— 不可解析路径的测试也因此保持确定。
    """

    KNOWN: dict[str, dict] = {
        "西湖区": {"type": "Polygon", "coordinates": [[[120.05, 30.20], [120.25, 30.20], [120.25, 30.35], [120.05, 30.35], [120.05, 30.20]]]},
        "余杭区": {"type": "Polygon", "coordinates": [[[119.90, 30.25], [120.20, 30.25], [120.20, 30.45], [119.90, 30.45], [119.90, 30.25]]]},
        "中关村": {"type": "Point", "coordinates": [116.3166, 39.9953]},
        "徐汇区": {"type": "Polygon", "coordinates": [[[121.40, 31.15], [121.48, 31.15], [121.48, 31.22], [121.40, 31.22], [121.40, 31.15]]]},
        "天河区": {"type": "Polygon", "coordinates": [[[113.30, 23.10], [113.42, 23.10], [113.42, 23.18], [113.30, 23.18], [113.30, 23.10]]]},
        "南山区": {"type": "Polygon", "coordinates": [[[113.88, 22.45], [113.98, 22.45], [113.98, 22.56], [113.88, 22.56], [113.88, 22.45]]]},
        "成都高新区": {"type": "Point", "coordinates": [104.06, 30.57]},
        "武昌区": {"type": "Polygon", "coordinates": [[[114.28, 30.52], [114.37, 30.52], [114.37, 30.58], [114.28, 30.58], [114.28, 30.52]]]},
    }

    @staticmethod
    def _match(address: str):
        for name, geom in _FakeGeocodeProvider.KNOWN.items():
            if name in address:
                return name, geom
        return None, None

    async def district(self, keywords, level="", return_geometry="point"):
        name, geom = self._match(keywords)
        if name:
            return {"features": [{"geometry": geom, "properties": {"name": name}}]}
        return {"features": []}

    async def geocode(self, address, city=""):
        name, geom = self._match(address)
        if name:
            if geom["type"] == "Point":
                lng, lat = geom["coordinates"]
            else:
                lng, lat = geom["coordinates"][0][0]
            return {"results": [{"location": [lng, lat], "formatted_address": address}]}
        return {"results": []}


@pytest.fixture
def engine():
    """注入假 provider 的 DecisionEngine —— 全链路离线且确定。"""
    return DecisionEngine(
        target_resolver=TargetAreaResolver(geocode_provider=_FakeGeocodeProvider())
    )


@pytest.fixture(autouse=True)
def patch_embed(monkeypatch):
    """RAG grounding 必须离线（仓库既有惯例，见 test_rag_durability.patch_embed）。

    不 patch 的话，证据链的向量检索会在 worker 线程里尝试从 HuggingFace 下载
    sentence-transformer 模型 —— 测试环境网络被禁时，huggingface_hub 会重试约
    半分钟，且 wait_for 无法取消 to_thread，最终把事件循环关停卡死（整个测试文件
    挂到 pytest-timeout）。返回零向量让 FAISS 空索引检索直接得到空结果，RAG
    grounding 走文档化的降级路径（rule_pack.retrieve_evidence_from_rag 的 fallback），
    这正是该路径本身该被测试覆盖的行为。
    """
    import numpy as np

    from app.services.rag.faiss_store import FaissVectorStore

    def fake(self, texts):
        return np.zeros((len(texts), 384), dtype=np.float32)

    monkeypatch.setattr(FaissVectorStore, "embed_texts", fake)


@pytest.mark.asyncio
async def test_decision_engine_subway_scenario(engine):
    """Test DecisionEngine on subway station scenario with geocoded target area."""
    
    result = await engine.evaluate_decision(
        scenario_text="新建地铁站，评估周边房价与交通影响",
        target_area_text="杭州市西湖区",
        parameters={"growth_pct": 10},
    )

    assert isinstance(result, SpatialDecisionResult)
    assert result.type == "spatial_decision_result"
    assert result.scenario.scenario_type == "subway"
    assert result.target_area.resolved_name != ""
    assert result.target_area.confidence > 0.0
    # NO hardcoded Beijing fallback [116.4, 39.9]
    assert result.target_area.center != (116.4, 39.9) or "杭州" not in result.target_area.query
    
    # Check metrics and uncertainty ranges
    assert "housing_price" in result.metrics
    hp_metric = result.metrics["housing_price"]
    assert hp_metric.simulated != 100.0 or hp_metric.baseline != 100.0  # Not hardcoded 100.0 default
    # GIS-03 (deep-audit): no fabricated values. If the metric has no real
    # baseline it is honestly unsimulated (None fields + gap note); otherwise
    # the uncertainty range must contain the simulated value.
    if hp_metric.missing_baseline:
        assert hp_metric.baseline is None
        assert hp_metric.simulated is None
        assert hp_metric.delta_pct is None
        assert hp_metric.evidence_gap_note is not None
    else:
        assert hp_metric.range is not None
        assert hp_metric.range.min_val <= hp_metric.simulated <= hp_metric.range.max_val

    # Check spatial impact zones (direct & indirect)
    assert len(result.spatial_impacts) >= 1
    assert result.simulation_geojson["type"] == "FeatureCollection"
    assert len(result.simulation_geojson["features"]) >= 1

    # Check evidence chain & recommendations
    assert len(result.evidence_chain) >= 1
    assert len(result.recommendations) >= 1
    assert result.confidence >= 0.0


@pytest.mark.asyncio
async def test_decision_engine_all_six_scenarios(engine):
    """Verify that all 6 required scenario types run real data-grounded evaluation."""
    
    scenarios_to_test = [
        ("subway", "新建地铁站影响评估", "北京市海淀区中关村"),
        ("school", "新建实验小学及学区规划", "上海市徐汇区"),
        ("hospital", "新建三甲医院建设分析", "广州市天河区"),
        ("park", "新建中央公园绿地项目", "深圳市南山区"),
        ("population_growth", "区域人口增长20%承载力预测", "成都高新区", {"growth_pct": 20}),
        ("traffic_restriction", "主要干道实施高峰限行政策", "武汉市武昌区"),
    ]

    for expected_type, text, area, *extra_params in scenarios_to_test:
        params = extra_params[0] if extra_params else {}
        res = await engine.evaluate_decision(
            scenario_text=text,
            target_area_text=area,
            parameters=params,
        )
        assert res.scenario.scenario_type == expected_type
        assert res.confidence > 0.0
        assert len(res.metrics) > 0
        assert len(res.rules_applied) > 0


@pytest.mark.asyncio
async def test_scenario_comparison_engine(engine):
    """Test multi-scenario comparison (Baseline vs Scenario A vs Scenario B vs Scenario C)."""
    cmp_engine = ScenarioComparisonEngine()

    res_a = await engine.evaluate_decision(
        scenario_text="方案A：新建地铁站",
        target_area_text="杭州市余杭区",
    )
    res_b = await engine.evaluate_decision(
        scenario_text="方案B：新建中央公园",
        target_area_text="杭州市余杭区",
    )
    res_c = await engine.evaluate_decision(
        scenario_text="方案C：新建实验小学",
        target_area_text="杭州市余杭区",
    )

    cmp_result = await cmp_engine.compare_scenarios(
        results=[res_a, res_b, res_c]
    )

    assert isinstance(cmp_result, ScenarioComparisonResult)
    assert len(cmp_result.scenarios) == 3
    assert len(cmp_result.metric_matrix) > 0
    assert len(cmp_result.affected_area_comparison) == 3
    assert cmp_result.recommended_scenario_id in [res_a.scenario.scenario_id, res_b.scenario.scenario_id, res_c.scenario.scenario_id]
    assert cmp_result.recommendation_rationale != ""
    assert cmp_result.comparison_geojson["type"] == "FeatureCollection"
    assert len(cmp_result.comparison_geojson["features"]) > 0


@pytest.mark.asyncio
async def test_unresolvable_target_area_returns_correction_hint(engine):
    """Verify that unresolvable target area returns structured correction hint instead of Beijing fallback."""
    
    result = await engine.evaluate_decision(
        scenario_text="新建地铁站",
        target_area_text="未知不存在的虚幻地区xyz123456",
    )

    assert result.target_area.confidence == 0.0
    assert result.target_area.correction_hint is not None
    assert "无法解析" in result.target_area.correction_hint or "建议" in result.target_area.correction_hint
