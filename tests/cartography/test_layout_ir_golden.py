"""C2 LayoutDescription IR golden 对拍 —— Python 侧（V11 W0.1，ADR-0160）。

与 ``frontend/lib/layout/ir.golden.test.ts`` 消费**同一份** fixture：
expected 由 Python 权威实现（build_layout_ir）生成冻结，TS 镜像须逐字段
复现；本测试同时锁定：装配确定性、结构校验 fail-closed、层级 tie-break。

parity 骨架（W0 定稿）：W6 三渲染器收敛时，本 IR 是唯一输入 —— 渲染器
等价性测试在 W6 落地，本测试锁定 IR 本身的跨语言一致性。
"""

import json
from pathlib import Path

import pytest

from app.lib.cartography.layout_description import (

    LAYOUT_IR_VERSION,
    build_layout_ir,
    validate_layout_ir,
)

pytestmark = pytest.mark.cartography

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "tests/cartography/golden_corpus/layout_ir/basic.json"


@pytest.fixture(scope="module")
def fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_fixture_versioned(fixture) -> None:
    assert fixture["expected"]["version"] == LAYOUT_IR_VERSION


def test_ir_matches_fixture(fixture) -> None:
    """同输入 → 同 IR（逐字段；TS 镜像消费同一 expected）。"""
    ir = build_layout_ir(
        canvas=fixture["input"]["canvas"],
        components=fixture["input"]["components"],
        degradations=fixture["input"]["degradations"],
    )
    assert ir == fixture["expected"]


def test_ir_deterministic(fixture) -> None:
    kwargs = dict(
        canvas=fixture["input"]["canvas"],
        components=fixture["input"]["components"],
        degradations=fixture["input"]["degradations"],
    )
    assert build_layout_ir(**kwargs) == build_layout_ir(**kwargs)


def test_layers_z_ascending_with_id_tiebreak(fixture) -> None:
    ir = fixture["expected"]
    zs = [layer["z"] for layer in ir["layers"]]
    assert zs == sorted(zs)
    # z=30 的并列层按 id 字典序稳定排（跨语言 tie-break 契约）
    tied = [layer["id"] for layer in ir["layers"] if layer["z"] == 30]
    assert tied == sorted(tied) and len(tied) == 2


def test_validate_accepts_fixture_ir(fixture) -> None:
    assert validate_layout_ir(fixture["expected"]) == []


def _valid_minimal_ir() -> dict:
    return build_layout_ir(
        canvas={"widthPx": 100, "heightPx": 100},
        components=[
            {"id": "c1", "kind": "title", "role": "secondary",
             "frame": {"x": 0, "y": 0, "width": 40, "height": 10,
                       "anchor": "top_left", "z": 1}},
        ],
    )


@pytest.mark.parametrize("mutate,fragment", [
    (lambda ir: ir.update({"version": 1}), "version"),
    (lambda ir: ir["components"].append({
        "id": "c1", "kind": "legend", "role": "decorative",
        "frame": {"x": 0, "y": 0, "width": 1, "height": 1,
                  "anchor": "top_left", "z": 2}}), "id 重复"),
    (lambda ir: ir["components"][0].update({"kind": "unicorn"}), "kind 非法"),
    (lambda ir: ir["components"][0]["frame"].update({"x": -5}), "非负"),
    (lambda ir: ir["components"][0]["frame"].update({"width": 999}), "超出画布"),
    (lambda ir: ir["components"][0]["frame"].pop("anchor"), "anchor 非法"),
    (lambda ir: ir["canvas"].update({"widthPx": 0}), "widthPx"),
])
def test_validate_rejects_malformed(mutate, fragment) -> None:
    ir = _valid_minimal_ir()
    mutate(ir)
    issues = validate_layout_ir(ir)
    assert issues and any(fragment in msg for msg in issues)


def test_validate_bounds_component_count() -> None:
    components = [
        {"id": f"c{i}", "kind": "text_note", "role": "decorative",
         "frame": {"x": 0, "y": 0, "width": 1, "height": 1,
                   "anchor": "top_left", "z": i}}
        for i in range(33)
    ]
    ir = build_layout_ir(canvas={"widthPx": 100, "heightPx": 100},
                         components=components)
    assert any("上界" in msg for msg in validate_layout_ir(ir))
