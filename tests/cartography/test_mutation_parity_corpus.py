"""Mutation parity corpus — apply_mutation 行为等价安全网（H02）。

对每个 mutation intent 记录归一化投影（结果信封 + MapSpec 指纹 + 运行时
状态 + 幂等存证），golden 在**未重构**基线上生成（ZCODE_PARITY_GENERATE=1）；
lifecycle 解巨石重构（per-intent handler + registry dispatch）必须逐 case
等价。投影刻意剔除易变面：checkpoint_id 的毫秒时间戳、mapspec 全量载荷
（由 mapspec_fingerprint 内容哈希覆盖）。

确定性保障：
- 每 case 独立 tmp_path 存储域（BASE_STORAGE_DIR monkeypatch，磁盘 sidecar
  不跨运行泄漏——与 test_component_lifecycle 同款手法）；
- 每 case 独立引擎实例（prior-blocking 指纹缓存不跨 case）；
- 会话 id 带运行级 uuid（内存态不跨运行）。
"""

import json
import os
import uuid
from pathlib import Path

import pytest

import app.services.mapspec.store as mapspec_store_module
from app.services.mapspec.lifecycle_engine import MapSpecLifecycleEngine
from app.services.session_data import session_data_manager

GOLDEN_PATH = Path(__file__).parent / "golden" / "mutation_parity_corpus.golden.json"

_EMPTY_FC = {"type": "FeatureCollection", "features": []}


def _valid_workbench_doc():
    return {
        "version": 5,
        "groups": [
            {"id": "wg-1", "name": "东部", "collapsed": False, "parentId": None},
            {"id": "wg-2", "name": "子组", "collapsed": False, "parentId": "wg-1"},
        ],
        "membership": {"layer-a": "wg-2"},
        "lockedLayerIds": ["layer-b"],
        "mode": "analyze",
    }


def _layer(layer_id, *, type="circle", paint=None, source="src-1", **extra):
    layer = {"id": layer_id, "type": type, "source": source}
    if paint is not None:
        layer["paint"] = paint
    layer.update(extra)
    return layer


def _upsert(layer_id, **kw):
    return {
        "class": "UpsertLayerIntent",
        "fields": {"layer": _layer(layer_id, **kw), "source_data": dict(_EMPTY_FC)},
    }


def _heal_no_ops():
    from app.services.mapspec.visual_healer import VisualCritiqueItem

    # 未定位缺陷（layer_ids=()）→ planner 诚实跳过 → HEAL_PLAN_EMPTY。
    return {
        "class": "ApplyVisualHealPatchIntent",
        "fields": {
            "defects": [VisualCritiqueItem(category="label_collision", layer_ids=())]
        },
    }


def _heal_label(layer_id):
    from app.services.mapspec.visual_healer import VisualCritiqueItem

    return {
        "class": "ApplyVisualHealPatchIntent",
        "fields": {
            "defects": [
                VisualCritiqueItem(
                    category="label_collision",
                    layer_ids=(layer_id,),
                )
            ]
        },
    }


