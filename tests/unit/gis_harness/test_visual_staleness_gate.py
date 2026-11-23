"""C13 stale 观测硬门回归（证据新鲜度 = 修复面前的第一道门）。

不变式：
1. ``latest_screenshot_for`` 严格匹配：revision/指纹任一不一致 → None
   （绝不回退旧截图 —— 旧像素归因新 spec = 跨代误修复入口）；
2. provider ``_eval_rules`` 第二道门（纵深）：screenshot 与观察的
   revision/指纹不一致 → ``screenshot_revision_mismatch`` /
   ``screenshot_fingerprint_mismatch`` 诚实缺席，不跑像素判据；
3. UnifiedFinding.observed_revision 随 to_dict 流动（修复面的新鲜度尺）。
"""

from __future__ import annotations

import io
import uuid

import pytest

from app.services.gis_harness.completion.unified_findings import UnifiedFinding
from app.services.gis_harness.visual_observation.contracts import (
    VisualObservationInput,
    VisualScreenshotRef,
)
from app.services.gis_harness.visual_observation.provider import (
    evaluate_observation,
)
from app.services.gis_harness.visual_observation.store import (
    SCREENSHOT_INDEX_KEY,
    latest_screenshot_for,
    register_visual_screenshot,
)
from app.services.session_data import session_data_manager

_FP_A = "carto-sha256:" + "a" * 64
_FP_B = "carto-sha256:" + "b" * 64


@pytest.fixture
def session_id():
    return f"vstale-{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
async def _clean_session(session_id):
    await session_data_manager.clear_session(session_id)
    yield
    await session_data_manager.clear_session(session_id)


def _ref(revision: int, fingerprint: str = "") -> VisualScreenshotRef:
    return VisualScreenshotRef(
        ref="vshot-deadbeef", sha256="ab" * 32, size=10,
        width=8, height=8, mapspec_revision=revision,
        mapspec_fingerprint=fingerprint,
    )


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    from PIL import Image

    Image.new("RGB", (8, 8), (10, 10, 10)).save(buf, "PNG")
    return buf.getvalue()


# ── store 第一道门 ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_latest_screenshot_requires_exact_revision(session_id):
    await register_visual_screenshot(
        session_id, _png_bytes(), mapspec_revision=3,
        mapspec_fingerprint=_FP_A)
    # 同 revision 同指纹 → 命中。
    hit = await latest_screenshot_for(session_id, 3, mapspec_fingerprint=_FP_A)
    assert hit is not None and hit.mapspec_revision == 3
    # 更新后的 revision 无截图 → 绝不回退旧图。
    assert await latest_screenshot_for(
        session_id, 4, mapspec_fingerprint=_FP_A) is None
    # 指纹未知（调用方没算出指纹）→ 退化为 revision 单尺（仍不回退）。
    legacy = await latest_screenshot_for(session_id, 3)
    assert legacy is not None


@pytest.mark.asyncio
async def test_latest_screenshot_fingerprint_mismatch_is_stale(session_id):
    """同 revision 但指纹漂移 = 状态被替换/回滚 → 旧图不可用。"""
    await register_visual_screenshot(
        session_id, _png_bytes(), mapspec_revision=5,
        mapspec_fingerprint=_FP_A)
    assert await latest_screenshot_for(
        session_id, 5, mapspec_fingerprint=_FP_B) is None
    assert await latest_screenshot_for(
        session_id, 5, mapspec_fingerprint=_FP_A) is not None


@pytest.mark.asyncio
async def test_latest_screenshot_legacy_entries_without_fingerprint(session_id):
    """旧索引条目（指纹空串）在调用方带指纹时按「指纹未知」放行 ——
    门不惩罚历史数据，但 revision 单尺依然强制。"""
    await session_data_manager.set_map_state(session_id, SCREENSHOT_INDEX_KEY, [{
        "ref": "vshot-deadbeef", "sha256": "ab" * 32, "size": 10,
        "width": 8, "height": 8, "mapspec_revision": 2,
    }])
    hit = await latest_screenshot_for(session_id, 2, mapspec_fingerprint=_FP_A)
    assert hit is not None and hit.mapspec_fingerprint == ""
    assert await latest_screenshot_for(
        session_id, 3, mapspec_fingerprint=_FP_A) is None


