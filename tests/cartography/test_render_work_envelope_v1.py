"""RenderWorkEnvelope V1 契约测试（Goal 03，ADR-0225）。

锁定：确定性（同输入同 work_id/fingerprint）、零载荷纪律（信封序列化
不含 GeoJSON/样式体）、有界截断披露、invalidation hints 语义
（style-only / data-changed / 层增删）、work_id 形状。
"""
from __future__ import annotations

import json

import pytest

from app.lib.cartography.render_work_envelope import (
    MAX_COMPONENT_WORK,
    MAX_DATA_DEPENDENCIES,
    MAX_DIAGNOSTICS,
    MAX_LAYER_WORK,
    RENDER_WORK_SCHEMA_VERSION,
    project_render_work_envelope,
)

pytestmark = pytest.mark.cartography


def _spec(layers=None, sources=None, components=None):
    return {
        "version": "1.5",
        "sources": sources or {},
        "layers": layers or [],
        "layout": {"components": components or []},
    }


def _project(mapspec, **kw):
    kw.setdefault("session_id", "sess-T")
    kw.setdefault("revision", 3)
    kw.setdefault("fingerprint", "fp-abc123def456")
    kw.setdefault("observation_seq", 9)
    return project_render_work_envelope(mapspec, **kw)


class TestDeterminism:
    def test_same_inputs_same_identity(self):
        spec = _spec(
            [{"id": "l1", "type": "circle", "source": "s1"}],
            {"s1": {"type": "geojson", "content_revision": 2}},
        )
        e1 = _project(spec)
        e2 = _project(spec)
        assert e1 == e2
        assert e1.work_id == e2.work_id == "rw-3-fp-abc123def"
        assert e1.work_fingerprint == e2.work_fingerprint

    def test_different_session_different_fingerprint(self):
        spec = _spec([{"id": "l1", "type": "circle", "source": ""}])
        e1 = _project(spec, session_id="sess-A")
        e2 = _project(spec, session_id="sess-B")
        assert e1.work_fingerprint != e2.work_fingerprint

    def test_revision_change_changes_work_id(self):
        spec = _spec([{"id": "l1", "type": "fill", "source": ""}])
        assert (
            _project(spec, revision=3).work_id
            != _project(spec, revision=4).work_id
        )

    def test_source_dict_order_irrelevant(self):
        s1 = _project(_spec(
            layers=[{"id": "a", "type": "fill", "source": "x"}],
            sources={"x": {"type": "geojson", "content_revision": 1},
                     "y": {"type": "vector", "tiles": ["t"]}},
        ))
        s2 = _project(_spec(
            layers=[{"id": "a", "type": "fill", "source": "x"}],
            sources={"y": {"type": "vector", "tiles": ["t"]},
                     "x": {"type": "geojson", "content_revision": 1}},
        ))
        assert s1.work_fingerprint == s2.work_fingerprint
        assert [d.source_id for d in s1.data_dependencies] == ["x", "y"]


class TestZeroPayloadDiscipline:
    def test_no_geometry_or_style_in_serialized_envelope(self):
        spec = _spec(
            [{"id": "l1", "type": "circle", "source": "big",
              "paint": {"circle-color": "#ff0000"}}],
            {"big": {"type": "geojson", "inlineData": {
                "type": "FeatureCollection",
                "features": [{"type": "Feature", "geometry": None}] * 100,
            }, "content_revision": 1}},
        )
        blob = json.dumps(_project(spec).to_dict())
        assert "features" not in blob
        assert "inlineData" not in blob
        assert "circle-color" not in blob
        # payload 只有 id/类型/revision/fingerprint 的事实。
        dep = _project(spec).data_dependencies[0]
        assert dep.source_id == "big" and dep.revision == "1"


class TestBounds:
    def test_layer_truncation_disclosed(self):
        layers = [
            {"id": f"l{i}", "type": "fill", "source": ""} for i in range(MAX_LAYER_WORK + 5)
        ]
        env = _project(_spec(layers))
        assert len(env.layer_work) == MAX_LAYER_WORK
        assert any("truncated" in d for d in env.diagnostics)

    def test_deps_truncation_disclosed(self):
        sources = {
            f"s{i}": {"type": "geojson", "content_revision": i}
            for i in range(MAX_DATA_DEPENDENCIES + 3)
        }
        env = _project(_spec([{"id": "l", "type": "fill", "source": "s0"}], sources))
        assert len(env.data_dependencies) == MAX_DATA_DEPENDENCIES
        assert any("truncated" in d for d in env.diagnostics)
        assert len(env.diagnostics) <= MAX_DIAGNOSTICS

    def test_component_truncation(self):
        comps = [{"id": f"c{i}", "type": "legend"} for i in range(MAX_COMPONENT_WORK + 2)]
        env = _project(_spec(components=comps))
        assert len(env.component_work) == MAX_COMPONENT_WORK


