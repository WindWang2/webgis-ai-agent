"""地图模型库（model_library）完整性 + 扩充分级方法（std_dev/head_tail）。

模型库知识来源（网络调研，2026-08）：
- MapLibre Style Spec 图层枚举: https://maplibre.org/maplibre-style-spec/layers/
- QGIS graduated 分类方法清单: https://docs.qgis.org/latest/en/docs/user_manual/working_with_vector/vector_properties.html
- GeoDa 分类地图体系: https://geodacenter.github.io/workbook/3a_mapping/lab3a.html
- deck.gl / kepler.gl 图层目录: 见 model_library 模块 docstring
"""
import numpy as np
import pytest

from app.lib.cartography.model_library import (
    CLASSIFICATION_METHODS,
    MAPLIBRE_LAYER_TYPES,
    PALETTE_KINDS,
    get_map_model,
    get_map_model_registry,
    palettes_for_kind,
    validate_model_library,
)
from app.lib.cartography.palettes import COLOR_PALETTES, resolve_palette_colors
from app.services.cartography_service import CartographyService


class TestModelLibraryIntegrity:
    def test_validation_passes(self):
        """跨引用完整性：maplibre 枚举 / palette / classifier 引用全存在。"""
        assert validate_model_library() == []

    def test_palette_kinds_cover_all_palettes(self):
        assert set(PALETTE_KINDS) >= set(COLOR_PALETTES)

    def test_alias_resolution(self):
        # 旧词汇别名 → 规范模型 id
        assert get_map_model("choropleth").id == "administrative_choropleth"
        assert get_map_model("hexbin").id == "aggregate_grid"
        assert get_map_model("bubble_map").id == "proportional_symbol"
        assert get_map_model("density_overview").id == "visual_heatmap"

    def test_planned_models_are_honest(self):
        reg = get_map_model_registry()
        planned = set(reg.planned_ids())
        # ADR-0092 Phase D: flow_od_arc promoted to native (od_flow_edges
        # tool + converter flow paint + frontend line channels).
        assert "flow_od_arc" in set(reg.native_ids())
        assert {"extrusion_3d", "isoline_contour"} <= planned
        for mid in planned:
            assert get_map_model(mid).runtime_status == "planned"

    def test_all_maplibre_types_within_style_spec_enum(self):
        for model in get_map_model_registry()._by_id.values():
            assert model.maplibre_layer_type in MAPLIBRE_LAYER_TYPES

    def test_qualitative_palette_family(self):
        qual = palettes_for_kind("qualitative")
        assert {"Set1", "Set2", "Dark2", "Pastel1"} <= set(qual)
        diverging = palettes_for_kind("diverging")
        assert {"RdYlGn", "RdBu"} <= set(diverging)


class TestPaletteExpansion:
    """ColorBrewer 官方 hex（axismaps/colorbrewer 数据集逐色核对）。"""

    def test_new_colorbrewer_palettes_present(self):
        for pid in ("Oranges", "Purples", "RdYlGn", "RdBu",
                    "Set1", "Set2", "Dark2", "Pastel1"):
            assert pid in COLOR_PALETTES
            colors = resolve_palette_colors(pid)
            assert colors == COLOR_PALETTES[pid]
            assert all(c.startswith("#") for c in colors)

    def test_perceptual_uniform_family(self):
        for pid in ("Inferno", "Plasma"):
            assert len(COLOR_PALETTES[pid]) >= 5

    @pytest.mark.parametrize(
        "pid,head", [
            ("Set1", "#e41a1c"),
            ("RdBu", "#ca0020"),
            ("Oranges", "#feedde"),
        ],
    )
    def test_authoritative_hexes(self, pid, head):
        assert COLOR_PALETTES[pid][0] == head


class TestStdDevClassification:
    def test_symmetric_data_centers_on_mean(self):
        rng = np.random.default_rng(7)
        vals = (rng.normal(100, 10, 200)).tolist()
        breaks = CartographyService.classify(vals, method="std_dev", k=6)
        mu, sd = float(np.mean(vals)), float(np.std(vals))
        assert breaks[0] == pytest.approx(min(vals))
        assert breaks[-1] == pytest.approx(max(vals))
        # 至少含 mean ± 0.5 SD 的内断点（0.5 SD 步进）
        inner = breaks[1:-1]
        assert any(abs(b - (mu - 0.5 * sd)) < 1e-6 for b in inner)
        assert any(abs(b - (mu + 0.5 * sd)) < 1e-6 for b in inner)
        # 断点单调不减、去重
        assert breaks == sorted(set(breaks))

    def test_constant_field_degrades_to_endpoints(self):
        breaks = CartographyService.classify([5.0] * 30, method="std_dev", k=5)
        assert breaks == [5.0, 5.0]

    def test_out_of_range_breaks_clipped(self):
        vals = [0.0, 1.0, 2.0, 3.0, 4.0]
        breaks = CartographyService.classify(vals, method="std_dev", k=8)
        assert breaks[0] == 0.0 and breaks[-1] == 4.0


class TestHeadTailClassification:
    def test_heavy_tail_yields_multiple_classes(self):
        """重尾数据（城市计数典型形态）：头尾法应产出接近 k 的类数。"""
        rng = np.random.default_rng(11)
        vals = [float(np.round(1 + rng.exponential(50))) for _ in range(300)]
        vals += [5000.0, 8000.0, 12000.0]   # 重尾
        breaks = CartographyService.classify(vals, method="head_tail", k=5)
        inner = [b for b in breaks if min(vals) < b < max(vals)]
        assert len(inner) >= 3
        assert breaks == sorted(set(breaks))

    def test_class_count_capped_by_k(self):
        vals = [float(x) for x in range(1, 500)]
        breaks = CartographyService.classify(vals, method="head_tail", k=4)
        # 类数 ≤ k（断点总数 ≤ k+1 含两端）
        assert len(breaks) <= 5

    def test_plateau_data_stops_early_by_design(self):
        """重复值平台期无进展即停：类数由数据形态决定，而非硬凑 k。"""
        vals = [1.0] * 95 + [1000.0] * 4 + [50000.0]
        breaks = CartographyService.classify(vals, method="head_tail", k=5)
        # 第一次断裂后头全是等值 1.0 → 均值无切分进展，立即停止
        assert breaks == [1.0, breaks[1], 50000.0]
        assert len(breaks) == 3

    def test_constant_field_returns_endpoints(self):
        breaks = CartographyService.classify([7.0] * 20, method="head_tail", k=5)
        assert breaks == [7.0, 7.0]

    def test_unknown_method_falls_back_to_equal_interval(self):
        vals = [1.0, 4.0]
        got = CartographyService.classify(vals, method="mystery", k=2)
        # equal_interval（k=2，min→max）的唯一内断点正中；不应是 4-class 的等距
        assert got == [1.0, 2.5, 4.0]
        assert got != [1.0, 2.0, 3.0, 4.0]


class TestClassifierMetadataContract:
    def test_engine_supports_every_registered_classifier(self):
        """登记在册的分级方法必须真实可执行（元数据 ≠ 死目录）。"""
        supported = {"quantiles", "equal_interval", "natural_breaks",
                     "std_dev", "head_tail"}
        registered = {cid for cid, m in CLASSIFICATION_METHODS.items()}
        assert registered == supported
