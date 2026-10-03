"""债务棘轮（scripts/debt_ratchet.py）的回归测试（#1377）。

v2 颗粒化基线（G14）：计数规则按 {路径: 计数} 棘轮 + 总量双保险，
v1 标量基线保持兼容（退回旧"仅总量"判定）。词法屏蔽器把 TS 注释/
字符串/正则字面量从 ": any" 计数中剔除（只算真实标注位置）。
"""
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
        func = _parse("def test_x():\n    m.assert_called_once()\n").body[0]
        assert debt_ratchet._has_assertion(func)

    def test_no_assertion_detected(self):
        func = _parse("def test_x():\n    do_thing()\n    cleanup()\n").body[0]
        assert not debt_ratchet._has_assertion(func)


class TestTsNoncodeMasking:
    """_mask_ts_noncode：": any" 只在代码位置计数（G14 误报修复）。"""

    def _count(self, src: str) -> int:
        return len(debt_ratchet._ANY_RE.findall(
            debt_ratchet._mask_ts_noncode(src)))

    def test_real_annotation_counts(self):
        assert self._count("const a: any = 1;") == 1

    def test_line_comment_not_counted(self):
        assert self._count("const a = 1; // see: any docs") == 0

    def test_block_comment_not_counted_multiline(self):
        src = "const a = 1;\n/*\n: any\n*/\nconst b: any = 2;\n"
        assert self._count(src) == 1

    def test_string_content_not_counted(self):
        assert self._count('const msg = "type: any here";') == 0

    def test_url_slashes_in_string_do_not_open_comment(self):
        src = 'const u = "https://x.io//a"; const t: any = u;'
        assert self._count(src) == 1

    def test_template_text_not_counted(self):
        assert self._count("const t = `value: any`;") == 0

    def test_template_interpolation_is_code(self):
        src = "const t = `${flag ? a : b}`; const x: any = 1;"
        assert self._count(src) == 1

    def test_regex_literal_not_counted(self):
        assert self._count('s.replace(/: any/g, "")') == 0

    def test_regex_charclass_quotes_do_not_open_string(self):
        src = 'const re = /["\']/; const x: any = 1;'
        assert self._count(src) == 1

    def test_division_is_not_regex(self):
        src = "const q = a / b; const z: any = 1;"
        assert self._count(src) == 1

    def test_escaped_quote_in_string(self):
        assert self._count("const s = 'it\\'s: any';") == 0

    def test_nested_template_string(self):
        src = "const t = `a ${x ? `n: any` : ''} b`;"
        assert self._count(src) == 0

    def test_single_quote_leak_does_not_cascade_across_lines(self):
        # JSX 文本里的撇号（非字符串定界符）只影响本行，不得翻转
        # 后续行的奇偶（G14 修复：'/" 帧遇裸换行强制复位）。
        src = "const el = <div>don't panic</div>;\nconst v: any = 1;\n"
        assert self._count(src) == 1

    def test_jsx_closing_tag_slash_is_not_regex_start(self):
        # G14 review P1 回归：`</div>` 的 `/` 不得被当正则起点跨行吞并。
        src = "return <div>hi</div>;\nconst z: any = 3;\n"
        assert self._count(src) == 1

    def test_jsx_expression_with_any_still_counted(self):
        src = "<div>{arr.map((x: any) => x)}</div>\n"
        assert self._count(src) == 1

    def test_unterminated_regex_start_falls_back_to_division(self):
        # 正则字面量必须同行闭合；同行无收尾 `/` 按除法处理，不跨行屏蔽。
        src = "const q = /\n; const w: any = 2;\n"
        assert self._count(src) == 1

    def test_masking_preserves_length_and_newlines(self):
        src = "const a = 1; /* x: any */\nconst b: any = 2;\n"
        masked = debt_ratchet._mask_ts_noncode(src)
        assert len(masked) == len(src)
        for orig, new in zip(src, masked):
            if orig == "\n":
                assert new == "\n"


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


# ------------------------------------------------------- v2 颗粒化（G14）


def _make_tree(root: Path) -> dict:
    """正反例共用的最小 fixture 树：1 处 any / 1 处吞异常 / 1 个无断言测试 /
    1 个未注解函数，全部可定位到行。"""
    (root / "frontend" / "src").mkdir(parents=True)
    (root / "frontend" / "src" / "a.ts").write_text(
        "const a: any = 1;\nconst b: any = 2;\n", encoding="utf-8")
    (root / "app").mkdir()
    (root / "app" / "m.py").write_text(
        "def f(x):\n"
        "    try:\n"
        "        g()\n"
        "    except Exception:\n"
        "        pass\n",
        encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_a.py").write_text(
        "def test_ok():\n"
        "    assert True\n"
        "\n"
        "\n"
        "def test_bad():\n"
        "    pass\n",
        encoding="utf-8")
    return debt_ratchet.measure(root)


