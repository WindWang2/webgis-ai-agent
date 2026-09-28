"""import 边界门禁测试（ADR-0216，issues #1541/#1542 防回归）。

三层断言：
1. 真实仓树 PASS —— gate 对当前 app/ 无违规（本文件同时是 ci-local.sh
   contract tier 成员，构成"一条命令"本地门禁）。
2. 负向 fixture —— tmp 树构造 core→services / contracts→services /
   lib/cartography→services / 函数级懒导入违规，gate 必须逐条 FAIL。
3. 豁免语义 —— TYPE_CHECKING 守护 import 与 ``# h01:allow`` 行内注释
   被正确豁免；``h01:allow`` 当前全仓使用数为 0（防逃生口滥用）。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GATE = REPO_ROOT / "scripts" / "check_import_boundaries.py"


def _run_gate(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE), str(root)],
        capture_output=True,
        text=True,
        timeout=120,
    )


def _make_pkg(root: Path, dotted: str, init: str = '"""pkg"""\n') -> Path:
    pkg_dir = root.joinpath(*dotted.split("."))
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "__init__.py").write_text(init, encoding="utf-8")
    return pkg_dir


# ── 1. 真实仓树 ──────────────────────────────────────────────────────────────


def test_real_tree_has_no_boundary_violations():
    result = _run_gate(REPO_ROOT)
    assert result.returncode == 0, (
        f"import 边界违规（ADR-0216）:\n{result.stderr}"
    )
    assert "[import-boundaries] OK" in result.stdout


def test_allow_escape_hatch_unused_in_repo():
    """``# h01:allow`` 逃生口当前全仓 0 处 —— 用了必须在 PR 说明并同步此处。"""
    hits = [
        str(p)
        for p in (REPO_ROOT / "app").rglob("*.py")
        if "h01:allow" in p.read_text(encoding="utf-8")
    ]
    assert hits == [], f"h01:allow 逃生口被使用: {hits}"


# ── 2. 负向 fixture ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("module_path", "import_stmt"),
    [
        # core → services（模块级）
        ("app/core/violator.py", "from app.services.history_service_async import AsyncHistoryService\n"),
        # core → services（函数级懒导入 —— #1541/#1542 的真实形态）
        (
            "app/core/violator_lazy.py",
            "def f():\n    from app.services import chat_engine\n    return chat_engine\n",
        ),
        # contracts → services（kernel 依赖上层，禁止）
        ("app/contracts/violator.py", "from app.services.governor import render_budget\n"),
        # lib/cartography → services（#1541 原形）
        (
            "app/lib/cartography/violator.py",
            "from app.services.mapspec import lifecycle_engine\n",
        ),
        # lib/cartography → api
        (
            "app/lib/cartography/violator_api.py",
            "from app.api.routes import chat\n",
        ),
        # core → api
        ("app/core/violator_api.py", "from app.api.routes import health\n"),
    ],
)
def test_gate_fails_on_violation(tmp_path, module_path, import_stmt):
    pkg = _make_pkg(tmp_path, ".".join(module_path.split("/")[:-1]))
    (pkg / module_path.split("/")[-1]).write_text(import_stmt, encoding="utf-8")
    result = _run_gate(tmp_path)
    assert result.returncode == 1, "gate 必须对违规边 FAIL"
    assert module_path.replace("/", ".") in result.stderr.replace("/", ".") or (
        module_path in result.stderr
    )


def test_gate_violation_message_contains_rule_reason(tmp_path):
    pkg = _make_pkg(tmp_path, "app.core")
    (pkg / "bad.py").write_text(
        "from app.services.history_service_async import AsyncHistoryService\n",
        encoding="utf-8",
    )
    result = _run_gate(tmp_path)
    assert "app.core" in result.stderr
    assert "app.services.history_service_async" in result.stderr
    assert "ADR-0216" in result.stderr


def test_gate_passes_on_clean_tree(tmp_path):
    _make_pkg(tmp_path, "app.core")
    _make_pkg(tmp_path, "app.services")
    (tmp_path / "app" / "core" / "ok.py").write_text(
        "from app.core.config import settings\n", encoding="utf-8"
    )
    result = _run_gate(tmp_path)
    assert result.returncode == 0
    assert "[import-boundaries] OK" in result.stdout


# ── 3. 豁免语义 ──────────────────────────────────────────────────────────────


def test_type_checking_imports_exempt(tmp_path):
    pkg = _make_pkg(tmp_path, "app.core")
    (pkg / "typed.py").write_text(
        "from typing import TYPE_CHECKING\n"
        "\n"
        "if TYPE_CHECKING:\n"
        "    from app.services.chat_engine import ChatEngine\n"
        "\n"
        "\n"
        "def build() -> 'ChatEngine':  # noqa: F821\n"
        "    raise NotImplementedError\n",
        encoding="utf-8",
    )
    result = _run_gate(tmp_path)
    assert result.returncode == 0, result.stderr


def test_allow_comment_exempts_and_is_visible(tmp_path):
    pkg = _make_pkg(tmp_path, "app.core")
    (pkg / "waived.py").write_text(
        "from app.services.chat_engine import ChatEngine  # h01:allow 遗留豁免\n",
        encoding="utf-8",
    )
    result = _run_gate(tmp_path)
    assert result.returncode == 0, result.stderr
