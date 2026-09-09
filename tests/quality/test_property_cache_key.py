"""缓存键 property 契约（Quality V2 W7）。

``make_cache_key`` 的可缓存性/正确性边界，对 seeded 随机参数树：
1. 确定性：同 (tool, args, scope) → 同键；键序不敏感（canonical JSON）；
2. 区分性：不同 args → 不同键（弱断言：随机树上几乎必然）；
3. 安全性：含 ``ref:`` 叶子 → None（会话内可变引用禁止跨时间共享）；
   owner 域隔离：不同 session_id → 不同键（跨用户时序侧信道防线）；
4. 健壮性：任意变异 args 永不 raise（返回键或 None）。
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tests"))

from fixtures.generative import (  # noqa: E402
    fuzz_inputs,
    random_cache_args,
)

from app.lib.tool_cache import make_cache_key  # noqa: E402


def test_same_args_same_key():
    rng = random.Random(21)
    for _ in range(50):
        args = random_cache_args(rng)
        args = {k: v for k, v in args.items() if v != f"ref:{v}"}
        # 过滤掉 ref 叶子（ref → None 是另一条契约）
        clean = _strip_refs(args)
        if clean is None:
            continue
        a = make_cache_key("buffer_analysis", clean)
        b = make_cache_key("buffer_analysis", clean)
        assert a == b
        assert a is not None


def _strip_refs(value):
    """移除含 ref: 叶子的键；全空返回 None。"""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if isinstance(v, str) and v.startswith("ref:"):
                continue
            stripped = _strip_refs(v)
            out[k] = stripped
        return out or None
    if isinstance(value, list):
        return [x for x in value if not (isinstance(x, str) and x.startswith("ref:"))]
    return value


def test_key_order_insensitive():
    args = {"a": 1, "b": [1, 2], "c": {"d": True, "e": "x"}}
    k1 = make_cache_key("t", args)
    k2 = make_cache_key("t", {"c": {"e": "x", "d": True}, "b": [1, 2], "a": 1})
    assert k1 == k2, "canonical JSON：键序不影响键值"


def test_different_args_different_keys():
    rng = random.Random(22)
    seen = {}
    for _ in range(60):
        args = _strip_refs(random_cache_args(rng))
        if not args:
            continue
        key = make_cache_key("t", args)
        assert key is not None
        canonical = repr(sorted(args.items()))
        if canonical in seen:
            assert seen[canonical] == key
        seen[canonical] = key
    # 弱区分断言：60 棵随机树产生的键去重后应接近 60
    assert len(set(seen.values())) >= len(seen) * 0.95


def test_ref_leaves_disable_cache():
    assert make_cache_key("t", {"layer": "ref:abc123"}) is None
    assert make_cache_key("t", {"nested": {"deep": ["ref:x"]}}) is None
    assert make_cache_key("t", {"ok": 1}) is not None


def test_owner_scope_isolates_sessions():
    """同 args 不同 owner 域 → 不同键（跨用户时序侧信道防线）。"""
    args = {"buffer": 100, "unit": "m"}
    k_anon = make_cache_key("t", args, owner_scope="anonymous")
    k_a = make_cache_key("t", args, owner_scope="s:aaaa")
    k_b = make_cache_key("t", args, owner_scope="s:bbbb")
    assert len({k_anon, k_a, k_b}) == 3
    # args 注入 session_id 时自动派生 owner 域
    k1 = make_cache_key("t", {"session_id": "sess-1", "z": 1})
    k2 = make_cache_key("t", {"session_id": "sess-2", "z": 1})
    assert k1 != k2


@pytest.mark.parametrize("seed", [31, 77])
def test_mutated_args_never_raise(seed):
    rng = random.Random(seed)
    count = 0
    for mutated in fuzz_inputs(rng, lambda: random_cache_args(rng), 80):
        count += 1
        try:
            out = make_cache_key("t", mutated)
        except Exception as e:  # noqa: BLE001
            pytest.fail(
                f"make_cache_key 对 {str(mutated)[:100]!r} 抛出 "
                f"{type(e).__name__}: {e}（seed={seed}）"
            )
        assert out is None or (
            isinstance(out, str) and out.startswith("tool_cache:v2:")
        )
    assert count == 80


def test_tool_name_participates_in_key():
    args = {"x": 1}
    assert make_cache_key("t1", args) != make_cache_key("t2", args)
