"""Foundation V2 基础设施 conformance 测试。

覆盖（ADR-0099 契约体系的共享层扩展）：

- ``backend_selection.select_backend``：确定性变体选择（窗口命中 /
  窗口外降级 / n 未知延迟 / 无变体默认路径 / 未知算法拒绝 / 理由有界 /
  diagnostics 形状）；
- ``BackendVariant`` 规模窗口字段的模型校验（min > max 拒绝）；
- ``binary_field_required`` 前置条件的五值判定语义；
- Foundation V2 新增 method references 存在性（描述符引用前的白名单保障）；
- 算法目录 parity：``scripts/gen_science_catalog.py`` 的生成结果必须与
  提交的 ``docs/science/ALGORITHM_CATALOG.md`` 字节一致（杜绝手编辑漂移）。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

from app.lib.gis.algorithm_registry import BackendVariant, get_algorithm_registry
from app.lib.gis.backend_selection import ScaleProfile, select_backend
from app.lib.gis.method_references import reference_exists
from app.lib.gis.scientific_preconditions import evaluate_precondition


# ── BackendVariant 规模窗口校验 ──────────────────────────────────────
class TestBackendVariantScaleWindow:
    def test_min_greater_than_max_rejected(self):
        with pytest.raises(ValueError):
            BackendVariant(id="bad", backend="numpy", min_features=100, max_features=10)

    def test_negative_min_rejected(self):
        with pytest.raises(ValueError):
            BackendVariant(id="bad", backend="numpy", min_features=-1)

    def test_open_window_defaults_ok(self):
        v = BackendVariant(id="legacy", backend="numpy")
        assert v.min_features is None and v.max_features is None


# ── select_backend 决策规则 ──────────────────────────────────────────
def _register_tmp_algorithm(monkeypatch, variants):
    reg = get_algorithm_registry()
    existing = reg.get("stats.morans_i")
    assert existing is not None, "stats.morans_i 必须内建存在"
    # 用已有算法 id 挂临时变体，测试后恢复，避免污染全局单例
    monkeypatch.setattr(existing, "backend_variants", variants, raising=True)
    return existing


class TestSelectBackend:
    def test_unknown_algorithm_rejected(self):
        with pytest.raises(KeyError):
            select_backend("nonexistent.algorithm", ScaleProfile(feature_count=10))

    def test_no_variants_default_tool_path(self):
        reg = get_algorithm_registry()
        algo_id = next(a for a in reg.all_ids if not reg.get(a).backend_variants)
        d = select_backend(algo_id, ScaleProfile(feature_count=100))
        assert d.variant_id == "" and d.backend == ""
        assert d.matched is False
        assert "no_variants_declared" in d.rationale

    def test_window_hit_prefers_declaration_order(self, monkeypatch):
        variants = [
            BackendVariant(id="small", backend="numpy", max_features=1000),
            BackendVariant(id="large", backend="scipy", min_features=1001),
        ]
        _register_tmp_algorithm(monkeypatch, variants)
        d = select_backend("stats.morans_i", ScaleProfile(feature_count=500))
        assert d.variant_id == "small" and d.matched is True
        d2 = select_backend("stats.morans_i", ScaleProfile(feature_count=5000))
        assert d2.variant_id == "large" and d2.matched is True

    def test_gap_falls_back_to_unbounded_then_first(self, monkeypatch):
        variants = [
            BackendVariant(id="tiny", backend="numpy", max_features=10),
            BackendVariant(id="mid", backend="scipy", min_features=20, max_features=30),
        ]
        _register_tmp_algorithm(monkeypatch, variants)
        # n=15 落在 10..20 的洞里 → 无界上界优先（本例没有）→ 声明序第一个
        d = select_backend("stats.morans_i", ScaleProfile(feature_count=15))
        assert d.variant_id == "tiny"
        assert d.matched is False
        assert "降级" in d.rationale or "不在任何变体窗口" in d.rationale

    def test_unknown_n_defers_to_declaration_order(self, monkeypatch):
        variants = [
            BackendVariant(id="a", backend="numpy", max_features=10),
            BackendVariant(id="b", backend="scipy"),
        ]
        _register_tmp_algorithm(monkeypatch, variants)
        d = select_backend("stats.morans_i", ScaleProfile(feature_count=None))
        assert d.variant_id == "a" and d.matched is False
        assert "scale_unknown" in d.rationale

    def test_deterministic_and_bounded_rationale(self, monkeypatch):
        variants = [
            BackendVariant(id="x", backend="numpy", max_features=100,
                           notes="n" * 300),
        ]
        _register_tmp_algorithm(monkeypatch, variants)
        d1 = select_backend("stats.morans_i", ScaleProfile(feature_count=50))
        d2 = select_backend("stats.morans_i", ScaleProfile(feature_count=50))
        assert d1 == d2
        assert len(d1.rationale) <= 160

    def test_kriging_declared_variants_are_selectable(self):
        # 真实内建：interpolation.kriging 声明 numpy_batched/scipy_linalg
        d = select_backend("interpolation.kriging", ScaleProfile(feature_count=500))
        assert d.variant_id == "numpy_batched"  # 声明序即偏好序
        assert d.backend == "numpy"
        assert d.scale_tier == "interactive"
        assert d.execution_policy in ("interactive_fast", "analysis_quality")

    def test_diagnostic_shape(self):
        d = select_backend("interpolation.kriging", ScaleProfile(feature_count=500))
        diag = d.to_diagnostic()
        assert set(("name", "value", "text")) <= set(diag.keys())
        assert diag["name"] == "backend_selection"
        assert diag["value"] == "numpy_batched"
        assert len(diag["text"]) <= 160

    def test_runtime_strategy_recommends_server_channel_for_heavy(self):
        d = select_backend("interpolation.kriging", ScaleProfile(feature_count=60_000))
        # 超过前端渲染上限 → 不再是前端内联通道；kriging 输出 raster_surface
        # → 服务端栅格通道（cost_model.resolve_runtime_strategy 语义）
        assert d.runtime_strategy == "server_raster"
        assert d.execution_policy == "large_data"
        assert d.scale_tier == "server_side"


# ── binary_field_required 前置条件 ───────────────────────────────────
class TestBinaryFieldPrecondition:
    def test_fields_unknown_defers_pass(self):
        r = evaluate_precondition("binary_field_required", {})
        assert r.verdict == "PASS"

    def test_known_fields_without_binary_rejected(self):
        r = evaluate_precondition("binary_field_required", {
            "fields": {"income": {"type": "number"}},
        })
        assert r.verdict == "INSUFFICIENT_DATA"
        assert r.transform_hint

    def test_binary_field_passes(self):
        r = evaluate_precondition("binary_field_required", {
            "fields": {"landuse_code": {"type": "number"}},
            "binaryFields": ["landuse_code"],
        })
        assert r.verdict == "PASS"


# ── Foundation V2 method references 白名单 ───────────────────────────
@pytest.mark.parametrize("ref_id", [
    "cliff_ord1973", "wartenberg1985", "wang2010", "anselin1988", "ord1975",
    "brunsdon1996", "fotheringham2002", "jarque_bera1980", "breusch_pagan1979",
    "holm1979", "diggle1983", "van_lieshout_baddeley1996", "illian2008",
    "besag1977", "knox1964", "matern1986", "webster_oliver2007", "odeh1995",
    "watson1981", "clough_tocher1966", "hakimi1964", "church_revelle1974",
    "huff1964", "zipf1946", "hansen1959", "brandes2001", "barnes2014",
    "strahler1957", "beven_kirkby1979", "wischmeier_smith1978",
    "desmet_govers1996", "yokoyama2002", "jasiewicz_stepinski2013",
    "lee1980", "lee1981", "lopes1990", "frost1982", "oliver_quegan1998",
    "haralick1973", "crist_cicone1984", "baig2014", "shi_xu2019",
])
def test_new_method_references_registered(ref_id):
    assert reference_exists(ref_id), f"Foundation V2 引用缺失: {ref_id}"


# ── 算法目录 parity（生成器 == 提交文档）────────────────────────────
def test_catalog_doc_matches_registry_projection():
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(root / "scripts"))
    try:
        from gen_science_catalog import generate
    finally:
        sys.path.pop(0)
    committed = (root / "docs" / "science" / "ALGORITHM_CATALOG.md").read_text(
        encoding="utf-8")
    assert generate() == committed, (
        "ALGORITHM_CATALOG.md 与注册表投影不一致 —— 请运行 "
        "`python scripts/gen_science_catalog.py` 再提交")


# ── benchmark manifest parity（Wave 10 · science-v3）────────────────
def test_benchmark_manifest_matches_registry_projection():
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(root / "scripts"))
    try:
        from gen_science_benchmark_manifest import generate
    finally:
        sys.path.pop(0)
    committed = (root / "docs" / "science" / "BENCHMARK_MANIFEST.md").read_text(
        encoding="utf-8")
    assert generate() == committed, (
        "BENCHMARK_MANIFEST.md 与注册表投影不一致 —— 请运行 "
        "`python scripts/gen_science_benchmark_manifest.py` 再提交")