# ── provider 第二道门 ─────────────────────────────────────────────────────

def _obs(revision: int, fingerprint: str, ref: VisualScreenshotRef):
    return VisualObservationInput(
        trigger="finalization", session_id="s1",
        mapspec_revision=revision, mapspec_fingerprint=fingerprint,
        screenshot=ref,
    )


def test_provider_rejects_revision_mismatch():
    result = evaluate_observation(_obs(7, _FP_A, _ref(6, _FP_A)))
    assert not result.evaluated
    assert result.reason == "screenshot_revision_mismatch"


def test_provider_rejects_fingerprint_mismatch():
    result = evaluate_observation(_obs(7, _FP_A, _ref(7, _FP_B)))
    assert not result.evaluated
    assert result.reason == "screenshot_fingerprint_mismatch"


def test_provider_accepts_matching_screenshot_shape():
    """revision/指纹一致（即便 blob 解析失败）→ 进入解析面，缺席原因
    是 ``screenshot_unresolvable`` 而非 mismatch —— 门不越权。"""
    result = evaluate_observation(_obs(7, _FP_A, _ref(7, _FP_A)))
    assert not result.evaluated
    assert result.reason == "screenshot_unresolvable"


def test_provider_legacy_snapshot_without_fingerprint_still_gated():
    """旧 snapshot（指纹空串）→ 指纹门退化，revision 门依然强制。"""
    assert not evaluate_observation(
        _obs(7, "", _ref(6, ""))).evaluated
    result = evaluate_observation(_obs(7, "", _ref(7, "")))
    assert result.reason == "screenshot_unresolvable"


def test_provider_snapshot_without_session_id_is_refused():
    """review P2-13：快照无 session_id 不得 fail-open 跳过 C13 会话绑定护栏。

    旧行为把空串传给 resolve_visual_screenshot —— 解析面的所有权门整体
    跳过，任意 vshot ref 无所有权证据即可读出字节。
    """
    obs = VisualObservationInput(
        trigger="finalization", session_id="",
        mapspec_revision=7, mapspec_fingerprint=_FP_A,
        screenshot=_ref(7, _FP_A),
    )
    result = evaluate_observation(obs)
    assert not result.evaluated
    assert result.reason == "screenshot_session_unbound"


# ── observed_revision 流动 ────────────────────────────────────────────────

def test_split_fresh_findings_treats_missing_revision_as_stale():
    """review P2-16：缺 observed_revision（0）的旧证据不得混过新鲜度门。

    ``_cartographic_mutation_revision`` 缺失（current=0）时，0==0 会让缺
    观测号的旧证据被判为 fresh —— 违背「缺省按过期处理」。
    """
    from app.services.gis_harness.visual_observation.stored_findings import (
        split_fresh_findings,
    )

    legacy = [{"code": "visual_overlap"}]  # 无 observed_revision 键
    fresh, stale = split_fresh_findings(legacy, 0)
    assert fresh == [] and stale == 1
    fresh, stale = split_fresh_findings(
        [{"code": "visual_overlap", "observed_revision": 0}], 0)
    assert fresh == [] and stale == 1
    stamped = [{"code": "visual_overlap", "observed_revision": 7}]
    fresh, stale = split_fresh_findings(stamped, 7)
    assert len(fresh) == 1 and stale == 0
    fresh, stale = split_fresh_findings(stamped, 8)
    assert fresh == [] and stale == 1


def test_unified_finding_observed_revision_round_trips():
    uf = UnifiedFinding(
        domain="visual", code="visual_overlap", severity="warning",
        observed_revision=11,
    )
    raw = uf.to_dict()
    assert raw["observed_revision"] == 11
    # 缺省 = 0（未知/旧数据），修复面保守按过期处理。
    assert UnifiedFinding(
        domain="visual", code="visual_overlap",
        severity="warning").to_dict()["observed_revision"] == 0
