"""V6（ADR-0120 W4）canonical render scene —— 双语言 parity。

fixtures（tests/cartography/golden_corpus/render_scene/*.json）由 pytest 与
vitest 消费同一批文件：expected 快照为**手写 oracle**（非被测代码生成），
跨语言断言同一投影语义。改投影语义必须双侧同步 + 显式更新 expected。
"""
import json
from pathlib import Path

import pytest

from app.lib.cartography.render_scene import (
    describe_render_scene,
    resolve_components,
)

FIXTURE_DIR = Path(__file__).resolve().parent / "golden_corpus" / "render_scene"
FIXTURES = sorted(FIXTURE_DIR.glob("*.json"))


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("fixture_path", FIXTURES, ids=lambda p: p.stem)
def test_scene_matches_golden(fixture_path: Path):
    case = _load(fixture_path)
    snapshot = describe_render_scene(case["spec"], case.get("degradations"))
    assert snapshot.to_dict() == case["expected"], fixture_path.name


def test_fixtures_exist():
    assert len(FIXTURES) >= 4, "共享语料不应缩水"


def test_snapshot_json_deterministic():
    case = _load(FIXTURE_DIR / "choropleth_basic.json")
    s1 = describe_render_scene(case["spec"]).to_json(indent=2)
    s2 = describe_render_scene(case["spec"]).to_json(indent=2)
    assert s1 == s2


class TestComponentResolutionMirrorsFrontend:
    def test_floating_rect_defaults(self):
        spec = {
            "layout": {
                "components": [
                    {"id": "f", "type": "annotation", "placement": {"mode": "floating", "x": 10}}
                ]
            }
        }
        (comp,) = resolve_components(spec)
        assert comp.floating is True
        assert comp.floating_rect == {
            "x": 10.0,
            "y": 0.0,
            "width": None,
            "height": None,
            "zIndex": 40.0,
            "collapsed": False,
        }

    def test_anchor_precedence(self):
        spec = {
            "layout": {
                "components": [
                    {
                        "id": "a",
                        "type": "inset_map",
                        "position": "top-left",
                        "placement": {"mode": "anchor", "anchor": "bottom-right"},
                    },
                    {"id": "b", "type": "inset_map", "position": "top-left"},
                    {"id": "c", "type": "inset_map"},
                ]
            }
        }
        by_id = {c.id: c for c in resolve_components(spec)}
        assert by_id["a"].anchor == "bottom-right"  # placement anchor 优先
        assert by_id["b"].anchor == "top-left"  # 旧 position 次之
        assert by_id["c"].anchor == "top-right"  # 类型默认兜底

    def test_text_variant_layerid_extraction(self):
        spec = {
            "layout": {
                "components": [
                    {
                        "id": "t",
                        "type": "title",
                        "variant": "from-field",
                        "options": {
                            "text": "T",
                            "variant": "from-options",
                            "layerId": "L1",
                        },
                    }
                ]
            }
        }
        (comp,) = resolve_components(spec)
        assert comp.text == "T"
        assert comp.variant == "from-options"  # options.variant 优先
        assert comp.layer_id == "L1"

    def test_enabled_filter_left_to_consumer(self):
        spec = {"layout": {"components": [{"id": "x", "type": "legend", "enabled": False}]}}
        (comp,) = resolve_components(spec)
        assert comp.enabled is False  # 解析层不过滤，消费端决定
