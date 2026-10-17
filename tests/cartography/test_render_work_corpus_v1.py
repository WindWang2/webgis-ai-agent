"""RenderWork corpus 三重锁测试（Python 侧，Goal 03 ADR-0224）。

锁 1：contract_manifest.json ≡ 后端 consts（单一权威）；
锁 2：render_work_golden.json 的 envelope ≡ 真实 projector 输出
     （重放确定性 —— golden 由 generate.py 用真实实现生成，手改即爆）；
锁 3：场景级语义断言（style-only / data-change / #1617 透传 / 幂等重放）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.lib.cartography import renderer_capability as rc
from app.lib.cartography.render_apply_ack import (
    ACK_SCHEMA_VERSION,
    ACK_STATUSES,
    ENTRY_STATUSES,
    MAX_ACK_COMPONENTS,
    MAX_ACK_LAYERS,
    MAX_ACK_SOURCES,
    REASON_CODES,
)
from app.lib.cartography.render_perf_probes import PERF_SCHEMA_VERSION
from app.lib.cartography.render_work_envelope import (
    MAX_COMPONENT_WORK,
    MAX_DATA_DEPENDENCIES,
    MAX_DIAGNOSTICS,
    MAX_LAYER_WORK,
    RENDER_WORK_SCHEMA_VERSION,
    _SOURCE_KINDS,
    project_render_work_envelope,
)

CORPUS_DIR = Path(__file__).resolve().parent / "render_work_corpus"
MANIFEST_PATH = CORPUS_DIR / "contract_manifest.json"
GOLDEN_PATH = CORPUS_DIR / "render_work_golden.json"


@pytest.fixture(scope="module")
def manifest():
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def golden():
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


class TestContractManifestParity:
    """锁 1：JSON ≡ Python consts（TS 侧由 contract.parity.golden.test.ts 锁）。"""

    def test_render_work_block(self, manifest):
        block = manifest["render_work"]
        assert block["schema_version"] == RENDER_WORK_SCHEMA_VERSION
        assert block["max_data_dependencies"] == MAX_DATA_DEPENDENCIES
        assert block["max_layer_work"] == MAX_LAYER_WORK
        assert block["max_component_work"] == MAX_COMPONENT_WORK
        assert block["max_diagnostics"] == MAX_DIAGNOSTICS
        assert block["source_kinds"] == sorted(_SOURCE_KINDS)

    def test_apply_ack_block(self, manifest):
        block = manifest["apply_ack"]
        assert block["schema_version"] == ACK_SCHEMA_VERSION
        assert block["statuses"] == list(ACK_STATUSES)
        assert block["entry_statuses"] == list(ENTRY_STATUSES)
        assert block["reason_codes"] == sorted(REASON_CODES)
        assert block["max_layers"] == MAX_ACK_LAYERS
        assert block["max_components"] == MAX_ACK_COMPONENTS
        assert block["max_sources"] == MAX_ACK_SOURCES

    def test_perf_and_capability_blocks(self, manifest):
        assert manifest["perf"]["schema_version"] == PERF_SCHEMA_VERSION
        cap = manifest["capability"]
        assert cap["schema_version"] == rc.CAPABILITY_SCHEMA_VERSION
        assert cap["source_type_levels"] == {
            k: v["level"] for k, v in sorted(rc.SOURCE_TYPE_SUPPORT.items())
        }
        assert cap["feature_support"] == {
            k: bool(v["supported"]) for k, v in sorted(rc.FEATURE_SUPPORT.items())
        }


class TestGoldenReplay:
    """锁 2：golden envelope ≡ 真实 projector 重放。"""

    def test_all_scenarios_replay(self, golden):
        scenarios = golden["scenarios"]
        assert len(scenarios) == 12
        for name, sc in scenarios.items():
            inp = {k: v for k, v in sc["projector_input"].items() if k != "previous"}
            previous = None
            prev_name = sc["projector_input"].get("previous")
            if prev_name:
                prev_inp = {
                    k: v for k, v in scenarios[prev_name]["projector_input"].items()
                    if k not in ("previous",)
                }
                previous = project_render_work_envelope(**prev_inp)
            envelope = project_render_work_envelope(previous=previous, **inp)
            assert envelope.to_dict() == sc["envelope"], f"golden drift: {name}"

    def test_idempotent_replay_identical(self, golden):
        scenarios = golden["scenarios"]
        a = scenarios["s01_ten_layer_full_apply"]["envelope"]
        b = scenarios["s11_reconnect_idempotent_replay"]["envelope"]
        assert a == b
        assert a["work_id"] == b["work_id"]
        assert a["work_fingerprint"] == b["work_fingerprint"]


class TestScenarioSemantics:
    """锁 3：场景级语义（Goal J 12 场景的关键断言）。"""

    def test_s03_style_only(self, golden):
        inv = golden["scenarios"]["s03_style_only_patch_preserves_data"]["envelope"]["invalidation"]
        assert inv["style_only"] is True
        assert inv["data_changed"] == []

    def test_s04_data_revision_changed(self, golden):
        env = golden["scenarios"]["s04_same_ref_revision_changed"]["envelope"]
        assert env["invalidation"]["style_only"] is False
        assert env["invalidation"]["data_changed"] == ["src-main"]
        assert [d["revision"] for d in env["data_dependencies"]] == ["4"]

    def test_s08_representation_passthrough_no_restatement(self, golden):
        """#1617 前向兼容：信封不复述 representation（不重裁决）。"""
        blob = json.dumps(
            golden["scenarios"]["s08_representation_passthrough"]["envelope"]
        )
        assert "representation" not in blob
        assert "point_cluster" not in blob

    def test_s09_mvt_and_dem_deps(self, golden):
        deps = {
            d["source_id"]: d for d in
            golden["scenarios"]["s09_mvt_vector_source"]["envelope"]["data_dependencies"]
        }
        assert deps["tiles-x"]["kind"] == "vector"
        assert "revision" not in deps["tiles-x"]  # MVT URL 源无内容代次
        assert deps["dem-y"]["kind"] == "raster-dem"

    def test_s10_fabric_ref_identity(self, golden):
        dep = golden["scenarios"]["s10_data_fabric_ref_dependency"]["envelope"]["data_dependencies"][0]
        assert dep["kind"] == "data_fabric"
        assert dep["ref"] == "ref-abc-123"
        assert dep["revision"] == "9"

    def test_s12_layer_removal(self, golden):
        inv = golden["scenarios"]["s12_layer_remove_and_invalidation"]["envelope"]["invalidation"]
        assert len(inv["layers_removed"]) == 5
        assert inv["layers_added"] == []

    def test_required_capabilities_declared(self, golden):
        caps = set(
            golden["scenarios"]["s08_representation_passthrough"]["envelope"]["required_capabilities"]
        )
        assert {"circle", "geojson"} <= caps
        # #1617 未合并：capability 面不声明 representation 为 supported ——
        # 信封的 required_capabilities 也不得出现其专属键。
        assert "semantic_zoom_representation" not in caps


if __name__ == "__main__":
    pytest.main([__file__])