class TestIdentityDiscrimination:
    """Review P0-1/P1-1 回归：真实指纹格式（carto-sha256:<64hex>）下
    work_id 必须切**哈希段**，指纹全长保留。"""

    def test_same_revision_different_content_different_work_id(self):
        spec_a = _spec([{"id": "x", "type": "fill", "source": ""}])
        spec_b = _spec([{"id": "y", "type": "fill", "source": ""}])
        e1 = _project(spec_a, revision=5, fingerprint="carto-sha256:" + "a" * 64)
        e2 = _project(spec_b, revision=5, fingerprint="carto-sha256:" + "b" * 64)
        assert e1.work_id != e2.work_id
        assert e1.work_id == "rw-5-" + "a" * 12
        assert e2.work_id == "rw-5-" + "b" * 12

    def test_fingerprint_full_length_preserved(self):
        fp = "carto-sha256:" + "9" * 64
        env = _project(_spec([{"id": "l", "type": "fill", "source": ""}]),
                       fingerprint=fp)
        assert env.to_dict()["mapspec_fingerprint"] == fp

    def test_seq_not_in_work_fingerprint(self):
        """seq 跨通道漂移（SSE seq=N vs 恢复面 seq=N+1）不得改变身份。"""
        spec = _spec([{"id": "l", "type": "fill", "source": ""}])
        e1 = _project(spec, observation_seq=7)
        e2 = _project(spec, observation_seq=99)
        assert e1.work_fingerprint == e2.work_fingerprint
        assert e1.observation_target_seq != e2.observation_target_seq


class TestInvalidationHints:
    def _base(self):
        return _spec(
            [{"id": "l1", "type": "circle", "source": "s1"}],
            {"s1": {"type": "geojson", "content_revision": 3}},
        )

    def test_first_envelope_unknown_hints(self):
        env = _project(self._base())
        assert env.invalidation.style_only is None
        assert env.invalidation.data_changed == ()

    def test_style_only_patch(self):
        prev = _project(self._base())
        # 只改 paint（不在信封投影面）→ style-only。
        spec2 = _spec(
            [{"id": "l1", "type": "circle", "source": "s1",
              "paint": {"circle-color": "#00ff00"}}],
            {"s1": {"type": "geojson", "content_revision": 3}},
        )
        env = _project(spec2, revision=4, fingerprint="fp2", previous=prev)
        assert env.invalidation.style_only is True
        assert env.invalidation.data_changed == ()

    def test_data_revision_change_invalidates(self):
        prev = _project(self._base())
        spec2 = _spec(
            [{"id": "l1", "type": "circle", "source": "s1"}],
            {"s1": {"type": "geojson", "content_revision": 4}},
        )
        env = _project(spec2, revision=5, fingerprint="fp3", previous=prev)
        assert env.invalidation.style_only is False
        assert env.invalidation.data_changed == ("s1",)

    def test_layer_added_removed(self):
        prev = _project(self._base())
        spec2 = _spec(
            [
                {"id": "l1", "type": "circle", "source": "s1"},
                {"id": "l2", "type": "fill", "source": "s1"},
            ],
            {"s1": {"type": "geojson", "content_revision": 3}},
        )
        env = _project(spec2, revision=6, fingerprint="fp4", previous=prev)
        assert env.invalidation.layers_added == ("l2",)
        assert env.invalidation.layers_removed == ()
        assert env.invalidation.style_only is False

        env2 = _project(self._base(), revision=7, fingerprint="fp5", previous=env)
        assert env2.invalidation.layers_removed == ("l2",)


class TestIdentityProjection:
    def test_unknown_source_kind_disclosed_not_invented(self):
        env = _project(_spec(
            [{"id": "l", "type": "fill", "source": "s"}],
            {"s": {"type": "quantum-entangled", "content_revision": 1}},
        ))
        assert env.data_dependencies[0].kind == "unknown"

    def test_data_fabric_ref_projection(self):
        env = _project(_spec(
            [{"id": "l", "type": "circle", "source": "f"}],
            {"f": {"type": "data_fabric", "ref_id": "ref-1",
                   "content_revision": 7, "data_fingerprint": "df1"}},
        ))
        dep = env.data_dependencies[0]
        assert dep.kind == "data_fabric"
        assert dep.ref == "ref-1"
        assert dep.revision == "7"
        assert dep.fingerprint == "df1"

    def test_required_capabilities_closed_over_inputs(self):
        env = _project(_spec(
            [{"id": "l", "type": "heatmap", "source": "s"}],
            {"s": {"type": "geojson"}},
            [{"id": "c", "type": "legend"}],
        ))
        assert "heatmap" in env.required_capabilities
        assert "geojson" in env.required_capabilities
        assert "component:legend" in env.required_capabilities

    def test_empty_spec_explicit_envelope(self):
        env = _project(None, fingerprint="")
        assert env.schema_version == RENDER_WORK_SCHEMA_VERSION
        assert env.work_id == "rw-3-empty"
        assert env.layer_work == ()
        assert env.data_dependencies == ()


if __name__ == "__main__":
    pytest.main([__file__])
