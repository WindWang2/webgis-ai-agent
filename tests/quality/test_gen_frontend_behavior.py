"""scripts/gen_frontend_behavior.py scan() 闸门语义回归。

``_MAX_TEST_FILES`` 闸必须数**测试文件数**，不是 behavior id 数：
history: 该闸曾判 ``len(behaviors)``（去重 behavior id 数），>800 个
behavior id 即在文件数远未达上限时 break，行为证据索引静默缺尾
（P3 data-integrity，scripts/gen_frontend_behavior.py:43）。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts/gen_frontend_behavior.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "gen_frontend_behavior_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def gfb(tmp_path, monkeypatch):
    """加载被测脚本，扫描面重定向到 tmp 假 frontend（不碰真实仓库）。"""
    mod = _load_module()
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    monkeypatch.setattr(mod, "REPO", tmp_path)
    monkeypatch.setattr(mod, "FRONTEND", frontend)
    monkeypatch.setattr(mod, "_MAX_TEST_FILES", 2)
    return mod, frontend


def test_max_test_files_gate_counts_files_not_behavior_ids(gfb):
    """文件数未超闸时，behavior id 数超过 _MAX_TEST_FILES 也不得截断；
    文件数超闸时截断仍生效。"""
    mod, frontend = gfb
    # 第 1 个文件即携带 3 个 behavior id（> 闸值 2），但只算 1 个测试文件
    (frontend / "a.test.ts").write_text(
        "// @behavior:alpha\n// @behavior:beta\n// @behavior:gamma\n"
        "it('t-alpha', () => {})\n", encoding="utf-8")
    # 第 2 个文件：文件数仍在闸内，其行为证据必须进索引
    (frontend / "b.test.ts").write_text(
        "// @behavior:delta\nit('t-delta', () => {})\n", encoding="utf-8")
    # 第 3 个文件：文件数超闸，截断必须仍然生效（只警告、不静默）
    (frontend / "c.test.ts").write_text(
        "// @behavior:epsilon\nit('t-epsilon', () => {})\n", encoding="utf-8")

    behaviors = mod.scan()

    assert "delta" in behaviors, (
        "文件数未达上限却截断：_MAX_TEST_FILES 闸判成了 behavior id 数")
    assert behaviors["delta"][0]["tests"] == ["t-delta"]
    assert sorted(behaviors) == ["alpha", "beta", "delta", "gamma"]
    assert "epsilon" not in behaviors, "文件数超上限后截断失效"
