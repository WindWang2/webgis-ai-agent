"""#1216 类回归：quality_runner changed-lane 映射键必须真实匹配。

#1216（audit3 D-5）：`"/".join(parts[:3])` 对两段键（app/tools、app/services、
app/api、app/core）永不匹配 —— 映射键存在但永不命中，主应用面改动在 changed
lane 静默失去测试映射。本测试通过 monkeypatch subprocess.run 伪造 git diff，
对 scripts/quality_runner.py 的真实映射函数断言每个键都能命中其目标目录下的
代表文件（键漂移/匹配逻辑退化即红）。

注意：不 import app 包（quality_runner 是 scripts/ 独立脚本），用 importlib
按路径加载；不改 scripts/quality_runner.py 本身。
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
QR_PATH = REPO / "scripts" / "quality_runner.py"


def _load_qr() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("quality_runner_under_test", QR_PATH)
    mod = importlib.util.module_from_spec(spec)
    # 脚本按 __main__ 之外的方式加载不应执行 main()
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def qr(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    mod = _load_qr()
    # 让 (REPO / mapped).is_dir() 走真实仓库 —— tests/unit 等目录存在。
    return mod


def _patch_git(monkeypatch: pytest.MonkeyPatch, files: list[str]) -> None:
    def fake_run(args, **kwargs):  # noqa: ANN001, ANN003
        out = types.SimpleNamespace()
        out.stdout = "\n".join(files) + ("\n" if files else "")
        out.returncode = 0
        return out

    monkeypatch.setattr("subprocess.run", fake_run)


# #1216 原始 bug 的沉默失效面：这四个前缀的文件当年一个都映射不上。
@pytest.mark.parametrize(
    "changed,mapped",
    [
        ("app/core/auth.py", "tests"),
        ("app/services/session_data.py", "tests/unit"),
        ("app/api/routes/chat.py", "tests"),
        ("app/tools/registry.py", "tests/unit/tools"),
        # 特异性降序：lib/gis 与 lib/quality 必须先于（不存在的）app/lib 命中
        ("app/lib/gis/geometry.py", "tests/unit/gis"),
        ("app/lib/quality/runner_support.py", "tests/quality"),
    ],
)
def test_two_segment_keys_match_own_files(
    qr: types.ModuleType, monkeypatch: pytest.MonkeyPatch, changed: str, mapped: str
) -> None:
    _patch_git(monkeypatch, [changed])
    targets = qr._changed_py_targets()
    assert mapped in targets, f"{changed} 必须映射到 {mapped}（#1216 类静默失效）"


def test_tests_changes_run_verbatim(qr: types.ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_git(monkeypatch, ["tests/unit/workflow_runtime/test_driver_x.py"])
    targets = qr._changed_py_targets()
    assert "tests/unit/workflow_runtime/test_driver_x.py" in targets


def test_base_lane_always_runs_and_is_deduped(
    qr: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_git(monkeypatch, ["app/core/auth.py", "app/api/routes/chat.py"])
    targets = qr._changed_py_targets()
    assert targets[:2] == ["tests/quality/", "tests/unit/tools/"]  # 红线恒跑
    assert len(targets) == len(set(targets))


def test_targets_bounded_at_40(qr: types.ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_git(monkeypatch, [f"app/core/m{i}.py" for i in range(60)] + ["README.md"])
    targets = qr._changed_py_targets()
    assert len(targets) <= 40


def test_unmapped_paths_yield_base_only(
    qr: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_git(monkeypatch, ["docs/x.md", "frontend/app/page.tsx"])
    targets = qr._changed_py_targets()
    assert targets == ["tests/quality/", "tests/unit/tools/"]
