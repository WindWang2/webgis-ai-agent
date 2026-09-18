"""债务棘轮（scripts/debt_ratchet.py）的回归测试（#1377）。"""
import ast
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts import debt_ratchet  # noqa: E402


def _parse(src: str) -> ast.Module:
    return ast.parse(src)


class TestSilentHandlerDetection:
    def test_bare_pass_is_silent(self):
        tree = _parse("try:\n    x()\nexcept Exception:\n    pass\n")
        handler = tree.body[0].handlers[0]
        assert debt_ratchet._is_silent_handler(handler)

    def test_ellipsis_is_silent(self):
        tree = _parse("try:\n    x()\nexcept Exception:\n    ...\n")
        handler = tree.body[0].handlers[0]
        assert debt_ratchet._is_silent_handler(handler)

    def test_docstring_plus_pass_is_silent(self):
        tree = _parse(
            'try:\n    x()\nexcept Exception:\n    "why"\n    pass\n')
        handler = tree.body[0].handlers[0]
        assert debt_ratchet._is_silent_handler(handler)

    def test_logged_handler_not_silent(self):
        tree = _parse(
            "try:\n    x()\nexcept Exception:\n    logger.debug('e')\n")
        handler = tree.body[0].handlers[0]
        assert not debt_ratchet._is_silent_handler(handler)


class TestAssertlessDetection:
    def test_plain_assert_counts(self):
        func = _parse("def test_x():\n    assert 1 == 1\n").body[0]
        assert debt_ratchet._has_assertion(func)

    def test_pytest_raises_counts(self):
        func = _parse(
            "def test_x():\n"
            "    with pytest.raises(ValueError):\n"
            "        x()\n"
        ).body[0]
        assert debt_ratchet._has_assertion(func)

    def test_mock_assert_counts(self):
        func = _parse(
            "def test_x():\n    m.assert_called_once()\n").body[0]
        assert debt_ratchet._has_assertion(func)

    def test_no_assertion_detected(self):
        func = _parse("def test_x():\n    do_thing()\n    cleanup()\n").body[0]
        assert not debt_ratchet._has_assertion(func)


class TestCheckGate:
    def test_missing_baseline_returns_2(self, monkeypatch, tmp_path):
        monkeypatch.setattr(debt_ratchet, "BASELINE_PATH",
                            tmp_path / "nope.json")
        assert debt_ratchet.check() == 2

    def test_zero_baseline_fails_on_any_debt(self, monkeypatch, tmp_path):
        bp = tmp_path / "baseline.json"
        bp.write_text(json.dumps({
            "frontend_explicit_any": 0,
            "except_silent": 0,
            "god_modules": {},
            "assertless_tests": 0,
            "unannotated_defs_pct": 0,
        }), encoding="utf-8")
        monkeypatch.setattr(debt_ratchet, "BASELINE_PATH", bp)
        assert debt_ratchet.check() == 1

    def test_checked_in_baseline_passes(self):
        """入库基线与当前代码一致时 check 必须绿。"""
        assert debt_ratchet.BASELINE_PATH.is_file()
        assert debt_ratchet.check() == 0

    def test_new_god_module_fails(self, monkeypatch, tmp_path):
        bp = tmp_path / "baseline.json"
        bp.write_text(json.dumps({
            "frontend_explicit_any": 999999,
            "except_silent": 999999,
            "god_modules": {},
            "assertless_tests": 999999,
            "unannotated_defs_pct": 99.0,
        }), encoding="utf-8")
        monkeypatch.setattr(debt_ratchet, "BASELINE_PATH", bp)
        # 基线没有 god_modules 条目 → 当前所有 >2000 行文件都算 NEW
        assert debt_ratchet.check() == 1


@pytest.mark.skipif(
    not (Path(__file__).resolve().parents[2] / "frontend").is_dir(),
    reason="frontend tree absent",
)
def test_baseline_file_shape():
    data = json.loads(
        debt_ratchet.BASELINE_PATH.read_text(encoding="utf-8"))
    assert set(data) == {
        "frontend_explicit_any", "except_silent", "god_modules",
        "assertless_tests", "unannotated_defs_pct",
    }
    assert isinstance(data["god_modules"], dict)
    assert all(isinstance(v, int) for v in data["god_modules"].values())
