"""生成式测试 harness（Quality V2 W7）——seeded、有界、零新依赖。

设计约束：
- 确定性：所有生成器吃一个 ``random.Random(seed)``；同 seed 同输入序列，
  失败可精确重放（测试输出打印 seed）；
- 有界：调用方控制迭代数；生成器本身对嵌套深度/元素个数有硬上限；
- 零依赖：不用 hypothesis（资源/依赖纪律），生成策略显式可读。

失败样本治理：fuzz 中发现未按契约处理的输入 → 缩减为最小样本放入
``tests/fixtures/fuzz_corpus/``（test_fuzz_parsers.py 逐条回归）。
"""

from __future__ import annotations

import random
import string
from typing import Any, Callable, Iterable, List

MAX_DEPTH = 4
MAX_ITEMS = 8

# ── GeoJSON ──────────────────────────────────────────────────────────────

_COORD_RANGE = (116.0, 117.0, 39.5, 40.5)  # 北京附近小窗口，避免投影越界


def random_position(rng: random.Random) -> List[float]:
    w, e, s, n = _COORD_RANGE
    return [round(rng.uniform(w, e), 6), round(rng.uniform(s, n), 6)]


def random_polygon_ring(rng: random.Random, n: int = 5) -> List[List[float]]:
    cx, cy = random_position(rng)
    ring = []
    for i in range(n):
        angle = 2 * 3.14159265 * i / n
        ring.append(
            [
                round(cx + 0.01 * (1 + rng.random()) * (angle**0 + 1) * _orbit(rng), 6),
                round(cy + 0.01 * (1 + rng.random()) * _orbit(rng), 6),
            ]
        )
    ring.append(ring[0])  # 闭合环
    return ring


def _orbit(rng: random.Random) -> float:
    return rng.choice([-1.0, 1.0]) * (0.5 + rng.random())


def random_geometry(rng: random.Random) -> dict:
    kind = rng.choice(["Point", "Polygon"])
    if kind == "Point":
        return {"type": "Point", "coordinates": random_position(rng)}
    return {"type": "Polygon", "coordinates": [random_polygon_ring(rng)]}


def random_feature(rng: random.Random) -> dict:
    return {
        "type": "Feature",
        "geometry": random_geometry(rng),
        "properties": {
            "value": rng.uniform(0, 100),
            "name": "".join(rng.choices(string.ascii_lowercase, k=6)),
        },
    }


def random_feature_collection(rng: random.Random, n: int | None = None) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            random_feature(rng) for _ in range(n or rng.randint(1, MAX_ITEMS))
        ],
    }


# ── 变异（fuzz 用：合法结构的邻居） ──────────────────────────────────────

_MUTATION_ATOMS: tuple = (
    "drop_key",
    "wrong_type",
    "nan_coord",
    "deep_nest",
    "huge_number",
    "empty_string",
    "null_inject",
    "negative_size",
    "unicode_bomb",
    "truncated_json",
    "extra_keys",
)


def _scalars(rng: random.Random) -> list:
    return [
        None,
        True,
        False,
        0,
        -1,
        1.5,
        "",
        "ref:abc",
        "NaN",
        "Infinity",
        10**30,
        -(10**30),
        float("nan"),
        float("inf"),
        "乱序" + chr(rng.randint(0x2000, 0x2FFF)),
    ]


