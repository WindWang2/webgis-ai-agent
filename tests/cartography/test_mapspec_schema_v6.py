"""V6（ADR-0120 W2）authoritative MapSpec schema 契约测试。

核心不变式：
1. 脏值兼容 corpus（R1-C2）：document 恒保真（不 coerce），披露结构化；
   publication 语义 == legacy raw-dict 语义（与孪生 _valid()/`is False` 同口径）。
2. canonical golden：dumps(canonicalize(x)) == dumps(x)（保序拷贝合同，R1-M1）。
3. 迁移矩阵：缺 version→1.0 默认；1.0→1.1 identity 升级；forward 拒绝显式。
4. unknown fields：任意深度保留 + 披露（消费方 payload 契约）。
"""
import json

import pytest

from app.lib.cartography.mapspec_schema import (
    DEFAULT_VERSION,
    KNOWN_VERSIONS,
    LATEST_VERSION,
    MAX_SPEC_FRAMES,
    MapSpecSchemaError,
    canonicalize_mapspec,
    dumps_canonical,
    parse_mapspec,
    require_parseable_mapspec,
)

# ── 脏值兼容 corpus（R1-C2 的回归锁）─────────────────────────────────────


def _dirty_spec() -> dict:
    return {
        "version": "1.0",
        "zOrder": "custom-meta-preserved",  # unknown top-level
        "view": {"center": [116.4, 39.9], "zoom": 10},
        "sources": {
            "pts": {"type": "geojson", "inlineData": {"features": []}, "vendorHint": {"any": True}},
        },
        "layers": [
            {
                "id": "pts_fill",
                "source": "pts",
                "type": "circle",
                "paint": {"circle-radius": 5, "circle-color": "#de2d26"},
                "visible": "false",  # 脏值：孪生 `is False` 口径下仍渲染
            },
            {
                "id": "pts_hide",
                "source": "pts",
                "type": "circle",
                "paint": {"circle-radius": 5},
                "layout": {"visibility": "none"},
                "visible": False,  # 合法：隐藏
            },
        ],
        "thresholds": {"maxFeatures": "2000"},  # 脏值：孪生 _valid() 忽略
    }


class TestDirtyValueCorpus:
    def test_document_preserves_dirty_values_verbatim(self):
        result = parse_mapspec(_dirty_spec())
        doc = result.document
        assert doc["layers"][0]["visible"] == "false"  # 未被 coerce 成 False
        assert doc["thresholds"]["maxFeatures"] == "2000"  # 未被 coerce 成 2000
        assert doc["zOrder"] == "custom-meta-preserved"
        assert doc["sources"]["pts"]["vendorHint"] == {"any": True}

    def test_invalid_and_unknown_disclosed(self):
        result = parse_mapspec(_dirty_spec())
        invalid = {d.path for d in result.invalid_fields}
        unknown = {d.path for d in result.unknown_fields}
        assert "layers[0].visible" in invalid
        assert "thresholds.maxFeatures" in invalid
        assert "zOrder" in unknown
        assert "sources.pts.vendorHint" in unknown
        assert result.valid is False

    def test_publication_semantics_match_legacy_twin(self):
        """schema 路径与 legacy raw-dict 路径的渲染语义一致。

        - thresholds："2000" 在孪生 _resolve_export_thresholds 下被忽略
          （回退默认）→ canonical document 喂同一函数必须同样忽略。
        - visible："false" 在孪生 `layer.get("visible") is False` 口径下
          仍渲染 → canonical document 的层同样不被隐藏。
        """
        from app.services.mapspec_to_svg import _resolve_export_thresholds

        spec = _dirty_spec()
        result = parse_mapspec(spec)

        cap_raw, _ = _resolve_export_thresholds(spec, None, None)
        cap_canonical, _ = _resolve_export_thresholds(result.document, None, None)
        assert cap_raw == cap_canonical  # "2000" 两侧都被忽略（默认 50000）

        def _legacy_visible(layer: dict) -> bool:
            return layer.get("visible") is False

        for raw_layer, canonical_layer in zip(spec["layers"], result.document["layers"]):
            assert _legacy_visible(raw_layer) == _legacy_visible(canonical_layer)
        # 具体断言：脏 "false" 层仍渲染，合法 False 层隐藏
        assert not _legacy_visible(result.document["layers"][0])
        assert _legacy_visible(result.document["layers"][1])

    def test_coercion_flip_regressions(self):
        """R1-C2 实证回归：这两个输入曾可被 lax 校验翻转语义。"""
        spec = _dirty_spec()
        spec["layers"][0]["visible"] = "true"  # coerce → True（同义，但仍须保真）
        result = parse_mapspec(spec)
        assert result.document["layers"][0]["visible"] == "true"
        assert any(d.path == "layers[0].visible" for d in result.invalid_fields)


