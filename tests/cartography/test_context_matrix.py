"""6 上下文 × 18 色带 golden 矩阵（V11 W2.5，ADR-0162）。

- 判定口径与 ``resolve_symbology`` 完全同源（``context_matrix`` 复用
  symbology 的常量与变换）—— 矩阵与裁决不各说各话；
- golden 冻结 108 格（任务书 96 组为 16 色带估算，注册表实测 18 条，
  §0.5 以代码为准）；fail 格是**冻结的已知集合**（对应上下文由
  resolve_symbology 自然落选/换带，ADR-0152 语义），本测试锁定该集合
  不悄悄变大；
- 注册门（W2.5）：新色带必须过 :func:`validate_new_palette` 全上下文。
"""

import json
from pathlib import Path

import pytest

from app.lib.cartography.context_matrix import (
    evaluate_cell,
    evaluate_matrix,
    failing_cells,
    validate_new_palette,
)
from app.lib.cartography.palettes import sample_ramp_colors
from app.lib.cartography.symbology import resolve_symbology, SymbologyIntent, SymbologyProfile

pytestmark = pytest.mark.cartography

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN = REPO_ROOT / "tests/cartography/golden_corpus/context_matrix/matrix.json"


@pytest.fixture(scope="module")
def golden() -> dict:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def test_matrix_matches_frozen_golden(golden) -> None:
    """byte 语义级冻结：任何格判定变化都必须显式重生成 golden。"""
    current = evaluate_matrix()
    assert current == golden


def test_matrix_shape_and_contexts(golden) -> None:
    assert golden["version"] == 1
    assert len(golden["cells"]) == 6 * 18
    assert set(golden["contexts"]) == {
        "screen", "projector", "print",
        "cvd_deuteranopia", "cvd_protanopia", "cvd_tritanopia",
    }
    assert golden["summary"]["unavailable"] == 0
    assert golden["summary"]["pass"] + golden["summary"]["fail"] == 108


def test_failing_set_is_frozen_and_disclosed(golden) -> None:
    """fail 集必须与 golden 记载一致（防「悄悄变大」；变好需显式重生成）。"""
    fails = {(c["context"], c["palette"]) for c in failing_cells(golden)}
    assert len(fails) == golden["summary"]["fail"] == 27
    # 冻结集抽样锁定（浅色低饱和系与红绿系的固有属性，非回归）
    for pair in (
        ("cvd_deuteranopia", "RdYlGn"),
        ("cvd_protanopia", "Set1"),
        ("print", "Pastel1"),
        ("screen", "Pastel1"),
    ):
        assert pair in fails


def test_matrix_agrees_with_adjudication(golden) -> None:
    """矩阵 ↔ 裁决同源抽检：fail 格的色带在对应上下文不被 resolve_symbology
    选中（screen 上 Pastel1 fail → 决议落其它色带）。"""
    failing_palette = next(
        c["palette"] for c in failing_cells(golden) if c["context"] == "screen"
    )
    decision = resolve_symbology(
        SymbologyProfile(), intent=SymbologyIntent(context="screen"),
    )
    assert decision.palette != failing_palette
    # 对照：全上下文通过的色带（Viridis）在其 pass 格上可被裁决选中
    viridis_pass = all(
        c["verdict"] == "pass"
        for c in golden["cells"] if c["palette"] == "Viridis"
    )
    assert viridis_pass


def test_register_gate_rejects_indistinguishable_ramp() -> None:
    """注册门：不可分辨 ramp 全上下文拒绝；感知均匀 ramp 全上下文通过。"""
    bad = ["#ff0000"] * 5
    assert set(validate_new_palette(bad)) == {
        "screen", "projector", "print",
        "cvd_deuteranopia", "cvd_protanopia", "cvd_tritanopia",
    }
    good = sample_ramp_colors("Viridis", 5)
    assert validate_new_palette(good) == []


def test_evaluate_cell_rejects_unknown_context() -> None:
    with pytest.raises(ValueError):
        evaluate_cell("Viridis", "thermal")