def mutate_value(rng: random.Random, value: Any, depth: int = 0) -> Any:
    """随机深度变异：结构破坏 + 畸形标量，用于 parser/fuzz 契约测试。"""
    atom = rng.choice(_MUTATION_ATOMS)
    if atom == "drop_key" and isinstance(value, dict) and value:
        out = dict(value)
        out.pop(rng.choice(list(out)))
        return out
    if atom == "wrong_type" and isinstance(value, dict):
        return rng.choice([[], "str", 42, None])
    if atom == "nan_coord" and isinstance(value, dict):
        return {**value, "coordinates": [float("nan"), float("inf")]}
    if atom == "deep_nest" and depth < MAX_DEPTH:
        return {"nested": [mutate_value(rng, value, depth + 1)]}
    if atom == "huge_number":
        return 10 ** rng.randint(10, 40)
    if atom == "empty_string":
        return ""
    if atom == "null_inject" and isinstance(value, list):
        out = list(value)
        out.insert(rng.randint(0, len(out)), None)
        return out
    if atom == "negative_size" and isinstance(value, list):
        return value[: -rng.randint(1, max(1, len(value)))]
    if atom == "unicode_bomb":
        return "".join(chr(rng.randint(0, 0x10FFFF - 2048)) for _ in range(8))
    if atom == "truncated_json" and isinstance(value, dict):
        import json

        text = json.dumps(value, default=str)
        return text[: max(1, len(text) // 2)]
    if atom == "extra_keys" and isinstance(value, dict):
        return {**value, "__proto__": {"x": 1}, "\x00key": rng.random()}
    return rng.choice(_scalars(rng))


def fuzz_inputs(
    rng: random.Random, base: Callable[[], Any], count: int
) -> Iterable[Any]:
    for _ in range(count):
        yield mutate_value(rng, base())


# ── bbox 字符串 ──────────────────────────────────────────────────────────


def random_bbox_string(rng: random.Random) -> str:
    w = rng.uniform(-179, 178)
    s = rng.uniform(-89, 88)
    return rng.choice(
        [
            f"[{w}, {s}, {w + 1}, {s + 1}]",
            f"({w:.3f},{s:.3f},{w + 0.5:.3f},{s + 0.5:.3f})",
            f"{w},{s},{w + 2},{s + 2}",
            f"[{s}, {w}, {s + 1}, {w + 1}]",  # 语义上经纬互换（应由解析拒绝）
            "not,a,bbox",
            "[1,2,3]",
            "[]",
            "1;2;3;4",
        ]
    )


# ── 缓存参数树 ───────────────────────────────────────────────────────────


def random_cache_args(rng: random.Random, depth: int = 0) -> dict:
    args: dict = {}
    for i in range(rng.randint(1, 5)):
        kind = rng.choice(["num", "str", "bool", "list", "nested", "ref"])
        key = f"k{i}_{kind}"
        if kind == "num":
            args[key] = rng.uniform(-100, 100)
        elif kind == "str":
            args[key] = "".join(rng.choices(string.ascii_letters, k=5))
        elif kind == "bool":
            args[key] = rng.choice([True, False])
        elif kind == "list":
            args[key] = [rng.randint(0, 9) for _ in range(rng.randint(0, 4))]
        elif kind == "nested" and depth < 3:
            args[key] = random_cache_args(rng, depth + 1)
        else:
            args[key] = f"ref:{uuid_hex(rng)}"
    return args


def uuid_hex(rng: random.Random) -> str:
    return "".join(rng.choices("0123456789abcdef", k=8))


# ── MapSpec intents ─────────────────────────────────────────────────────


def random_benign_intents(rng: random.Random, count: int) -> List[Any]:
    """对既有 state 恒为合法的 intent 序列（生命周期不变量测试用）。"""
    from app.services.mapspec.lifecycle_engine import (
        SetBasemapIntent,
        SetLayoutIntent,
        SetTimeIntent,
        SetViewIntent,
    )

    out: List[Any] = []
    for _ in range(count):
        kind = rng.choice(["view", "layout", "time", "basemap"])
        if kind == "view":
            out.append(
                SetViewIntent(
                    center=[random_position(rng)[0], random_position(rng)[1]],
                    zoom=rng.uniform(1, 18),
                    bearing=rng.uniform(0, 360),
                    pitch=rng.uniform(0, 60),
                )
            )
        elif kind == "layout":
            out.append(
                SetLayoutIntent(
                    legend={"visible": rng.choice([True, False])},
                    margins={"top": rng.randint(10, 60), "right": rng.randint(10, 60)},
                )
            )
        elif kind == "time":
            out.append(
                SetTimeIntent(
                    enabled=rng.choice([True, False]),
                    field=rng.choice(["ts", "date"]),
                    step=rng.uniform(0.1, 5.0),
                )
            )
        else:
            out.append(
                SetBasemapIntent(
                    provider_id=rng.choice(["dark", "streets", "satellite"]),
                )
            )
    return out