def _write_baseline(root: Path, data: dict) -> Path:
    bp = root / "_baseline.json"
    bp.write_text(json.dumps(data), encoding="utf-8")
    return bp


class TestGranularRatchet:
    def test_v2_baseline_clean_tree_passes(self, tmp_path):
        baseline = _make_tree(tmp_path)
        assert baseline["version"] == 2
        bp = _write_baseline(tmp_path, baseline)
        assert debt_ratchet.check(bp, tmp_path) == 0

    def test_new_any_file_fails_with_path_and_line(self, tmp_path):
        baseline = _make_tree(tmp_path)
        bp = _write_baseline(tmp_path, baseline)
        (tmp_path / "frontend" / "src" / "new.ts").write_text(
            "const c: any = 3;\n", encoding="utf-8")
        current, items = debt_ratchet._collect(tmp_path)
        failures = debt_ratchet._gate_failures(baseline, current, items)
        hit = [f for f in failures
               if "frontend_explicit_any" in f and "frontend/src/new.ts" in f
               and "L1" in f]
        assert hit, failures
        assert debt_ratchet.check(bp, tmp_path) == 1

    def test_new_silent_except_fails_with_path_and_line(self, tmp_path):
        baseline = _make_tree(tmp_path)
        bp = _write_baseline(tmp_path, baseline)
        (tmp_path / "app" / "n.py").write_text(
            "try:\n    h()\nexcept Exception:\n    pass\n", encoding="utf-8")
        current, items = debt_ratchet._collect(tmp_path)
        failures = debt_ratchet._gate_failures(baseline, current, items)
        hit = [f for f in failures
               if "except_silent" in f and "app/n.py" in f and "L3" in f]
        assert hit, failures
        assert debt_ratchet.check(bp, tmp_path) == 1

    def test_growing_existing_file_fails_per_path_and_total(self, tmp_path):
        baseline = _make_tree(tmp_path)
        (tmp_path / "frontend" / "src" / "a.ts").write_text(
            "const a: any = 1;\nconst b: any = 2;\nconst c: any = 3;\n",
            encoding="utf-8")
        current, items = debt_ratchet._collect(tmp_path)
        failures = debt_ratchet._gate_failures(baseline, current, items)
        assert any("frontend/src/a.ts 3 > baseline 2" in f for f in failures)
        assert any("frontend_explicit_any 总量: 3 > baseline 2" in f
                   for f in failures)

    def test_redistribution_fails_under_v2_but_passes_under_v1(self, tmp_path):
        """颗粒化的核心增量：A 文件清债 + B 文件加债（总量不变）必须 FAIL。"""
        baseline = _make_tree(tmp_path)
        (tmp_path / "frontend" / "src" / "a.ts").write_text(
            "const a: any = 1;\n", encoding="utf-8")
        (tmp_path / "frontend" / "src" / "new.ts").write_text(
            "const c: any = 3;\n", encoding="utf-8")
        current, items = debt_ratchet._collect(tmp_path)
        # v2（路径级）：new.ts 是新路径 → 违规
        assert debt_ratchet._gate_failures(baseline, current, items)
        # v1（标量总量 2）：总量不变 → 旧判定放行（这就是要堵的洞）
        v1 = {
            "frontend_explicit_any": 2,
            "except_silent": 1,
            "god_modules": {},
            "assertless_tests": 1,
            "unannotated_defs_pct": 100.0,
        }
        assert not debt_ratchet._gate_failures(v1, current, items)

    def test_v1_scalar_baseline_exact_totals_pass(self, tmp_path):
        _make_tree(tmp_path)
        v1 = {
            "frontend_explicit_any": 2,
            "except_silent": 1,
            "god_modules": {},
            "assertless_tests": 1,
            "unannotated_defs_pct": 100.0,
        }
        bp = _write_baseline(tmp_path, v1)
        assert debt_ratchet.check(bp, tmp_path) == 0

    def test_v1_scalar_baseline_lower_total_fails(self, tmp_path):
        _make_tree(tmp_path)
        v1 = {
            "frontend_explicit_any": 1,  # 实际 2 → 总量违规
            "except_silent": 1,
            "god_modules": {},
            "assertless_tests": 1,
            "unannotated_defs_pct": 100.0,
        }
        bp = _write_baseline(tmp_path, v1)
        current, items = debt_ratchet._collect(tmp_path)
        failures = debt_ratchet._gate_failures(v1, current, items)
        assert any(f.startswith("frontend_explicit_any: 2 > baseline 1")
                   for f in failures)
        assert debt_ratchet.check(bp, tmp_path) == 1

    def test_paying_down_debt_passes_and_reports_improvement(self, tmp_path,
                                                             capsys):
        baseline = _make_tree(tmp_path)
        (tmp_path / "frontend" / "src" / "a.ts").write_text(
            "const a: any = 1;\n", encoding="utf-8")
        bp = _write_baseline(tmp_path, baseline)
        assert debt_ratchet.check(bp, tmp_path) == 0
        out = capsys.readouterr().out
        assert "frontend_explicit_any: 1 / baseline 2 (-1)" in out

    def test_new_god_module_in_fixture_fails(self, tmp_path):
        baseline = _make_tree(tmp_path)
        (tmp_path / "app" / "big.py").write_text("x = 1\n" * 2001,
                                                 encoding="utf-8")
        current, items = debt_ratchet._collect(tmp_path)
        failures = debt_ratchet._gate_failures(baseline, current, items)
        assert any("god_modules: NEW" in f and "app/big.py" in f
                   for f in failures)

    def test_v2_baseline_missing_rule_key_fails(self, tmp_path):
        """v2 基线缺规则键 = 文件被篡改/不完整，必须 FAIL 而非静默跳过。"""
        baseline = _make_tree(tmp_path)
        del baseline["except_silent"]
        bp = _write_baseline(tmp_path, baseline)
        current, items = debt_ratchet._collect(tmp_path)
        failures = debt_ratchet._gate_failures(baseline, current, items)
        assert any("except_silent" in f and "缺失规则键" in f
                   for f in failures), failures
        assert debt_ratchet.check(bp, tmp_path) == 1

    def test_v1_baseline_without_new_rule_keys_still_passes(self, tmp_path):
        """v1 标量基线本就没有 unannotated_defs/pct 键——兼容跳过，不误判。"""
        _make_tree(tmp_path)
        v1 = {
            "frontend_explicit_any": 2,
            "except_silent": 1,
            "god_modules": {},
            "assertless_tests": 1,
        }
        bp = _write_baseline(tmp_path, v1)
        current, items = debt_ratchet._collect(tmp_path)
        assert debt_ratchet._gate_failures(v1, current, items) == []
        assert debt_ratchet.check(bp, tmp_path) == 0


