"""composition_selection W5 接线契约测试（V11 W0.3，ADR-0160，缺口 G1）。

缺口 G1：``select_composition_alternatives`` 零生产调用（07 线核心算法纸面）。
W0 定稿**调用契约**（``composition_alternatives_payload`` —— 唯一许可的
调用形态），用 golden fixture 锁定行为；W5 将其接入 component_composer
主链路时**必须**经由本契约（禁止绕过有界化直接序列化）。

锁定面：
- fixture 重放：同 context → 同载荷（candidate id 序 + count + 版本）；
- 确定性：两次调用逐字段相等；
- 有界性：candidates 数 ≤ MAX_ALTERNATIVES、字段与 V7 bounded 形状一致。
"""

import json
from pathlib import Path

import pytest

from app.lib.cartography.composition_selection import (

    COMPOSITION_ALTERNATIVES_VERSION,
    MAX_ALTERNATIVES,
    TaskCartographyContext,
    composition_alternatives_payload,
)

pytestmark = pytest.mark.cartography

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = (
    REPO_ROOT
    / "tests/cartography/golden_corpus/composition_selection"
    / "school_distribution.json"
)


@pytest.fixture(scope="module")
def fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def ctx(fixture) -> TaskCartographyContext:
    facts = fixture["context"]
    return TaskCartographyContext(
        task_categories=tuple(facts["task_categories"]),
        geometry_kind=facts["geometry_kind"],
        variable_kind=facts["variable_kind"],
        statistic=facts["statistic"],
        scale_hint=facts["scale_hint"],
        output_target=facts["output_target"],
        artifact_types=tuple(facts["artifact_types"]),
    )


def test_fixture_exists_and_versioned(fixture) -> None:
    assert FIXTURE.exists(), "接线契约 fixture 缺失"
    assert fixture["expected"]["version"] == COMPOSITION_ALTERNATIVES_VERSION


def test_payload_matches_fixture(ctx, fixture) -> None:
    """同 context → 同载荷（候选 id 序与计数锁定；W5 调参前先改 fixture）。"""
    payload = composition_alternatives_payload(ctx)
    assert payload["version"] == fixture["expected"]["version"]
    assert payload["count"] == fixture["expected"]["count"]
    got_ids = [(c["mapModel"], c["composition"]) for c in payload["candidates"]]
    want_ids = [
        (c["mapModel"], c["composition"]) for c in fixture["expected"]["candidates"]
    ]
    assert got_ids == want_ids


def test_payload_deterministic(ctx) -> None:
    assert composition_alternatives_payload(ctx) == composition_alternatives_payload(ctx)


def test_payload_bounded(ctx) -> None:
    payload = composition_alternatives_payload(ctx)
    assert 1 <= payload["count"] <= MAX_ALTERNATIVES
    for cand in payload["candidates"]:
        assert set(cand) == {
            "mapModel", "composition", "spec", "category",
            "score", "reasons", "slots", "disclosures",
        }
        assert len(cand["reasons"]) <= 6


def test_structured_input_only() -> None:
    """契约面拒绝 query 形态：TaskCartographyContext 无 query 字段。"""
    assert "query" not in TaskCartographyContext.model_fields
