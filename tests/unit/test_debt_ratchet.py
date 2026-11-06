"""债务棘轮（scripts/debt_ratchet.py）的回归测试（#1377）。

v2 颗粒化基线（G14）：计数规则按 {路径: 计数} 棘轮 + 总量双保险，
v1 标量基线保持兼容（退回旧"仅总量"判定）。词法屏蔽器把 TS 注释/
字符串/正则字面量从 ": any" 计数中剔除（只算真实标注位置）。
"""
import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts import debt_ratchet  # noqa: E402


def _parse(src: str) -> ast.Module:
    return ast.parse(src)


def _assert_cli_check_passes(root: Path, baseline: Path) -> None:
    # The scanner reads source text rather than executing app code. Do not let
    # inherited coverage startup hooks trace its stdlib AST walks again.
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith("COV_CORE_")
        and key not in {"COVERAGE_PROCESS_START", "COVERAGE_PROCESS_CONFIG"}
    }
    try:
        result = subprocess.run(
            [sys.executable, str(debt_ratchet.REPO_ROOT / "scripts" / "debt_ratchet.py"),
             "check", "--root", str(root), "--baseline", str(baseline)],
            cwd=debt_ratchet.REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=150,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(
            f"debt ratchet CLI timed out after {exc.timeout}s\n"
            f"stdout: {exc.stdout!r}\nstderr: {exc.stderr!r}")
    assert result.returncode == 0, (
        f"debt ratchet CLI exited {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    assert "debt ratchet OK" in result.stdout


class TestFileDiscovery:
    @pytest.mark.parametrize("subdir,suffixes", [
        ("frontend", (".ts", ".tsx")),
        ("app", (".py",)),
        ("scripts", (".py",)),
        ("tests", (".py",)),
    ])
    def test_preserves_included_paths(self, tmp_path, subdir, suffixes):
        base = tmp_path / subdir
        for directory in ("src", "buildings", "distances", "node_modules_like",
                          *debt_ratchet._FRONTEND_EXCLUDE):
            target = base / directory
            target.mkdir(parents=True)
            (target / f"source{suffixes[0]}").write_text("", encoding="utf-8")
        (base / "src" / "next-env.d.ts").write_text("", encoding="utf-8")
        (base / "ignored.txt").write_text("", encoding="utf-8")
        # Preserve rglob's treatment of suffix-bearing directories.
        (base / f"directory{suffixes[0]}").mkdir()
        expected = sorted(
            path for path in base.rglob("*")
            if path.suffix in suffixes
            and not any(part in debt_ratchet._FRONTEND_EXCLUDE for part in path.parts)
        )

        assert debt_ratchet._iter_files(tmp_path, subdir, suffixes) == expected
        assert base / "node_modules_like" / f"source{suffixes[0]}" in expected

    def test_preserves_symlinks_without_descending_into_them(self, tmp_path):
        base = tmp_path / "frontend"
        (base / "src").mkdir(parents=True)
        source = base / "src" / "source.ts"
        source.write_text("", encoding="utf-8")
        linked_file = base / "linked_file.ts"
        linked_directory = base / "linked_directory.ts"
        try:
            linked_file.symlink_to(source)
            linked_directory.symlink_to(base / "src", target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"test environment cannot create symlinks: {exc}")

        assert debt_ratchet._iter_files(tmp_path, "frontend", (".ts",)) == sorted(
            [linked_file, linked_directory, source])

    def test_excluded_subtrees_are_not_visited(self, tmp_path, monkeypatch):
        base = tmp_path / "frontend"
        for directory in debt_ratchet._FRONTEND_EXCLUDE:
            target = base / "src" / directory / "nested"
            target.mkdir(parents=True)
            (target / "hidden.ts").write_text("", encoding="utf-8")
        included = base / "src" / "included.ts"
        included.write_text("", encoding="utf-8")
        visited = []
        original_scandir = debt_ratchet.os.scandir

        def record_scandir(path):
            visited.append(Path(path))
            return original_scandir(path)

        monkeypatch.setattr(debt_ratchet.os, "scandir", record_scandir)

        assert debt_ratchet._iter_files(tmp_path, "frontend", (".ts",)) == [included]
        assert visited == [base, base / "src"]

    def test_missing_tree_is_empty(self, tmp_path):
        assert debt_ratchet._iter_files(tmp_path, "frontend", (".ts",)) == []

    def test_excluded_ancestor_is_empty(self, tmp_path):
        root = tmp_path / "build"
        (root / "app").mkdir(parents=True)
        (root / "app" / "source.py").write_text("", encoding="utf-8")
        assert debt_ratchet._iter_files(root, "app", (".py",)) == []


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
        # Exercise the real scanner on a bounded fixture. Scanning the entire
        # application again under coverage can exhaust the xdist worker budget.
        _make_tree(tmp_path)
        bp = tmp_path / "baseline.json"
        bp.write_text(json.dumps({
            "frontend_explicit_any": 0,
            "except_silent": 0,
            "god_modules": {},
            "assertless_tests": 0,
            "unannotated_defs_pct": 0,
        }), encoding="utf-8")
        monkeypatch.setattr(debt_ratchet, "BASELINE_PATH", bp)
        assert debt_ratchet.check(bp, tmp_path) == 1

    def test_checked_in_baseline_passes(self):
        """入库基线与当前代码一致时 check 必须绿。"""
        assert debt_ratchet.BASELINE_PATH.is_file()
        # Run the complete real scan through its CLI. It reads source text, not
        # application code; tracing its AST walks in an app-coverage worker adds
        # no application coverage and can exhaust the 180-second test budget.
        # Bound the child below that budget so a stalled scan reports a failure
        # instead of terminating the xdist worker.
        _assert_cli_check_passes(debt_ratchet.REPO_ROOT, debt_ratchet.BASELINE_PATH)

    def test_cli_propagates_real_gate_failure(self, tmp_path):
        baseline = _make_tree(tmp_path)
        baseline["frontend_explicit_any"] = {}
        bp = _write_baseline(tmp_path, baseline)

        with pytest.raises(AssertionError, match="debt ratchet CLI exited 1") as exc:
            _assert_cli_check_passes(tmp_path, bp)
        assert "debt ratchet FAILED" in str(exc.value)
        assert "frontend_explicit_any" in str(exc.value)

    def test_cli_timeout_reports_output_and_removes_coverage_hooks(self, monkeypatch):
        for key in ("COV_CORE_SOURCE", "COV_CORE_CONFIG", "COV_CORE_DATAFILE",
                    "COVERAGE_PROCESS_START", "COVERAGE_PROCESS_CONFIG"):
            monkeypatch.setenv(key, "inherited-instrumentation")
        monkeypatch.setenv("DEBT_SCANNER_TEST_KEEP", "preserved")

        def timed_out(command, **kwargs):
            assert kwargs["timeout"] == 150
            assert kwargs["capture_output"] is True
            assert kwargs["env"]["DEBT_SCANNER_TEST_KEEP"] == "preserved"
            assert not any(key.startswith("COV_CORE_") for key in kwargs["env"])
            assert "COVERAGE_PROCESS_START" not in kwargs["env"]
            assert "COVERAGE_PROCESS_CONFIG" not in kwargs["env"]
            raise subprocess.TimeoutExpired(
                command, kwargs["timeout"], output=b"scan progress", stderr=b"scan diagnostic")

        monkeypatch.setattr(subprocess, "run", timed_out)
        with pytest.raises(pytest.fail.Exception, match="timed out after 150s") as exc:
            _assert_cli_check_passes(debt_ratchet.REPO_ROOT, debt_ratchet.BASELINE_PATH)
        assert "scan progress" in str(exc.value)
        assert "scan diagnostic" in str(exc.value)

    def test_new_god_module_fails(self, monkeypatch, tmp_path, capsys):
        _make_tree(tmp_path)
        (tmp_path / "app" / "oversized.py").write_text(
            "pass\n" * (debt_ratchet.GOD_MODULE_LINES + 1), encoding="utf-8")
        bp = tmp_path / "baseline.json"
        bp.write_text(json.dumps({
            "frontend_explicit_any": 999999,
            "except_silent": 999999,
            "god_modules": {},
            "assertless_tests": 999999,
            "unannotated_defs_pct": 99.0,
        }), encoding="utf-8")
        monkeypatch.setattr(debt_ratchet, "BASELINE_PATH", bp)
        assert debt_ratchet.check(bp, tmp_path) == 1
        assert "god_modules: NEW" in capsys.readouterr().out


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


# ------------------------------------------------- #1550 dict[str, Any]


class TestDictStrAnyCollection:
    """#1550：dict[str, Any] 计数的识别面（AST，注释/字符串不误计）。"""

    def _count(self, src: str, tmp_path) -> int:
        (tmp_path / "app").mkdir(exist_ok=True)
        (tmp_path / "app" / "m.py").write_text(src, encoding="utf-8")
        return len(debt_ratchet.collect_dict_str_any(tmp_path))

    def test_lowercase_and_typing_forms_counted(self, tmp_path):
        src = (
            "from typing import Any, Dict\n"
            "def f(a: dict[str, Any]) -> Dict[str, Any]:\n"
            "    x: dict[str, Any] = {}\n"
            "    return x\n"
        )
        assert self._count(src, tmp_path) == 3

    def test_attribute_form_counted(self, tmp_path):
        src = "import typing\ndef f() -> typing.Dict[str, Any]:\n    return {}\n"
        assert self._count(src, tmp_path) == 1

    def test_each_occurrence_on_one_line_counted(self, tmp_path):
        # 一行两处独立注解 → 按出现位各计一次（同行不合并）。
        src = (
            "from typing import Any, Dict\n"
            "a: Dict[str, Any] = {}; b: dict[str, Any] = {}\n"
        )
        (tmp_path / "app").mkdir(exist_ok=True)
        (tmp_path / "app" / "m.py").write_text(src, encoding="utf-8")
        items = debt_ratchet.collect_dict_str_any(tmp_path)
        assert len(items) == 2
        assert {it.line for it in items} == {2}

    def test_nested_non_any_value_not_counted(self, tmp_path):
        # dict[str, Dict[str, Any]] 只计内层那处 —— 外层值类型不是 Any。
        src = (
            "from typing import Any, Dict\n"
            "x: dict[str, Dict[str, Any]] = {}\n"
        )
        (tmp_path / "app").mkdir(exist_ok=True)
        (tmp_path / "app" / "m.py").write_text(src, encoding="utf-8")
        items = debt_ratchet.collect_dict_str_any(tmp_path)
        assert len(items) == 1
        assert items[0].line == 2

    def test_other_generics_not_counted(self, tmp_path):
        src = (
            "from typing import Any, Dict, List, Optional\n"
            "def f(a: dict[str, int], b: List[str],\n"
            "       c: Optional[Dict[int, Any]]) -> None:\n"
            "    pass\n"
        )
        assert self._count(src, tmp_path) == 0

    def test_comment_and_string_not_counted(self, tmp_path):
        src = (
            "from typing import Any, Dict\n"
            "# dict[str, Any] in a comment is not debt\n"
            "MSG = 'Dict[str, Any] in a string is not debt'\n"
            "def f() -> None:\n"
            "    pass\n"
        )
        assert self._count(src, tmp_path) == 0

    def test_gate_fails_when_total_grows(self, tmp_path):
        (tmp_path / "app").mkdir()
        (tmp_path / "app" / "m.py").write_text(
            "from typing import Any\ndef f() -> dict[str, Any]:\n    return {}\n",
            encoding="utf-8")
        baseline = debt_ratchet.measure(tmp_path)
        bp = _write_baseline(tmp_path, baseline)
        # 同一文件再加一处 → 总量棘轮必须 FAIL
        (tmp_path / "app" / "m.py").write_text(
            "from typing import Any\n"
            "def f() -> dict[str, Any]:\n"
            "    x: dict[str, Any] = {}\n"
            "    return x\n",
            encoding="utf-8")
        current, items = debt_ratchet._collect(tmp_path)
        failures = debt_ratchet._gate_failures(baseline, current, items)
        assert any(f.startswith("dict_str_any: 2 > baseline 1")
                   for f in failures), failures
        assert debt_ratchet.check(bp, tmp_path) == 1

    def test_paying_down_passes(self, tmp_path):
        (tmp_path / "app").mkdir()
        (tmp_path / "app" / "m.py").write_text(
            "from typing import Any\n"
            "def f() -> dict[str, Any]:\n"
            "    x: dict[str, Any] = {}\n"
            "    return x\n",
            encoding="utf-8")
        baseline = debt_ratchet.measure(tmp_path)
        (tmp_path / "app" / "m.py").write_text(
            "from typing import Any\ndef f() -> dict[str, Any]:\n    return {}\n",
            encoding="utf-8")
        bp = _write_baseline(tmp_path, baseline)
        assert debt_ratchet.check(bp, tmp_path) == 0

    def test_missing_rule_key_in_v2_baseline_fails(self, tmp_path):
        baseline = _make_tree(tmp_path)
        del baseline["dict_str_any"]
        bp = _write_baseline(tmp_path, baseline)
        current, items = debt_ratchet._collect(tmp_path)
        failures = debt_ratchet._gate_failures(baseline, current, items)
        assert any("dict_str_any" in f and "缺失规则键" in f
                   for f in failures), failures
        assert debt_ratchet.check(bp, tmp_path) == 1

    def test_v1_baseline_without_dict_str_any_still_passes(self, tmp_path):
        """v1 标量基线本就没有 dict_str_any 键 —— 兼容跳过，不误判。"""
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
        "dict_str_any",
    }
    assert isinstance(data.get("note", ""), str)
    for key in ("frontend_explicit_any", "except_silent", "god_modules",
                "assertless_tests", "unannotated_defs"):
        assert isinstance(data[key], dict), key
        assert all(isinstance(v, int) for v in data[key].values()), key
    assert isinstance(data["unannotated_defs_pct"], (int, float))
    # #1550：dict_str_any 是总量口径标量（非路径级 map）。
    assert isinstance(data["dict_str_any"], int)
