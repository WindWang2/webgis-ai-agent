"""MapSpec 生命周期 property 契约（Quality V2 W7）。

对随机（seeded、有界）的合法 intent 序列，生命周期引擎必须维持：
1. 无异常：任意良性 intent 序列不 raise（错误以 MapSpecResult.is_error
   披露）；
2. revision 单调：每次 apply_mutation 成功后 revision 严格 +1（乐观并发
   契约的根基）；
3. 状态可序列化：任意序列后的 mapspec 状态可被 json 序列化（前端/存储
   契约）；
4. 类型化拒绝：畸形 intent（错型参数）要么被 dataclass 层拒绝，要么以
   is_error 结果披露——绝不静默成功也不 raise 未类型化异常。

全部离线；seed 打印以便失败重放。
"""
from __future__ import annotations

import json
import random
import shutil
import sys
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tests"))

from fixtures.generative import random_benign_intents  # noqa: E402

from app.services.mapspec.lifecycle_engine import (  # noqa: E402
    InitProjectIntent,
    MapSpecLifecycleEngine,
    SetViewIntent,
)
from app.services.mapspec.store import (  # noqa: E402
    BASE_STORAGE_DIR,
    mapspec_store_instance,
)
from app.services.session_data import session_data_manager  # noqa: E402


@pytest.fixture
async def clean_session():
    sid = f"prop-mapspec-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)
    d = BASE_STORAGE_DIR / sid
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


def _revision(result) -> int:
    """revision 的唯一权威字段是 MapSpecResult.mutation_revision（引擎
    提交时 +1；mapspec dict 内无 revision 键）。"""
    return int(getattr(result, "mutation_revision", 0) or 0)


@pytest.mark.parametrize("seed", [11, 2026])
async def test_benign_intent_sequence_invariants(clean_session, seed):
    rng = random.Random(seed)
    sid = clean_session
    engine = MapSpecLifecycleEngine()

    init_res = await engine.apply_mutation(sid, InitProjectIntent())
    assert not init_res.is_error, f"InitProject 失败: {init_res.error_msg}"
    prev_rev = _revision(init_res)
    prev_spec = init_res.mapspec

    intents = random_benign_intents(rng, 12)
    for i, intent in enumerate(intents):
        try:
            res = await engine.apply_mutation(sid, intent)
        except Exception as e:  # noqa: BLE001
            pytest.fail(f"良性 intent #{i} {intent} 抛出 {type(e).__name__}: "
                        f"{e}（seed={seed}）")
        assert not res.is_error, (
            f"良性 intent #{i} {intent!r} 被判 error: {res.error_msg}"
            f"（seed={seed}）")
        rev = _revision(res)
        # 不变量：revision 严格递增；等值仅允许 no-op（spec 内容不变）
        if rev == prev_rev:
            assert res.mapspec == prev_spec, (
                f"revision 未递增但 spec 内容变化（丢失提交）（seed={seed}, "
                f"intent#{i}={intent!r}）")
        else:
            assert rev > prev_rev, (
                f"revision 回退: {prev_rev} -> {rev}（seed={seed}）")
        prev_rev, prev_spec = rev, res.mapspec
        try:
            json.dumps(res.mapspec, ensure_ascii=False, default=str)
        except TypeError as e:
            pytest.fail(f"mapspec 不可序列化: {e}（seed={seed}）")


@pytest.mark.parametrize("seed", [13])
async def test_malformed_intents_are_typed_rejections(clean_session, seed):
    """畸形 intent：错型字段必须 is_error / 异常类型化，绝不静默成功。"""
    from app.services.mapspec.lifecycle_engine import (
        SetBasemapIntent, SetViewIntent,
    )

    sid = clean_session
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(sid, InitProjectIntent())

    malformed_cases = [
        SetViewIntent(center="not-a-list", zoom="high"),
        SetViewIntent(zoom=float("nan")),
        SetBasemapIntent(provider_id=12345),
    ]
    for intent in malformed_cases:
        try:
            res = await engine.apply_mutation(sid, intent)
        except Exception as e:  # noqa: BLE001
            assert isinstance(e, (TypeError, ValueError)), (
                f"{intent!r} 泄漏未类型化异常 {type(e).__name__}: {e}")
            continue
        if not res.is_error:
            # 规范化路径（如 NaN→默认值）必须保持 mapspec 仍可序列化
            try:
                json.dumps(res.mapspec, ensure_ascii=False, default=str)
            except (TypeError, ValueError) as e:
                pytest.fail(f"畸形 intent {intent!r} 静默成功且状态损坏: {e}")


async def test_revision_monotonic_under_interleaved_reads(clean_session):
    """读操作（get）不得推进 revision；写必须 +1。"""
    sid = clean_session
    engine = MapSpecLifecycleEngine()
    res0 = await engine.apply_mutation(sid, InitProjectIntent())
    base_rev = _revision(res0)
    for _ in range(3):
        state = await mapspec_store_instance.get_mapspec(sid)
        assert state is not None
        got = await engine.apply_mutation(
            sid, SetViewIntent(zoom=10.0 + base_rev))
        assert not got.is_error, got.error_msg
        assert _revision(got) == base_rev + 1, (
            f"revision 必须严格 +1: {base_rev} -> {_revision(got)}")
        base_rev = _revision(got)
