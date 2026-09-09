"""解析器 property 契约（Quality V2 W7）——对任意输入的**异常类型**边界。

property 语义（不做数值 oracle，只锁崩溃面）：
1. ``parse_bbox``：任意字符串 → ValueError 或合法 4 值 bbox；绝不泄漏
   其他异常类型（AttributeError/TypeError/OverflowError…）；
2. ``safe_parse``：任意 Python 对象 / 任意字符串 → dict/list/None；
   绝不 raise；对合法 GeoJSON 幂等（parse(parse 结果) 不变）；
3. 全部 fuzz 由 seeded random 驱动（失败重放：输出里的 seed），
   迭代数有界，无网络无大对象。
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tests"))

from fixtures.generative import (  # noqa: E402
    fuzz_inputs,
    random_bbox_string,
    random_feature_collection,
    random_polygon_ring,
    random_position,
)

from app.tools._utils import parse_bbox  # noqa: E402


@pytest.mark.parametrize("seed", [1, 42, 2026, 987654321])
def test_parse_bbox_contract(seed):
    rng = random.Random(seed)
    for _ in range(200):
        raw = random_bbox_string(rng)
        try:
            result = parse_bbox(raw)
        except ValueError:
            continue
        except Exception as e:  # noqa: BLE001
            pytest.fail(
                f"parse_bbox({raw!r}) 泄漏非 ValueError 异常 "
                f"{type(e).__name__}: {e}（seed={seed}）")
        assert isinstance(result, list) and len(result) == 4
        west, south, east, north = result
        assert west < east and south < north, f"parse_bbox({raw!r}) 返回退化框"
        assert all(-180 <= v <= 180 for v in (west, east))
        assert all(-90 <= v <= 90 for v in (south, north))


def test_parse_bbox_valid_formats_accepted():
    assert parse_bbox("[116.2, 39.7, 116.6, 40.1]") == \
        [116.2, 39.7, 116.6, 40.1]
    assert parse_bbox("(116.2,39.7,116.6,40.1)") == \
        [116.2, 39.7, 116.6, 40.1]
    assert parse_bbox("116.2, 39.7, 116.6, 40.1") == \
        [116.2, 39.7, 116.6, 40.1]


@pytest.mark.parametrize("seed", [7, 99])
def test_safe_parse_never_raises(seed):
    from app.lib.geo_processor.core import safe_parse

    rng = random.Random(seed)
    arbitrary = [
        None, 42, 3.14, True, 10 ** 40, True and not False, {"a": 1},
        float("nan"), float("inf"), -0.0, "",
        "not json", "{truncated", "[]", '"bare string"',
        json.dumps(random_feature_collection(rng)),
    ]
    for value in arbitrary:
        out = safe_parse(value)
        assert out is None or isinstance(out, (dict, list)), (
            f"safe_parse({value!r}) 返回 {type(out).__name__}（seed={seed}）")


@pytest.mark.parametrize("seed", [7, 99, 12345])
def test_safe_parse_fuzz_mutations_never_raise(seed):
    from app.lib.geo_processor.core import safe_parse

    rng = random.Random(seed)
    for mutated in fuzz_inputs(rng,
                               lambda: random_feature_collection(rng, 2), 120):
        try:
            out = safe_parse(mutated)
        except Exception as e:  # noqa: BLE001
            pytest.fail(
                f"safe_parse 对变异输入 {str(mutated)[:120]!r} 抛出 "
                f"{type(e).__name__}: {e}（seed={seed}）")
        assert out is None or isinstance(out, (dict, list))


def test_safe_parse_valid_geojson_idempotent():
    from app.lib.geo_processor.core import safe_parse

    rng = random.Random(2026)
    for _ in range(25):
        fc = random_feature_collection(rng, 3)
        once = safe_parse(json.dumps(fc))
        assert once == fc
        twice = safe_parse(once)
        assert twice == fc, "parse(parse(x)) 必须幂等"


def test_polygon_ring_contract():
    """生成器自身契约：环闭合（首尾点相同）、坐标为窗口内浮点。"""
    rng = random.Random(5)
    for _ in range(20):
        ring = random_polygon_ring(rng)
        assert ring[0] == ring[-1]
        assert all(len(p) == 2 for p in ring)
        assert all(isinstance(v, float) for p in ring for v in p)
        for x, y in ring:
            assert 115.0 <= x <= 118.0 and 38.5 <= y <= 41.5


def test_position_in_window():
    rng = random.Random(6)
    for _ in range(30):
        x, y = random_position(rng)
        assert 116.0 <= x <= 117.0 and 39.5 <= y <= 40.5