CASES = [
    # ── InitProject / SetView ────────────────────────────────────────────────
    {
        "name": "init_project_with_view",
        "actions": [
            {
                "class": "InitProjectIntent",
                "fields": {"view": {"center": [116.4, 39.9], "zoom": 12.0}},
            }
        ],
    },
    {
        "name": "init_project_bare",
        "actions": [{"class": "InitProjectIntent", "fields": {}}],
    },
    {
        "name": "set_view_auto_init",
        "actions": [
            {
                "class": "SetViewIntent",
                "fields": {"center": [120.0, 30.0], "zoom": 10.0},
            }
        ],
    },
    {
        "name": "set_view_update_then_partial",
        "seed": [
            {
                "class": "SetViewIntent",
                "fields": {"center": [100.0, 20.0], "zoom": 5.0, "bearing": 30.0},
            }
        ],
        "actions": [{"class": "SetViewIntent", "fields": {"zoom": 9.0}}],
    },
    # ── UpsertLayer ──────────────────────────────────────────────────────────
    {
        "name": "upsert_layer_first",
        "actions": [_upsert("ly-1", paint={"circle-color": "#ff0000"})],
    },
    {
        "name": "upsert_layer_replace_keeps_durable_presentation",
        "seed": [
            _upsert("poi"),
            {
                "class": "PatchLayerPresentationIntent",
                "fields": {"layer_id": "poi", "visible": False, "opacity": 0.3},
                "origin": "user",
                "expected_revision": "prior",
            },
        ],
        "actions": [_upsert("poi", paint={"circle-color": "#00ff00"})],
    },
    {
        "name": "upsert_layer_invalid_source_ref_blocking",
        "actions": [_upsert("ly-ghost", source="src-never-registered")],
    },
    {
        "name": "upsert_layer_suggested_view_not_framed",
        "actions": [
            {
                "class": "UpsertLayerIntent",
                "fields": {
                    "layer": _layer("geo-1", type="fill"),
                    "source_data": {
                        "type": "geojson",
                        "inlineData": {
                            "type": "FeatureCollection",
                            "features": [
                                {
                                    "type": "Feature",
                                    "geometry": {
                                        "type": "Point",
                                        "coordinates": [104.0, 30.6],
                                    },
                                    "properties": {"v": 1},
                                }
                            ],
                        },
                    },
                },
            }
        ],
    },
    # ── RemoveLayer / ReorderLayers ─────────────────────────────────────────
    {
        "name": "remove_layer_existing",
        "seed": [_upsert("roads"), _upsert("roads-label", type="symbol")],
        "actions": [{"class": "RemoveLayerIntent", "fields": {"layer_id": "roads"}}],
    },
    {
        "name": "remove_layer_missing",
        "seed": [_upsert("roads")],
        "actions": [{"class": "RemoveLayerIntent", "fields": {"layer_id": "ghost"}}],
    },
    {
        "name": "reorder_layers_partial_prefix",
        "seed": [_upsert("a"), _upsert("b"), _upsert("c")],
        "actions": [
            {"class": "ReorderLayersIntent", "fields": {"layer_ids": ["c", "a"]}}
        ],
    },
    {
        "name": "reorder_layers_no_match",
        "seed": [_upsert("a")],
        "actions": [{"class": "ReorderLayersIntent", "fields": {"layer_ids": ["zz"]}}],
    },
    # ── SetLayout ────────────────────────────────────────────────────────────
    {
        "name": "set_layout_legend_field_merge",
        "seed": [
            {
                "class": "SetLayoutIntent",
                "fields": {"legend": {"visible": True, "position": "top-right"}},
            }
        ],
        "actions": [
            {"class": "SetLayoutIntent", "fields": {"legend": {"visible": False}}}
        ],
    },
    {
        "name": "set_layout_components_replace",
        "actions": [
            {
                "class": "SetLayoutIntent",
                "fields": {
                    "components": [
                        {"id": "c-1", "type": "chart_panel", "priority": 1},
                        {"id": "c-2", "type": "chart_panel", "priority": 0},
                    ]
                },
            }
        ],
    },
    {
        "name": "set_layout_components_duplicate_ids",
        "actions": [
            {
                "class": "SetLayoutIntent",
                "fields": {
                    "components": [
                        {"id": "c-1", "type": "chart_panel"},
                        {"id": "c-1", "type": "chart_panel"},
                    ]
                },
            }
        ],
    },
    {
        "name": "set_layout_components_oversized",
        "actions": [
            {
                "class": "SetLayoutIntent",
                "fields": {
                    "components": [
                        {
                            "id": "big",
                            "type": "chart_panel",
                            "options": {"blob": "x" * 100_000},
                        },
                    ]
                },
            }
        ],
    },
    {
        "name": "set_layout_component_links_valid",
        "actions": [
            {
                "class": "SetLayoutIntent",
                "fields": {
                    "component_links": [
                        {
                            "src": "c-1",
                            "dst": "c-2",
                            "type": "composes",
                            "dst_kind": "component",
                        },
                    ]
                },
            }
        ],
    },
    {
        "name": "set_layout_component_links_invalid_type",
        "actions": [
            {
                "class": "SetLayoutIntent",
                "fields": {
                    "component_links": [
                        {
                            "src": "c-1",
                            "dst": "c-2",
                            "type": "ghost_link",
                            "dst_kind": "component",
                        },
                    ]
                },
            }
        ],
    },
    {
        "name": "set_layout_composition_oversized",
        "actions": [
            {
                "class": "SetLayoutIntent",
                "fields": {"composition": {"blob": "x" * 5000}},
            }
        ],
    },
    # ── PatchLayerPresentation / PatchLayerStyle ────────────────────────────
    {
        "name": "patch_presentation_hide_and_opacity",
        "seed": [_upsert("ly-1")],
        "actions": [
            {
                "class": "PatchLayerPresentationIntent",
                "fields": {"layer_id": "ly-1", "visible": False, "opacity": 0.4},
            }
        ],
    },
    {
        "name": "patch_presentation_missing_layer",
        "seed": [_upsert("ly-1")],
        "actions": [
            {
                "class": "PatchLayerPresentationIntent",
                "fields": {"layer_id": "ghost", "visible": True},
            }
        ],
    },
    {
        "name": "patch_style_merge",
        "seed": [_upsert("ly-1", paint={"circle-color": "#ff0000"})],
        "actions": [
            {
                "class": "PatchLayerStyleIntent",
                "fields": {"layer_id": "ly-1", "paint": {"circle-opacity": 0.5}},
            }
        ],
    },
    {
        "name": "patch_style_missing_layer",
        "seed": [_upsert("ly-1")],
        "actions": [
            {
                "class": "PatchLayerStyleIntent",
                "fields": {"layer_id": "ghost", "paint": {}},
            }
        ],
    },
    # ── UpsertSource ─────────────────────────────────────────────────────────
    {
        "name": "upsert_source_basic",
        "actions": [
            {
                "class": "UpsertSourceIntent",
                "fields": {
                    "source_id": "src-9",
                    "source": {"type": "geojson", "dataPath": "x.json"},
                },
            }
        ],
    },
    {
        "name": "upsert_source_inline_empty_fc_gate",
        "actions": [
            {
                "class": "UpsertSourceIntent",
                "fields": {
                    "source_id": "src-empty",
                    "source": {"type": "geojson", "inlineData": dict(_EMPTY_FC)},
                },
            }
        ],
    },
    # ── Component lifecycle ──────────────────────────────────────────────────
    {
        "name": "patch_component_upsert_new",
        "actions": [
            {
                "class": "PatchComponentIntent",
                "fields": {
                    "component_id": "chart-panel",
                    "component_type": "chart_panel",
                    "upsert": True,
                },
            }
        ],
    },
    {
        "name": "patch_component_no_components_no_upsert",
        "actions": [
            {
                "class": "PatchComponentIntent",
                "fields": {"component_id": "ghost", "enabled": False},
            }
        ],
    },
    {
        "name": "patch_component_update_then_unknown",
        "seed": [
            {
                "class": "PatchComponentIntent",
                "fields": {
                    "component_id": "chart-panel",
                    "component_type": "chart_panel",
                    "upsert": True,
                },
            }
        ],
        "actions": [
            {
                "class": "PatchComponentIntent",
                "fields": {"component_id": "chart-panel", "enabled": False},
            },
            {
                "class": "PatchComponentIntent",
                "fields": {"component_id": "ghost", "enabled": True},
            },
        ],
    },
    {
        "name": "remove_component_then_missing",
        "seed": [
            {
                "class": "PatchComponentIntent",
                "fields": {
                    "component_id": "chart-panel",
                    "component_type": "chart_panel",
                    "upsert": True,
                },
            }
        ],
        "actions": [
            {
                "class": "RemoveComponentIntent",
                "fields": {"component_id": "chart-panel"},
            },
            {
                "class": "RemoveComponentIntent",
                "fields": {"component_id": "chart-panel"},
            },
        ],
    },
    {
        "name": "duplicate_component_multi_instance",
        "seed": [
            {
                "class": "PatchComponentIntent",
                "fields": {
                    "component_id": "chart-panel",
                    "component_type": "chart_panel",
                    "upsert": True,
                },
            }
        ],
        "actions": [
            {
                "class": "DuplicateComponentIntent",
                "fields": {"component_id": "chart-panel"},
            }
        ],
    },
    {
        "name": "duplicate_component_singleton_refused",
        "actions": [
            {
                "class": "DuplicateComponentIntent",
                "fields": {"component_id": "north-arrow"},
            }
        ],
    },
    {
        "name": "rebind_component_whitelist_ok",
        "seed": [
            {
                "class": "PatchComponentIntent",
                "fields": {
                    "component_id": "chart-panel",
                    "component_type": "chart_panel",
                    "upsert": True,
                },
            }
        ],
        "actions": [
            {
                "class": "RebindComponentIntent",
                "fields": {
                    "component_id": "chart-panel",
                    "bindings": {"chartRef": "ref:chart-xyz"},
                },
            }
        ],
    },
    {
        "name": "rebind_component_layer_missing",
        "seed": [
            {
                "class": "PatchComponentIntent",
                "fields": {
                    "component_id": "chart-panel",
                    "component_type": "chart_panel",
                    "upsert": True,
                },
            }
        ],
        "actions": [
            {
                "class": "RebindComponentIntent",
                "fields": {
                    "component_id": "chart-panel",
                    "bindings": {"layerId": "ghost-layer"},
                },
            }
        ],
    },
    # ── Chrome: basemap / time / scenario / scene ────────────────────────────
    {
        "name": "set_basemap_partial",
        "actions": [
            {"class": "SetBasemapIntent", "fields": {"provider_id": "positron"}}
        ],
    },
    {
        "name": "set_time_defaults_merge",
        "actions": [
            {
                "class": "SetTimeIntent",
                "fields": {"enabled": True, "field": "ts", "step": 2.0},
            }
        ],
    },
    {
        "name": "set_scenario_mode_invalid",
        "actions": [
            {"class": "SetScenarioModeIntent", "fields": {"scenario_mode": "hologram"}}
        ],
    },
    {
        "name": "set_scenario_mode_set_then_exit",
        "actions": [
            {
                "class": "SetScenarioModeIntent",
                "fields": {"scenario_mode": "split_view"},
            },
            {"class": "SetScenarioModeIntent", "fields": {"scenario_mode": None}},
        ],
    },
    {
        "name": "set_scene_valid_then_clear",
        "actions": [
            {
                "class": "SetSceneIntent",
                "fields": {"scene": {"mode": "3d", "reason_code": "terrain_request"}},
            },
            {"class": "SetSceneIntent", "fields": {"scene": None}},
        ],
    },
    {
        "name": "set_scene_invalid_mode",
        "actions": [{"class": "SetSceneIntent", "fields": {"scene": {"mode": "4d"}}}],
    },
    {
        "name": "set_scene_terrain_ref_blocking",
        "actions": [
            {
                "class": "SetSceneIntent",
                "fields": {
                    "scene": {
                        "mode": "3d",
                        "terrain": {"source": "no-such-dem", "exaggeration": 1.2},
                    }
                },
            }
        ],
    },
    {
        "name": "set_scene_camera_pitch_out_of_range",
        "actions": [
            {
                "class": "SetSceneIntent",
                "fields": {"scene": {"mode": "3d", "camera": {"pitch": 120.0}}},
            }
        ],
    },
    # ── Workbench ────────────────────────────────────────────────────────────
    {
        "name": "workbench_state_user_commit",
        "actions": [
            {
                "class": "SetWorkbenchStateIntent",
                "fields": {"doc": _valid_workbench_doc()},
                "origin": "user",
                "expected_revision": "prior",
            }
        ],
    },
    {
        "name": "workbench_state_stale_cas_superseded",
        "seed": [
            {
                "class": "SetWorkbenchStateIntent",
                "fields": {"doc": _valid_workbench_doc()},
                "origin": "user",
                "expected_revision": "prior",
            }
        ],
        "actions": [
            {
                "class": "SetWorkbenchStateIntent",
                "fields": {"doc": _valid_workbench_doc(), "base_workbench_revision": 0},
                "origin": "user",
                "expected_revision": "prior",
            }
        ],
    },
    {
        "name": "workbench_state_invalid_docs",
        "actions": [
            {
                "class": "SetWorkbenchStateIntent",
                "fields": {"doc": {"version": 4}},
                "origin": "user",
                "expected_revision": "prior",
            },
            {
                "class": "SetWorkbenchStateIntent",
                "fields": {
                    "doc": {
                        "version": 5,
                        "groups": [{"id": "a"}, {"id": "a"}],
                        "mode": "explore",
                    }
                },
                "origin": "user",
                "expected_revision": "prior",
            },
            {
                "class": "SetWorkbenchStateIntent",
                "fields": {"doc": {"version": 5, "groups": [], "mode": "hacker"}},
                "origin": "user",
                "expected_revision": "prior",
            },
        ],
    },
    {
        "name": "workbench_delta_apply_then_conflict",
        "seed": [
            {
                "class": "SetWorkbenchStateIntent",
                "fields": {"doc": _valid_workbench_doc()},
                "origin": "user",
                "expected_revision": "prior",
            }
        ],
        "actions": [
            {
                "class": "PatchWorkbenchDeltaIntent",
                "fields": {"delta": {"setGroups": [{"id": "wg-3", "name": "新增组"}]}},
            },
            {
                "class": "PatchWorkbenchDeltaIntent",
                "fields": {"delta": {"membershipSet": {"ly-x": "wg-404"}}},
            },
        ],
    },
    {
        "name": "workbench_delta_invalid_shape",
        "actions": [
            {"class": "PatchWorkbenchDeltaIntent", "fields": {"delta": {"ghostOp": 1}}}
        ],
    },
    # ── Checkpoint / Rollback ────────────────────────────────────────────────
    {
        "name": "checkpoint_then_rollback",
        "seed": [
            {"class": "SetViewIntent", "fields": {"center": [100.0, 20.0], "zoom": 5.0}}
        ],
        "actions": [
            {"class": "CheckpointIntent", "fields": {"checkpoint_id": "parity-ckpt"}},
            {
                "class": "SetViewIntent",
                "fields": {"center": [110.0, 30.0], "zoom": 10.0},
            },
            {"class": "RollbackIntent", "fields": {"checkpoint_id": "parity-ckpt"}},
        ],
    },
    {
        "name": "rollback_missing_checkpoint",
        "actions": [
            {"class": "RollbackIntent", "fields": {"checkpoint_id": "no-such-ckpt"}}
        ],
    },
    # ── RestoreStyle ─────────────────────────────────────────────────────────
    {
        "name": "restore_style_subset_skips_missing",
        "seed": [_upsert("ly-a", paint={"circle-color": "#123456"})],
        "actions": [
            {
                "class": "RestoreStyleIntent",
                "fields": {
                    "snapshot": {
                        "view": {"center": [1.0, 2.0], "zoom": 4.0, "framed": True},
                        "layout": {
                            "legend": {"visible": False, "position": "bottom-left"}
                        },
                        "layers": [
                            {
                                "id": "ly-a",
                                "paint": {"circle-color": "#654321"},
                                "layout": {"visibility": "none"},
                            },
                            {"id": "ly-vanished", "paint": {}},
                        ],
                    }
                },
            }
        ],
    },
    # ── Visual heal ──────────────────────────────────────────────────────────
    {
        "name": "visual_heal_empty_spec",
        "actions": [{"build": _heal_no_ops}],
    },
    {
        "name": "visual_heal_unlocatable_defect_skipped",
        "seed": [_upsert("ly-1")],
        "actions": [{"build": _heal_no_ops}],
    },
    {
        "name": "visual_heal_label_plan",
        "seed": [_upsert("ly-1")],
        "actions": [{"build": lambda: _heal_label("ly-1")}],
    },
    # ── 信封语义：dedup / CAS / user 契约 ────────────────────────────────────
    {
        "name": "duplicate_mutation_id_replay",
        "seed": [
            {"class": "SetViewIntent", "fields": {"center": [1.0, 1.0], "zoom": 3.0}}
        ],
        "actions": [
            {
                "class": "SetViewIntent",
                "fields": {"zoom": 4.0},
                "mutation_id": "parity-dup-1",
            },
            {
                "class": "SetViewIntent",
                "fields": {"zoom": 5.0},
                "mutation_id": "parity-dup-1",
            },
        ],
    },
    {
        "name": "user_cas_superseded",
        "seed": [
            {"class": "SetViewIntent", "fields": {"center": [1.0, 1.0], "zoom": 3.0}}
        ],
        "actions": [
            {
                "class": "SetViewIntent",
                "fields": {"zoom": 4.0},
                "origin": "user",
                "expected_revision": 0,
            }
        ],
    },
    {
        "name": "user_missing_expected_revision",
        "actions": [
            {"class": "SetViewIntent", "fields": {"zoom": 4.0}, "origin": "user"}
        ],
    },
]


