"""ads-v1 acquisition-limits tests (DS0, ADR-0170): policy behaviour + grep-zero gate.

The second half is the A7 hard gate: the seven formerly-scattered threshold
literals must only exist in ``acquisition_limits`` — every consumer imports
the single point.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.core.config import settings
from app.services.data_fabric import acquisition_limits as AL

REPO = Path(__file__).resolve().parents[2]

# The seven A7 sites (file → import assertion token).
A7_SITES = {
    "app/services/mapspec_source.py": "acquisition_limits",
    "app/services/data_fabric/adapters/postgis_adapter.py": "acquisition_limits",
    "app/api/routes/data_quality.py": "acquisition_limits",
    "app/services/data_profile/unified.py": "acquisition_limits",
    "app/services/mapspec/composite_builder.py": "acquisition_limits",
    "app/services/mapspec/lifecycle_engine.py": "acquisition_limits",
    "app/services/publication_export.py": "acquisition_limits",
}


def test_surface_constants_unchanged_from_former_literals():
    """收敛 ≠ 改行为：单点数值必须与原七处字面量一致（DS8 校准前冻结）。"""
    assert AL.INLINE_REF_LIMIT == 5_000
    assert AL.MVT_TILE_FEATURE_LIMIT == 20_000
    assert AL.PROFILE_INLINE_LIMIT == 20_000
    assert AL.PROFILE_SCAN_ROWS_LIMIT == 50_000
    assert AL.MAPSPEC_MAX_FEATURES == 50_000
    assert AL.EXPORT_MAX_FEATURES == 50_000
    assert AL.MAP_QUALITY_GATE_FALLBACK == 5_000
    assert set(AL.SURFACE_LIMITS) == {
        "inline_ref", "mvt_tile", "profile_inline", "profile_scan_rows", "mapspec", "export", "map_quality_gate",
    }


def test_settings_map_quality_gate_default_matches_fallback():
    """lifecycle_engine 的 settings 兜底与 config 默认必须同源同值（防漂移）。"""
    assert AL.MAP_QUALITY_GATE_FALLBACK == settings.MAP_QUALITY_GATE_MAX_FEATURES


def test_effective_feature_limit_no_evidence_is_identity():
    assert AL.effective_feature_limit(50_000) == 50_000
    assert AL.effective_feature_limit(50_000, avg_vertices=None, viewport_features=None) == 50_000
    assert AL.effective_feature_limit(50_000, avg_vertices=0) == 50_000


def test_effective_feature_limit_complexity_scales_down():
    # 2× 平均顶点数 → 上限减半；轻几何不放大
    assert AL.effective_feature_limit(50_000, avg_vertices=40.0) == 25_000
    assert AL.effective_feature_limit(50_000, avg_vertices=20.0) == 50_000
    assert AL.effective_feature_limit(50_000, avg_vertices=5.0) == 50_000


def test_effective_feature_limit_viewport_cap_and_floor():
    # 视口证据 × 1.5 headroom 封顶
    assert AL.effective_feature_limit(50_000, viewport_features=1_000) == 1_500
    # 组合取更严者：min(50000/2=25000, 2000×1.5=3000) = 3000
    assert AL.effective_feature_limit(50_000, avg_vertices=40.0, viewport_features=2_000) == 3_000
    # 不低于下限
    assert AL.effective_feature_limit(50_000, viewport_features=1) == AL._MIN_EFFECTIVE_LIMIT
    # 不高于 base
    assert AL.effective_feature_limit(5_000, viewport_features=100_000) == 5_000


def test_effective_feature_limit_deterministic():
    args = {"avg_vertices": 33.3, "viewport_features": 777}
    assert AL.effective_feature_limit(20_000, **args) == AL.effective_feature_limit(20_000, **args)


def test_a7_sites_import_single_point_and_no_literal_left():
    for rel, token in A7_SITES.items():
        src = (REPO / rel).read_text(encoding="utf-8")
        assert token in src, f"{rel} no longer imports the single point ({token})"
    # 旧字面量残留在七处消费点（赋值/字典字面量/return）= 红线
    forbidden_patterns = [
        r"INLINE_FEATURE_LIMIT\s*=\s*5000\b",
        r"MVT_MAX_FEATURES_PER_TILE\s*=\s*20_?000\b",
        r"_MAX_INLINE_FEATURES\s*=\s*20000\b",
        r"maxFeatures\":\s*50000\b",
        r"return\s+50000\b",
        r",\s*50000\)",
        r"MAP_QUALITY_GATE_MAX_FEATURES[^\n]*,\s*5000\b",
    ]
    for rel in A7_SITES:
        src = (REPO / rel).read_text(encoding="utf-8")
        for pattern in forbidden_patterns:
            hit = re.search(pattern, src)
            assert hit is None, f"scattered threshold literal survived in {rel}: {pattern}"


def test_no_maxfeatures_numeric_literal_anywhere_in_app():
    """全仓断言：thresholds.maxFeatures 只允许出现常量引用，不允许数字字面量。"""
    offenders = []
    for py in (REPO / "app").rglob("*.py"):
        src = py.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r'"maxFeatures"\s*:\s*(\d+)', src):
            offenders.append(f"{py.relative_to(REPO)}:{src[:m.start()].count(chr(10)) + 1} → {m.group(0)}")
    assert offenders == [], "numeric maxFeatures literals remain:\n" + "\n".join(offenders)
