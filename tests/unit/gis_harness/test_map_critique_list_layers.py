"""R2-P1（qc-loop round 2/3）回归锁：观察层 canonical list 形状必须可用。

历史缺陷：``_observed_layers`` 把 ``observation["layers"]`` 当 dict 调
``.items()``，而 canonical 载荷是 ``list[dict]``（chat_schema.py，
条目带 id/runtime_store_id）→ AttributeError 被 completion/pipeline 的
``except Exception: pass`` 整体吞掉 —— blank_map / invalid_bounds /
publication_components / label_collision / overlay_mismatch 五项 V7 检查
在真实观察（list 形状）下全数静默失效。dict 形状保留兼容。
"""
from app.services.gis_harness.map_critique import (
    C_BLANK_MAP,
    _observed_layers,
    check_blank_map,
)


def _chapter(layers=2):
    return {
        "map_layers": [
            {"layer_id": f"lyr-{i}", "kind": "result"} for i in range(layers)
        ],
    }


def test_list_shaped_layers_indexed_by_id():
    observation = {"layers": [
        {"id": "lyr-0", "render_complete": True, "feature_count": 0},
        {"id": "lyr-1", "render_complete": True, "feature_count": 12},
    ]}
    observed = _observed_layers(observation)
    assert set(observed) == {"lyr-0", "lyr-1"}
    assert observed["lyr-0"]["feature_count"] == 0


def test_list_shaped_layers_indexed_by_runtime_store_id():
    observation = {"layers": [
        {"runtime_store_id": "rt-7", "render_complete": True},
    ]}
    observed = _observed_layers(observation)
    assert set(observed) == {"rt-7"}


def test_blank_map_fires_on_canonical_list_observation():
    observation = {"layers": [
        {"id": "lyr-0", "render_complete": True, "feature_count": 0},
        {"id": "lyr-1", "render_complete": True, "feature_count": 0},
    ]}
    findings = check_blank_map(_chapter(), observation)
    assert [f.code for f in findings] == [C_BLANK_MAP]


def test_dict_shaped_layers_still_supported():
    observation = {"layers": {
        "lyr-0": {"render_complete": True, "feature_count": 0},
        "lyr-1": {"render_complete": True, "feature_count": 0},
    }}
    findings = check_blank_map(_chapter(), observation)
    assert [f.code for f in findings] == [C_BLANK_MAP]