# ── canonical 合同（R1-M1）───────────────────────────────────────────────


class TestCanonicalContract:
    def test_canonical_preserves_input_key_order_and_bytes(self):
        raw = {
            "customMeta": {"b": 1, "a": 2},
            "version": "1.0",
            "zOrder": 7,
            "layers": [{"id": "l", "source": "s", "type": "fill", "zz": 1}],
            "sources": {},
        }
        raw_str = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
        assert dumps_canonical(canonicalize_mapspec(raw)) == raw_str

    def test_canonical_deep_copies(self):
        raw = {"version": "1.0", "layers": [{"id": "l", "source": "s", "type": "fill"}]}
        doc = canonicalize_mapspec(raw)
        doc["layers"][0]["id"] = "mutated"
        assert raw["layers"][0]["id"] == "l"

    def test_unicode_not_escaped(self):
        raw = {"version": "1.0", "layers": [], "note": "中文披露"}
        assert "中文披露" in dumps_canonical(canonicalize_mapspec(raw))

    def test_clean_spec_is_valid(self):
        result = parse_mapspec(
            {
                "version": "1.1",
                "view": {"center": [0, 0], "zoom": 3.5},
                "sources": {
                    "t": {"type": "vector", "tiles": ["https://x/{z}/{x}/{y}.pbf"]},
                    "r": {"type": "raster", "imageRef": "ref:raster/1", "bounds": [0, 0, 1, 1]},
                    "f": {"type": "data_fabric", "catalog_item_id": "c1", "lazy": True},
                },
                "layers": [
                    {"id": "a", "source": "t", "type": "line", "paint": {"line-width": 2}},
                    {"id": "b", "source": "r", "type": "raster"},
                    {
                        "id": "c",
                        "source": "t",
                        "type": "circle",
                        "legend_spec": {"type": "categorical", "categories": []},
                        "label": {"field": "name"},
                    },
                ],
                "layout": {
                    "legend": {"visible": True, "position": "top-right"},
                    "components": [
                        {"id": "t1", "type": "title", "position": "top-center"},
                        {
                            "id": "p1",
                            "type": "legend",
                            "placement": {"mode": "floating", "x": 10, "y": 20},
                        },
                    ],
                    "frames": [{"id": "f1", "extent": [0, 0, 10, 10]}],
                    "labels": {"collision": "deterministic", "maxLabels": 400},
                },
                "thresholds": {"maxFeatures": 2000, "timeoutMs": 30000},
            }
        )
        assert result.valid, [d.to_dict() for d in result.disclosures]
        assert result.unknown_fields == []
        assert result.invalid_fields == []


# ── 迁移矩阵 ─────────────────────────────────────────────────────────────


