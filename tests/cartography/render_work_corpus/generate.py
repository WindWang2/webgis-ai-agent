"""RenderWork corpus generator（Goal 03，ADR-0224）。

生成两份锁定物（**运行真实实现生成，禁止手改**）：

1. ``contract_manifest.json`` —— Python↔TS 词表/预算三重锁的 JSON 侧
   （backend 单一权威 consts 的 canonical 投影）；
2. ``render_work_golden.json`` —— 12 场景执行信封语料 + ACK builder
   输入（同一份 JSON 被 Python 投影重放测试
   ``test_render_work_corpus_v1.py`` 与 vitest ``corpus.golden.test.ts``
   消费 —— 跨语言单一语料，两侧漂移任一侧爆；ACK 侧断言在 TS 测试内
   以真实 builder 输出书写）。

再生：``python tests/cartography/render_work_corpus/generate.py``
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.lib.cartography.render_apply_ack import (  # noqa: E402
    ACK_SCHEMA_VERSION,
    ACK_STATUSES,
    ENTRY_STATUSES,
    MAX_ACK_COMPONENTS,
    MAX_ACK_LAYERS,
    MAX_ACK_SOURCES,
    REASON_CODES,
)
from app.lib.cartography.render_perf_probes import (  # noqa: E402
    PERF_SCHEMA_VERSION,
)
from app.lib.cartography.render_work_envelope import (  # noqa: E402
    MAX_COMPONENT_WORK,
    MAX_DATA_DEPENDENCIES,
    MAX_DIAGNOSTICS,
    MAX_LAYER_WORK,
    RENDER_WORK_SCHEMA_VERSION,
    _SOURCE_KINDS,
)
from app.lib.cartography.renderer_capability import (  # noqa: E402
    CAPABILITY_SCHEMA_VERSION,
    FEATURE_SUPPORT,
    SOURCE_TYPE_SUPPORT,
)

CORPUS_DIR = Path(__file__).resolve().parent


def contract_manifest() -> dict:
    return {
        "render_work": {
            "schema_version": RENDER_WORK_SCHEMA_VERSION,
            "max_data_dependencies": MAX_DATA_DEPENDENCIES,
            "max_layer_work": MAX_LAYER_WORK,
            "max_component_work": MAX_COMPONENT_WORK,
            "max_diagnostics": MAX_DIAGNOSTICS,
            "source_kinds": sorted(_SOURCE_KINDS),
        },
        "apply_ack": {
            "schema_version": ACK_SCHEMA_VERSION,
            "statuses": list(ACK_STATUSES),
            "entry_statuses": list(ENTRY_STATUSES),
            "reason_codes": sorted(REASON_CODES),
            "max_layers": MAX_ACK_LAYERS,
            "max_components": MAX_ACK_COMPONENTS,
            "max_sources": MAX_ACK_SOURCES,
        },
        "perf": {"schema_version": PERF_SCHEMA_VERSION},
        "capability": {
            "schema_version": CAPABILITY_SCHEMA_VERSION,
            "levels": ["full", "partial", "none"],
            "source_type_levels": {
                k: v["level"] for k, v in sorted(SOURCE_TYPE_SUPPORT.items())
            },
            "feature_support": {
                k: bool(v["supported"])
                for k, v in sorted(FEATURE_SUPPORT.items())
            },
        },
    }


# ── 场景语料（12 场景；mapspec 是 TS/Python 双消费的纯 JSON）──────────


def _spec(layers, sources=None, components=None):
    return {
        "version": "1.5",
        "view": {"center": [116.4, 39.9], "zoom": 10},
        "sources": sources or {},
        "layers": layers,
        "layout": {"components": components or []},
    }


def _geojson_source(revision, fp="d-" + "0" * 8):
    return {"type": "geojson", "content_revision": revision, "descriptor_fingerprint": fp}


def scenarios() -> list:
    """(name, projector_input, ack_input) 三元组列表。

    ack_input 是 buildRenderApplyAck 的 TS 侧输入（applied/mapIdle/pending/
    work）。Python 侧只投影 envelope；ACK 断言在 corpus.golden.test.ts
    内以真实 TS builder 输出书写（重放漂移由该测试爆出）。
    """
    ten_layers = [
        {
            "id": f"layer-{i}",
            "type": "circle" if i % 2 == 0 else "fill",
            "source": "src-main",
            "visible": True,
        }
        for i in range(10)
    ]
    return [
        {
            "name": "s01_ten_layer_full_apply",
            "projector": {
                "mapspec": _spec(ten_layers, {"src-main": _geojson_source(3)}),
                "session_id": "sess-A",
                "revision": 7,
                # 真实指纹形状（carto-sha256:<64hex>）—— 锁定 work_id 切
                # 哈希段而非 scheme 前缀（review P0-1 回归）。
                "fingerprint": "carto-sha256:" + "1" * 64,
                "observation_seq": 41,
            },
            "ack": {
                "revision": 7,
                "mapIdle": True,
                "pendingLayerIds": [],
                "pendingRemovedIds": [],
                "applied_layers": [ly["id"] for ly in ten_layers],
                "applied_sources": {"src-main": 3},
                "mounted_component_ids": [],
            },
        },
        {
            "name": "s02_unsupported_layer_type_partial",
            "projector": {
                "mapspec": _spec(
                    [
                        {"id": "ok-fill", "type": "fill", "source": "src-a", "visible": True},
                        {"id": "bad-type", "type": "voxel", "source": "src-a", "visible": True},
                    ],
                    {"src-a": _geojson_source(1)},
                ),
                "session_id": "sess-A",
                "revision": 8,
                "fingerprint": "fp-" + "2" * 16,
                "observation_seq": 42,
            },
            "ack": {
                "revision": 8,
                "mapIdle": True,
                "pendingLayerIds": [],
                "pendingRemovedIds": [],
                "applied_layers": ["ok-fill"],
                "applied_sources": {"src-a": 1},
                "mounted_component_ids": [],
            },
        },
        {
            "name": "s03_style_only_patch_preserves_data",
            "projector": {
                "mapspec": _spec(ten_layers, {"src-main": _geojson_source(3)}),
                "session_id": "sess-A",
                "revision": 9,
                "fingerprint": "fp-" + "3" * 16,
                "observation_seq": 43,
                "previous": "s01_ten_layer_full_apply",
            },
            "ack": {
                "revision": 9,
                "mapIdle": True,
                "pendingLayerIds": [],
                "pendingRemovedIds": [],
                "applied_layers": [ly["id"] for ly in ten_layers],
                "applied_sources": {"src-main": 3},
                "mounted_component_ids": [],
            },
        },
        {
            "name": "s04_same_ref_revision_changed",
            "projector": {
                "mapspec": _spec(ten_layers, {"src-main": _geojson_source(4)}),
                "session_id": "sess-A",
                "revision": 10,
                "fingerprint": "fp-" + "4" * 16,
                "observation_seq": 44,
                "previous": "s03_style_only_patch_preserves_data",
            },
            "ack": {
                "revision": 10,
                "mapIdle": True,
                "pendingLayerIds": [],
                "pendingRemovedIds": [],
                "applied_layers": [ly["id"] for ly in ten_layers],
                # 数据面仍在旧 revision（3）—— per-source stale 期望。
                "applied_sources": {"src-main": 3},
                "mounted_component_ids": [],
            },
        },
        {
            "name": "s05_user_hidden_layer_abstains",
            "projector": {
                "mapspec": _spec(
                    [
                        {"id": "kept", "type": "circle", "source": "src-u", "visible": True},
                        {"id": "user-hid", "type": "circle", "source": "src-u", "visible": True},
                    ],
                    {"src-u": _geojson_source(2)},
                ),
                "session_id": "sess-B",
                "revision": 3,
                "fingerprint": "fp-" + "5" * 16,
                "observation_seq": 12,
            },
            "ack": {
                "revision": 3,
                "mapIdle": True,
                # 用户正在隐藏 user-hid（pending 乐观操作）→ ACK 弃权该层。
                "pendingLayerIds": ["user-hid"],
                "pendingRemovedIds": [],
                "applied_layers": ["kept", "user-hid"],
                "applied_sources": {"src-u": 2},
                "mounted_component_ids": [],
            },
        },
        {
            "name": "s06_missing_after_apply_failed",
            "projector": {
                "mapspec": _spec(
                    [{"id": "ghost", "type": "line", "source": "src-g", "visible": True}],
                    {"src-g": _geojson_source(5)},
                ),
                "session_id": "sess-C",
                "revision": 2,
                "fingerprint": "fp-" + "6" * 16,
                "observation_seq": 7,
            },
            "ack": {
                "revision": 2,
                "mapIdle": True,
                "pendingLayerIds": [],
                "pendingRemovedIds": [],
                # apply 后层缺席（mapIdle 落定）→ failed + missing_after_apply。
                "applied_layers": [],
                "applied_sources": {"src-g": 5},
                "mounted_component_ids": [],
            },
        },
        {
            "name": "s07_component_unsupported_pending",
            "projector": {
                "mapspec": _spec(
                    [{"id": "base", "type": "fill", "source": "src-c", "visible": True}],
                    {"src-c": _geojson_source(1)},
                    [{"id": "comp-1", "type": "legend", "enabled": True},
                     {"id": "comp-2", "type": "hologram_deck", "enabled": True}],
                ),
                "session_id": "sess-D",
                "revision": 5,
                "fingerprint": "fp-" + "7" * 16,
                "observation_seq": 19,
            },
            "ack": {
                "revision": 5,
                "mapIdle": True,
                "pendingLayerIds": [],
                "pendingRemovedIds": [],
                "applied_layers": ["base"],
                "applied_sources": {"src-c": 1},
                # chrome 观察基准在场（legend mounted）；hologram_deck 非
                # chrome-renderable → mounted=False → failed missing_after_apply。
                "mounted_component_ids": ["comp-1"],
            },
        },
        {
            "name": "s08_representation_passthrough",
            "projector": {
                # #1617 前向兼容：representation 块原样留在 MapSpec（extra=
                # allow round-trip），信封不重裁决、不复述 —— golden 锁定
                # envelope 无 representation 复述 + layer_work 正常投影。
                "mapspec": _spec(
                    [
                        {
                            "id": "rep-layer", "type": "circle", "source": "src-r",
                            "visible": True,
                            "representation": {
                                "windows": [
                                    {"min_zoom": 0, "max_zoom": 8, "mode": "raw"},
                                    {"min_zoom": 8, "max_zoom": 24, "mode": "point_cluster"},
                                ],
                            },
                        },
                    ],
                    {"src-r": _geojson_source(6)},
                ),
                "session_id": "sess-E",
                "revision": 11,
                "fingerprint": "fp-" + "8" * 16,
                "observation_seq": 30,
            },
            "ack": {
                "revision": 11,
                "mapIdle": True,
                "pendingLayerIds": [],
                "pendingRemovedIds": [],
                "applied_layers": ["rep-layer"],
                "applied_sources": {"src-r": 6},
                "mounted_component_ids": [],
            },
        },
        {
            "name": "s09_mvt_vector_source",
            "projector": {
                "mapspec": _spec(
                    [
                        {"id": "mvt-lyr", "type": "fill", "source": "tiles-x", "visible": True},
                        {"id": "raster-lyr", "type": "hillshade", "source": "dem-y", "visible": True},
                    ],
                    {
                        "tiles-x": {
                            "type": "vector",
                            "tiles": ["https://t.example/{z}/{x}/{y}.mvt"],
                        },
                        "dem-y": {"type": "raster-dem", "url": "https://d.example/terrarium.json"},
                    },
                ),
                "session_id": "sess-F",
                "revision": 4,
                "fingerprint": "fp-" + "9" * 16,
                "observation_seq": 15,
            },
            "ack": {
                "revision": 4,
                "mapIdle": True,
                "pendingLayerIds": [],
                "pendingRemovedIds": [],
                "applied_layers": ["mvt-lyr", "raster-lyr"],
                # MVT/DEM 源在场但无内容代次（URL 源）—— null = 在场无 revision。
                "applied_sources": {"tiles-x": None, "dem-y": None},
                "mounted_component_ids": [],
            },
        },
        {
            "name": "s10_data_fabric_ref_dependency",
            "projector": {
                "mapspec": _spec(
                    [{"id": "fab", "type": "circle", "source": "src-fab", "visible": True}],
                    {
                        "src-fab": {
                            "type": "data_fabric",
                            "ref_id": "ref-abc-123",
                            "content_revision": 9,
                            "data_fingerprint": "df-" + "a" * 12,
                        },
                    },
                ),
                "session_id": "sess-G",
                "revision": 6,
                "fingerprint": "fp-" + "a" * 16,
                "observation_seq": 21,
            },
            "ack": {
                "revision": 6,
                "mapIdle": True,
                "pendingLayerIds": [],
                "pendingRemovedIds": [],
                "applied_layers": ["fab"],
                "applied_sources": {"src-fab": 9},
                "mounted_component_ids": [],
            },
        },
        {
            "name": "s11_reconnect_idempotent_replay",
            "projector": {
                # 重连/重放：同输入必同 work_id/fingerprint（golden 锁定
                # determinism —— 与 s01 输入相同但独立投影）。
                "mapspec": _spec(ten_layers, {"src-main": _geojson_source(3)}),
                "session_id": "sess-A",
                "revision": 7,
                # 真实指纹形状（carto-sha256:<64hex>）—— 锁定 work_id 切
                # 哈希段而非 scheme 前缀（review P0-1 回归）。
                "fingerprint": "carto-sha256:" + "1" * 64,
                "observation_seq": 41,
            },
            "ack": {
                "revision": 7,
                "mapIdle": True,
                "pendingLayerIds": [],
                "pendingRemovedIds": [],
                "applied_layers": [ly["id"] for ly in ten_layers],
                "applied_sources": {"src-main": 3},
                "mounted_component_ids": [],
            },
        },
        {
            "name": "s12_layer_remove_and_invalidation",
            "projector": {
                # s04 已把数据推进到 revision 4 —— 纯层删除场景沿用 4
                # （invalidation 只报 layers_removed，data_changed 空）。
                "mapspec": _spec(
                    ten_layers[:5], {"src-main": _geojson_source(4)},
                ),
                "session_id": "sess-A",
                "revision": 12,
                "fingerprint": "fp-" + "b" * 16,
                "observation_seq": 50,
                "previous": "s04_same_ref_revision_changed",
            },
            "ack": {
                "revision": 12,
                "mapIdle": True,
                "pendingLayerIds": [],
                "pendingRemovedIds": [],
                "applied_layers": [ly["id"] for ly in ten_layers[:5]],
                "applied_sources": {"src-main": 4},
                "mounted_component_ids": [],
            },
        },
    ]


def generate() -> None:
    from app.lib.cartography.render_work_envelope import (
        project_render_work_envelope,
    )

    manifest = contract_manifest()
    (CORPUS_DIR / "contract_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    produced: dict[str, dict] = {}
    envelopes: dict[str, dict] = {}
    for sc in scenarios():
        inp = dict(sc["projector"])
        previous_name = inp.pop("previous", None)
        previous = None
        if previous_name:
            from app.lib.cartography.render_work_envelope import (
                project_render_work_envelope as project,
            )

            prev_input = dict(
                next(p for p in scenarios() if p["name"] == previous_name)["projector"]
            )
            prev_input.pop("previous", None)
            prev_mapspec = prev_input.pop("mapspec")
            previous = project(prev_mapspec, **prev_input)
        mapspec = inp.pop("mapspec")
        envelope = project_render_work_envelope(mapspec, previous=previous, **inp)
        envelopes[sc["name"]] = envelope.to_dict()
        produced[sc["name"]] = {
            "projector_input": {**inp, "mapspec": mapspec, **(
                {"previous": previous_name} if previous_name else {}
            )},
            "ack_input": sc["ack"],
            "envelope": envelope.to_dict(),
        }
    # 确定性守卫内联：s11 == s01 envelope（同输入同输出）。
    assert envelopes["s11_reconnect_idempotent_replay"] == envelopes["s01_ten_layer_full_apply"], (
        "determinism violation: s11 must equal s01"
    )
    (CORPUS_DIR / "render_work_golden.json").write_text(
        json.dumps(
            {
                "schema_version": RENDER_WORK_SCHEMA_VERSION,
                "scenarios": produced,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {CORPUS_DIR / 'contract_manifest.json'}")
    print(f"wrote {CORPUS_DIR / 'render_work_golden.json'} ({len(produced)} scenarios)")


if __name__ == "__main__":
    generate()