def _build_intent(step):
    if "build" in step:
        builder = step["build"]
        step = builder() if callable(builder) else builder
    cls = getattr(
        __import__("app.services.mapspec.lifecycle_engine", fromlist=[step["class"]]),
        step["class"],
    )
    fields = dict(step.get("fields", {}))
    if "defects" in fields and isinstance(fields["defects"], list):
        fields["defects"] = tuple(fields["defects"])
    return cls(**fields)


def _projection(result):
    """结果信封的归一化投影（剔除易变面）。"""
    d = result.to_dict()
    d.pop("mapspec", None)
    if d.get("checkpoint_id"):
        d["checkpoint_id"] = "<ckpt>"
    d["has_mapspec"] = result.mapspec is not None
    d["layer_ids"] = [
        str(layer.get("id"))
        for layer in ((result.mapspec or {}).get("layers") or [])
        if isinstance(layer, dict)
    ]
    return d


async def _state_projection(engine, session_id):
    state = await session_data_manager.get_map_state(session_id)
    dedup = state.get("_mutation_dedup")
    return {
        "revision": state.get("_cartographic_mutation_revision", 0),
        "dedup_ids": sorted(dedup.keys()) if isinstance(dedup, dict) else [],
        "runtime_layer_ids": [
            str(layer.get("id"))
            for layer in (state.get("layers") or [])
            if isinstance(layer, dict)
        ],
    }