class TestVersioningAndMigration:
    def test_known_versions(self):
        # V7（Goal 08）：1.2 additive（layout.component_links）。
        assert KNOWN_VERSIONS == ("1.0", "1.1", "1.2")
        assert LATEST_VERSION == "1.2"
        assert DEFAULT_VERSION == "1.0"

    def test_missing_version_defaults_to_1_0_and_migrates(self):
        raw = {"layers": [], "sources": {}}
        result = parse_mapspec(raw)
        assert result.original_version == "1.0"
        assert result.effective_version == LATEST_VERSION
        assert result.migrated is True
        assert result.document.get("version") is None  # 迁移不改写存储字段
        assert "version" in {d.path for d in result.invalid_fields}  # 缺失披露

    def test_v1_0_migrates_to_latest_identity(self):
        result = parse_mapspec({"version": "1.0", "layers": [], "sources": {}})
        assert result.migrated is True
        assert result.effective_version == LATEST_VERSION
        assert result.valid is True

    def test_v1_1_migrates_to_latest_identity(self):
        # 1.1 → 1.2 纯 additive：identity 语义升级（1.1 本身不再是最前）
        result = parse_mapspec({"version": "1.1", "layers": [], "sources": {}})
        assert result.migrated is True
        assert result.effective_version == LATEST_VERSION

    def test_v1_2_no_migration(self):
        result = parse_mapspec({"version": "1.2", "layers": [], "sources": {}})
        assert result.migrated is False
        assert result.effective_version == LATEST_VERSION

    def test_forward_version_flagged_and_rejected(self):
        raw = {"version": "2.0", "layers": [], "sources": {}}
        result = parse_mapspec(raw)
        assert result.forward_version is True
        assert result.migrated is False
        with pytest.raises(MapSpecSchemaError) as ei:
            require_parseable_mapspec(raw)
        assert ei.value.code == "mapspec_forward_version"

    def test_non_object_payload_rejected(self):
        with pytest.raises(MapSpecSchemaError) as ei:
            require_parseable_mapspec([1, 2, 3])
        assert ei.value.code == "mapspec_not_an_object"

    def test_register_upgrader_chain(self):
        from app.lib.cartography.mapspec_schema import register_upgrader

        register_upgrader("1.1", "1.2", lambda doc: doc)
        register_upgrader("1.2", "1.3", lambda doc: doc)
        try:
            result = parse_mapspec({"version": "1.1", "layers": []}, target_version="1.3")
            assert result.migrated is True
            assert result.effective_version == "1.3"
        finally:
            # 注册表是进程级全局 —— 清理避免泄漏进其他测试
            from app.lib.cartography import mapspec_schema as m

            m._UPGRADERS.pop(("1.1", "1.2"), None)
            m._UPGRADERS.pop(("1.2", "1.3"), None)

    def test_unknown_target_version_no_path(self):
        result = parse_mapspec({"version": "1.0", "layers": []}, target_version="9.9")
        # 找不到升级路径：文档原样返回（不抛），迁移未发生
        assert result.migrated is False
        assert result.effective_version == "1.0"


# ── unknown fields 政策 + 有界性 ─────────────────────────────────────────


class TestUnknownFieldsPolicy:
    def test_unknown_nested_everywhere_preserved(self):
        raw = {
            "version": "1.0",
            "layers": [
                {"id": "l", "source": "s", "type": "fill", "futurePaint": {"x": 1}},
                {"id": "m", "source": "s", "type": "fill", "label": {"field": "f", "future": 2}},
            ],
            "layout": {
                "components": [{"id": "c", "type": "title", "futureOption": True}],
                "futureSection": {"k": "v"},
            },
        }
        result = parse_mapspec(raw)
        unknown = {d.path for d in result.unknown_fields}
        assert unknown == {
            "layers[0].futurePaint",
            "layers[1].label.future",
            "layout.components[0].futureOption",
            "layout.futureSection",
        }
        # 文档保真
        assert result.document["layers"][0]["futurePaint"] == {"x": 1}
        assert result.document["layout"]["futureSection"] == {"k": "v"}

    def test_frames_over_limit_disclosed_but_preserved(self):
        frames = [{"id": f"f{i}"} for i in range(MAX_SPEC_FRAMES + 5)]
        raw = {"version": "1.1", "layout": {"frames": frames}}
        result = parse_mapspec(raw)
        assert len(result.document["layout"]["frames"]) == MAX_SPEC_FRAMES + 5  # 保真
        assert result.valid is False  # 超限披露（渲染端另有 50 页上限+诊断）
        assert any("frames" in d.path for d in result.invalid_fields)

    def test_disclosures_payload_shape(self):
        result = parse_mapspec(_dirty_spec())
        for item in result.disclosures_payload():
            assert set(item) == {"path", "kind", "got"}

    def test_layer_invalid_type_positions_tracked(self):
        raw = {
            "version": "1.0",
            "layers": [
                {"id": "ok", "source": "s", "type": "fill"},
                {"id": 42, "source": "s", "type": "fill"},  # 非 str id
                {"id": "also-ok", "source": "s", "type": "fill"},
            ],
        }
        result = parse_mapspec(raw)
        assert any(d.path == "layers[1].id" for d in result.invalid_fields)
        # 索引错误不殃及相邻层
        assert not any(d.path == "layers[2].id" for d in result.invalid_fields)
