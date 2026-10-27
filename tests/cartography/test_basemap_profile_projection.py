"""G3D01 投影契约：再生成幂等 + 前后端目录一致性锚点。"""
from __future__ import annotations

from app.core import base_layers
from app.lib.cartography import basemap_profile_projection
from app.lib.cartography.basemap_profiles import (
    BASEMAP_PROVIDER_PROFILES,
    ELEVATION_FALLBACK_ID,
    get_basemap_profile,
    iter_llm_catalog,
)


class TestProjectionIdempotency:
    def test_regenerated_file_matches_committed(self):
        verdict = basemap_profile_projection.check_drift()
        assert not verdict["drifted"], (
            f"basemap-profiles.generated.ts 漂移：{verdict['reason']} —— "
            "请运行 python -m app.lib.cartography.basemap_profile_projection"
        )

    def test_output_file_exists_in_frontend(self):
        assert basemap_profile_projection.OUTPUT.exists()

    def test_generated_ts_carries_every_profile_name_and_attribution(self):
        """跨语言锚点：生成物必须逐条携带 name/attribution（完整锁在
        vitest basemap-profiles.contract.test.ts —— 双侧 import 直比）。"""
        content = basemap_profile_projection.OUTPUT.read_text(encoding="utf-8")
        for p in BASEMAP_PROVIDER_PROFILES:
            assert p.name in content, p.provider_id
            assert p.attribution in content, p.provider_id
        fb = get_basemap_profile("osm")
        assert fb is not None and fb.attribution in content
        assert ELEVATION_FALLBACK_ID in content


class TestBaseLayersDerivation:
    """base_layers（LLM 感知端）必须逐条派生自 profiles —— 手工双真相终结。"""

    def test_catalog_equals_profile_derivation(self):
        assert base_layers.BASE_LAYER_CATALOG == iter_llm_catalog()

    def test_providers_by_id_covers_all_llm_visible_profiles(self):
        by_id = base_layers._providers_by_id()
        for p in BASEMAP_PROVIDER_PROFILES:
            assert by_id.get(p.provider_id) == p.name, p.provider_id

    def test_llm_catalog_names_unique(self):
        names = [b["name"] for b in base_layers.BASE_LAYER_CATALOG]
        assert len(names) == len(set(names))

    def test_openfreemap_reachable_via_resolver(self):
        assert base_layers.resolve_provider_id_to_name("openfreemap-libre") == "OpenFreeMap Libre"
        assert base_layers.resolve_provider_id_to_name("openfreemap-positron") == "OpenFreeMap Positron"
        assert base_layers.resolve_provider_id_to_name("openfreemap-bright") == "OpenFreeMap Bright"