class TestLocate:
    def test_locate_reports_every_item_with_file_and_line(self, tmp_path):
        _make_tree(tmp_path)
        lines = debt_ratchet._locate_lines(tmp_path)
        assert "frontend/src/a.ts:1" in lines
        assert "frontend/src/a.ts:2" in lines
        assert "app/m.py:4  except Exception" in lines
        assert "tests/test_a.py:5  test_bad" in lines
        assert "app/m.py:1  f" in lines
        assert "# unannotated_defs_pct: 100.0" in lines

    def test_locate_rule_filter(self, tmp_path):
        _make_tree(tmp_path)
        lines = debt_ratchet._locate_lines(tmp_path, rule="except_silent")
        assert all(item.startswith("#") or "except_silent" not in item
                   for item in lines)
        assert "app/m.py:4  except Exception" in lines
        assert "frontend/src/a.ts:1" not in lines


@pytest.mark.skipif(
    not (Path(__file__).resolve().parents[2] / "frontend").is_dir(),
    reason="frontend tree absent",
)
def test_baseline_file_shape():
    data = json.loads(
        debt_ratchet.BASELINE_PATH.read_text(encoding="utf-8"))
    assert data["version"] == 2
    # TC-24：重基线时人工写入的 "note"（审计说明，脚本不消费）是允许的可选键。
    assert set(data) - {"note"} == {
        "version", "frontend_explicit_any", "except_silent", "god_modules",
        "assertless_tests", "unannotated_defs", "unannotated_defs_pct",
    }
    assert isinstance(data.get("note", ""), str)
    for key in ("frontend_explicit_any", "except_silent", "god_modules",
                "assertless_tests", "unannotated_defs"):
        assert isinstance(data[key], dict), key
        assert all(isinstance(v, int) for v in data[key].values()), key
    assert isinstance(data["unannotated_defs_pct"], (int, float))