async def _run_case(case, tmp_path, monkeypatch):
    monkeypatch.setattr(mapspec_store_module, "BASE_STORAGE_DIR", tmp_path)
    engine = MapSpecLifecycleEngine()
    session_id = f"parity-{uuid.uuid4().hex[:12]}"
    projections = []
    prior_revision = 0

    async def _apply(step):
        nonlocal prior_revision
        kwargs = {}
        if step.get("origin"):
            kwargs["origin"] = step["origin"]
        if "expected_revision" in step:
            er = step["expected_revision"]
            kwargs["expected_revision"] = prior_revision if er == "prior" else er
        if step.get("mutation_id"):
            kwargs["mutation_id"] = step["mutation_id"]
        result = await engine.apply_mutation(session_id, _build_intent(step), **kwargs)
        if isinstance(result.mutation_revision, int) and result.mutation_revision:
            prior_revision = result.mutation_revision
        return result

    for step in case.get("seed", []):
        await _apply(step)
    for step in case.get("actions", []):
        result = await _apply(step)
        projections.append(
            {
                "result": _projection(result),
                "state": await _state_projection(engine, session_id),
            }
        )
    return projections


@pytest.mark.asyncio
async def test_zcode_generate_golden(tmp_path_factory, monkeypatch):
    """golden 生成入口：ZCODE_PARITY_GENERATE=1 pytest 本文件::test_zcode_generate_golden。"""
    if not os.environ.get("ZCODE_PARITY_GENERATE"):
        pytest.skip("set ZCODE_PARITY_GENERATE=1 to regenerate golden")
    golden = {}
    for case in CASES:
        projections = await _run_case(
            case, tmp_path_factory.mktemp(case["name"]), monkeypatch
        )
        golden[case["name"]] = projections
    assert len(golden) == len(CASES) and all(golden.values()), (
        "golden 生成必须覆盖全部 case 且逐 case 非空"
    )
    GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN_PATH.write_text(
        json.dumps(golden, ensure_ascii=False, indent=1, sort_keys=True),
        encoding="utf-8",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
async def test_mutation_parity_corpus(case, tmp_path, monkeypatch):
    if os.environ.get("ZCODE_PARITY_GENERATE"):
        pytest.skip("generation run")
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    assert case["name"] in golden, (
        f"case {case['name']} missing from golden — regenerate corpus"
    )
    projections = await _run_case(case, tmp_path, monkeypatch)
    assert projections == golden[case["name"]], f"parity drift in case {case['name']}"


def test_corpus_covers_every_registry_intent():
    """corpus 覆盖 gate：每个注册 intent 至少出现在一个 case 的 seed/action。

    registry 缺席（重构前基线）时 skip —— registry 落地后此 gate 生效。
    """
    registry = pytest.importorskip("app.services.mapspec.mutation_registry")
    seen = set()
    for case in CASES:
        for step in case.get("seed", []) + case.get("actions", []):
            if "build" in step:
                step = step["build"]() if callable(step["build"]) else step["build"]
            name = step.get("class")
            if name:
                seen.add(name)
    missing = sorted(
        d.intent_cls.__name__
        for d in registry.MUTATION_REGISTRY.all()
        if d.intent_cls.__name__ not in seen
    )
    assert not missing, f"corpus 未覆盖 intents: {missing}"
