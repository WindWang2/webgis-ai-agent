"""W3 数据链路深化测试（V11，ADR-0163）。

覆盖：阈值单点（data_tiers + grep 断言）、19×11 修复实测矩阵（golden 冻结）、
50k 级数据策略阶梯。栅格动态拉伸与增量更新见 test_raster_stretch_payload 与
test_map_source_diff。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytestmark = pytest.mark.cartography

REPO_ROOT = Path(__file__).resolve().parents[2]

from app.lib.cartography.data_tiers import (  # noqa: E402
    TIER_EXPORT_FEATURES,
    TIER_INLINE_FEATURES,
    TIER_SCAN_CAP_FEATURES,
    effective_inline_budget,
    select_data_strategy,
)

# ── W3.1 阈值单点 ────────────────────────────────────────────────────────

#: 业务文件清单 → 禁止再现的三档字面量（**常量赋值形态**；docstring 引用
#: 文案豁免 —— S1 债扫描的 G10 站点）。
FORBIDDEN_TIER_LITERALS = {
    "app/services/mapspec_source.py": ("INLINE_FEATURE_LIMIT = 5000",),
    "app/services/mapspec_to_svg.py": ("DEFAULT_MAX_FEATURES = 50000",),
    "app/lib/cartography/label_plan.py": ("EXTREME_FEATURE_COUNT = 20000",),
    "app/lib/cartography/dot_density.py": ("MAX_TOTAL_DOTS = 20000",),
    "app/api/routes/data_quality.py": ("_MAX_INLINE_FEATURES = 20000",),
    "app/services/spatial_quality_gate.py": ("_MAX_FEATURES_DEFAULT = 5000",),
    "app/lib/gis/cost_model.py": ("DATA_FABRIC_MAX_FEATURES = 50_000",),
}


@pytest.mark.parametrize("rel, patterns", sorted(FORBIDDEN_TIER_LITERALS.items()))
def test_no_tier_literals(rel: str, patterns: tuple) -> None:
    """grep 断言：清单文件内三档字面量归零（W3.1 验收）。"""
    src = (REPO_ROOT / rel).read_text(encoding="utf-8")
    for pat in patterns:
        hits = [n for n, line in enumerate(src.splitlines(), 1) if pat in line]
        assert not hits, f"{rel}: 档位字面量 {pat!r} 回潮于行 {hits}（请 import data_tiers）"


def test_tier_constants_anchored() -> None:
    """既有校准锚点冻结（数值即行为，改动走 ADR）。"""
    assert TIER_INLINE_FEATURES == 5000
    assert TIER_SCAN_CAP_FEATURES == 20000
    assert TIER_EXPORT_FEATURES == 50000


def test_strategy_ladder() -> None:
    """自动选择阶梯（W3.2 决策面）：inline → scan → export → 收紧。"""
    assert select_data_strategy(feature_count=4000).tier == "inline"
    assert select_data_strategy(feature_count=15000).tier == "scan"
    assert select_data_strategy(feature_count=30000).tier == "export"
    over = select_data_strategy(feature_count=120000, geometry_complexity=3.0)
    assert over.tier == "export" and over.max_features < TIER_EXPORT_FEATURES
    plain_over = select_data_strategy(feature_count=120000)
    assert plain_over.max_features == TIER_EXPORT_FEATURES
    # 复杂几何压预算：稠密折线在 inline 预算下被挤出
    complex_ = select_data_strategy(feature_count=4000, geometry_complexity=4.0)
    assert complex_.tier == "scan"
    # 大视口升预算（亚线性，封顶 2×）
    big = effective_inline_budget(viewport_px_w=2560, viewport_px_h=1440)
    assert TIER_INLINE_FEATURES < big <= 2 * TIER_INLINE_FEATURES
    # 确定性
    a = select_data_strategy(feature_count=15000, geometry_complexity=2.0,
                             viewport_px_w=1280, viewport_px_h=720)
    b = select_data_strategy(feature_count=15000, geometry_complexity=2.0,
                             viewport_px_w=1280, viewport_px_h=720)
    assert a == b


# ── W3.4 19×11 修复实测矩阵 ─────────────────────────────────────────────

GOLDEN_MATRIX = REPO_ROOT / "tests/cartography/golden_corpus/repair_matrix/matrix.json"


@pytest.fixture(scope="module")
def golden_matrix() -> dict:
    return json.loads(GOLDEN_MATRIX.read_text(encoding="utf-8"))


def test_repair_matrix_shape(golden_matrix) -> None:
    assert len(golden_matrix["codes"]) == 19
    assert len(golden_matrix["ops"]) == 11
    assert len(golden_matrix["cells"]) == 19 * 11
    assert golden_matrix["summary"]["error"] == 0
    assert golden_matrix["summary"]["unmapped_no_effect"] > 0  # 明示不可修面真实存在


def test_repair_matrix_matches_frozen(golden_matrix) -> None:
    """byte 语义冻结：行为回退/漂移必须显式重生成 golden。"""
    from app.services.spatial_repair_matrix import build_repair_matrix

    assert build_repair_matrix() == golden_matrix


def test_repair_matrix_mapped_effective_sample(golden_matrix) -> None:
    """关键映射实测有效（抽样锁定：EMPTY→remove_empty、DUP→deduplicate、
    SELF_INTERSECTION→make_valid）。"""
    by_pair = {(c["code"], c["op"]): c for c in golden_matrix["cells"]}
    for pair in (
        ("EMPTY_GEOMETRY", "remove_empty"),
        ("DUPLICATE_FEATURE", "deduplicate"),
        ("SELF_INTERSECTION", "make_valid"),
    ):
        assert by_pair[pair]["verdict"] == "mapped_effective", pair


def test_repair_matrix_no_effect_cells_are_findings(golden_matrix) -> None:
    """mapped_no_effect 是如实登记的复核线索（不是隐藏失败）：
    NULL_ISLAND 需破坏性 drop_zero_coordinates；dedup 键 = geom+attrs 的
    语义收窄；crs_transform/flag 类 op 单 op 执行缺计划上下文。"""
    no_effect = {(c["code"], c["op"]) for c in golden_matrix["cells"]
                 if c["verdict"] == "mapped_no_effect"}
    assert ("NULL_ISLAND", "remove_empty") in no_effect
    assert ("DUPLICATE_GEOMETRY", "deduplicate") in no_effect
    assert ("NUMERIC_OUTLIER", "drop_outliers_or_flag") in no_effect


def test_frontend_tier_mirror() -> None:
    """前端镜像常量与权威实现逐值一致（源码扫描；W3.1 评审 finding）。"""
    ts = (REPO_ROOT / "frontend/lib/data-tiers.ts").read_text(encoding="utf-8")
    assert "TIER_INLINE_FEATURES = 5000" in ts
    assert "TIER_SCAN_CAP_FEATURES = 20000" in ts
    assert "TIER_EXPORT_FEATURES = 50000" in ts
