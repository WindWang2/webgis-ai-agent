"""Issue #564：perf 套件进 PR 门、perf_harness_v2 不再被 --cov lane 误收集、
覆盖率闸为真（后端 --cov-fail-under 非装饰值 + 前端 vitest thresholds）。

结构断言，pattern 同 tests/test_real_services_ci_wiring.py。
"""
import ast
import re
import shlex
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "production.yml"
PERF_DIR = REPO_ROOT / "tests" / "benchmarks"

# test-perf PR lane 的显式文件清单（与 workflow 的 run 命令保持一致）
PR_LANE_PERF_FILES = (
    "test_perf_harness.py",
    "test_transport_perf.py",
    "test_job_runtime_perf.py",
    "test_provenance_perf.py",
    "test_llm_http_pooling_perf.py",
    "test_perf_mapspec_mutation_cost.py",
    "test_dispatch_stall_perf.py",
    "test_perf_large_workspace.py",
    "test_data_control_plane_v5.py",
    # ADR-0245 Wave 11：墙钟断言统一 perf 车道（原散落在 --cov 主车道，
    # 与 #664 隔离策略冲突；接线见 production.yml test-perf 与 ci-local.sh）
    "test_gis_registry_perf.py",
    "test_template_registry_perf.py",
    "test_planner_runtime_perf.py",
    "test_pi_perf.py",
    "test_data_runtime_v2_perf.py",
    "test_geobench_v3.py",
    "test_quality_scenario_corpus_perf.py",
    "test_network_closest_facility_perf.py",
)

# Perf-marked files that stay nightly-only. They are collected by
# nightly-matrix ``-m "cartography or perf"`` and must NOT join the PR
# test-perf file list: they are wall-clock / large-N and trip the PR
# 180s thread timeout (or flake under shared runners).
NIGHTLY_ONLY_PERF_FILES = {
    # H02 W9 制图清单装配/校验预算为墙钟断言（upper_ms 档位）—— PR 共享
    # runner 负载下 flake，留 nightly（结构性证据由 cartography marker 承担）。
    "test_h02_cartography_perf.py": (
        "manifest assembly / verify semantics wall-clock budgets (upper_ms "
        "bands); flaky under PR load / --cov"
    ),
    "test_perf_harness_v2.py": (
        "event-loop lag is a wall-clock assertion; flaky under PR load / --cov"
    ),
    "test_perf_mapspec_e2e.py": (
        "1k/10k/50k MapSpec upsert + lag monitor exceeds the PR 180s budget"
    ),
    "test_perf_mapspec_svg_compile.py": (
        "20k-feature SVG compile wall-clock (15s structural budget); "
        "flaky under PR load / --cov"
    ),
    "test_lakehouse_v6.py": (
        "ADR-0249: depends on the optional geo stack (zarr is not a "
        "requirements.txt dependency — PR lane would skip or red at import) "
        "and carries wall-clock ratio bands; structural evidence stays "
        "deterministic for nightly"
    ),
    "test_lakehouse_v7.py": (
        "ADR-0119: optional geo stack (zarr/xarray) + nightly isolation; "
        "structural evidence (sharded composition, GC metadata-only "
        "touch counting) stays deterministic for nightly"
    ),
    "test_scene_perf.py": (
        "ADR-0199 M8: wall-clock medians (1k-layer gate / 1k² terrarium "
        "encode) — same flake-under-PR-load class as perf_harness_v2; "
        "nightly -m perf owns it"
    ),
}

_STDLIB = set(sys.stdlib_module_names)


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _job_run_text(job: str) -> str:
    return "\n".join(
        s.get("run", "") for s in _workflow()["jobs"][job]["steps"] if s.get("run")
    )


def _perf_marked_files() -> set:
    marked = set()
    for f in PERF_DIR.glob("test_*.py"):
        src = f.read_text(encoding="utf-8")
        if "pytestmark = pytest.mark.perf" in src or "@pytest.mark.perf" in src:
            marked.add(f.name)
    return marked


def _top_level_third_party_imports() -> set:
    """PR-lane perf 文件在模块层的第三方 import（ast 解析，避开 docstring/注释）。

    例：test_job_runtime_perf.py 的 `import pytest_asyncio` —— 若 lane 的
    pip install 不装 pytest-asyncio，收集期即 ImportError，lane 红。
    """
    third = set()
    first_party = {"app", "tests"}
    for name in PR_LANE_PERF_FILES:
        tree = ast.parse((PERF_DIR / name).read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Import):
                for a in node.names:
                    mod = a.name.split(".")[0]
                    if mod not in _STDLIB and mod not in first_party:
                        third.add(mod)
            elif isinstance(node, ast.ImportFrom):
                if node.level:  # 相对导入
                    continue
                mod = (node.module or "").split(".")[0]
                if mod and mod not in _STDLIB and mod not in first_party:
                    third.add(mod)
    return third


def _module_of_pip_package(pkg: str) -> str:
    """pip 包名 -> import 模块名的归一化：pytest-asyncio -> pytest_asyncio、
    psycopg2-binary -> psycopg2（`-`→`_`，剥离 -binary/-stubs 后缀）、
    sqlalchemy[asyncio] -> sqlalchemy（剥离 [...] extras，#1656：不剥则
    requirements.txt 的 extra 声明会让 covered 集合永远对不上 import 名，
    断言恒红）。"""
    name = pkg.split("[", 1)[0]
    name = name.split("==")[0].split(">=")[0].split("<")[0].strip().replace("-", "_")
    for suffix in ("_binary", "_stubs"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name


def _modules_of_requirements(req_path: Path) -> set:
    """requirements 文件 -> 包名归一化集合（跳过注释/空行/-r/-c 等选项行）。"""
    modules: set = set()
    for raw in req_path.read_text(encoding="utf-8").splitlines():
        raw = raw.split("#", 1)[0].strip()
        if not raw or raw.startswith("-"):
            continue
        modules.add(_module_of_pip_package(raw))
    return modules


def test_pr_perf_lane_installs_all_module_level_imports():
    """test-perf PR lane 的 pip install 必须覆盖 perf 文件模块层的全部第三方
    import —— 缺任何一个（如 pytest-asyncio）都会在收集期 ImportError，
    lane 红 → release-gate 红。覆盖集 = lane install 行显式参数 ∪ 其
    -r/--requirement 实际引入的 requirements 文件内容（#1656：lane 行若
    去掉 -r requirements.txt，整份基础依赖即失去覆盖，断言必须报红）。"""
    run = _job_run_text("test-perf")
    install_lines = [line for line in run.splitlines() if "pip install" in line]
    assert install_lines, "test-perf lane 必须有 pip install 步骤"

    covered: set = set()
    for line in install_lines:
        args = shlex.split(line)
        for i, arg in enumerate(args):
            if arg in ("pip", "install"):
                continue
            if arg in ("-r", "--requirement"):
                if i + 1 < len(args):
                    covered |= _modules_of_requirements(REPO_ROOT / args[i + 1])
                continue
            if arg.startswith(("-r=", "--requirement=")):
                covered |= _modules_of_requirements(REPO_ROOT / arg.split("=", 1)[1])
                continue
            if arg.startswith("-"):  # -c/--constraint 等：约束版本，不构成安装覆盖
                continue
            covered.add(_module_of_pip_package(arg))

    missing = sorted(_top_level_third_party_imports() - covered)
    assert not missing, (
        "test-perf PR lane 的 pip install 未覆盖 perf 文件模块层 import: "
        f"{missing} —— 收集期 ImportError → lane 红 → release-gate 红"
    )


def test_lane_dropping_requirements_r_flag_fails_closed(monkeypatch, tmp_path):
    """fail-closed 反向验证（#1656 验收标准 3）：把 lane 安装行里的
    `-r requirements.txt` 去掉后，覆盖集只剩显式 pin，perf 文件的第三方
    import 缺失必须报红（防止本契约被"恒绿"化）。"""
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    for step in workflow["jobs"]["test-perf"]["steps"]:
        run = step.get("run", "")
        if "pip install" in run:
            assert "-r requirements.txt " in run, "lane 安装行形态变化，请同步本测试"
            step["run"] = run.replace("-r requirements.txt ", "", 1)
    patched = tmp_path / "production.yml"
    patched.write_text(yaml.safe_dump(workflow, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "WORKFLOW", patched)

    with pytest.raises(AssertionError, match="sqlalchemy"):
        test_pr_perf_lane_installs_all_module_level_imports()


def test_every_perf_marked_file_is_wired_into_a_lane():
    """每个 perf-marked 文件都必须有真实归属 lane：要么在 PR test-perf 的
    显式文件清单里，要么登记进 NIGHTLY_ONLY_PERF_FILES 并由 nightly 的
    marker 选择器收集（TEST-09：原断言 `f in pr_run or 选择器存在` 恒真 ——
    选择器只要在 nightly 就吞掉所有未接线文件，覆盖元测试空转）。"""
    pr_run = _job_run_text("test-perf")
    nightly_run = _job_run_text("nightly-matrix")
    nightly_selector = '-m "cartography or perf"' in nightly_run
    for f in sorted(_perf_marked_files()):
        if f in PR_LANE_PERF_FILES:
            assert f in pr_run, (
                f"{f} 在 PR lane 清单里但 workflow test-perf 未显式选择它"
            )
        else:
            assert f in NIGHTLY_ONLY_PERF_FILES, (
                f"{f} 不在 PR lane 清单，也未登记为 nightly-only —— "
                "新增 perf 文件必须显式接线"
            )
            assert nightly_selector, (
                "nightly-matrix 必须用 -m \"cartography or perf\" 收集"
                " nightly-only perf 文件"
            )


def test_test_perf_pr_lane_runs_fast_subset_no_cov():
    run = _job_run_text("test-perf")
    assert "--no-cov" in run
    for f in PR_LANE_PERF_FILES:
        assert f in run, f"test-perf PR lane 缺少 {f}"
    # v2 的 event-loop lag 是墙钟断言，保持 nightly 专属，不进 PR
    assert "test_perf_harness_v2.py" not in run


def _assert_quality_corpus_perf_contract(source: str) -> None:
    """The required perf gate must execute all three real slices at 45s."""
    tree = ast.parse(source)
    assert any(
        isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets)
        and ast.unparse(node.value) == "pytest.mark.perf"
        for node in tree.body
    ), "quality corpus timing must have the perf marker"
    fn = next(
        node for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "test_quality_corpus_slice_budget"
    )
    assert any(
        isinstance(d, ast.Call)
        and ast.unparse(d.func) == "pytest.mark.parametrize"
        and ast.literal_eval(d.args[0]) == "prefix"
        and ast.literal_eval(d.args[1]) == ["basemap_", "science_", "data_"]
        for d in fn.decorator_list
    ), "all original timed corpus slices must remain selected"
    assert not any("skip" in ast.unparse(d) for d in fn.decorator_list)
    assignments = [node for node in fn.body if isinstance(node, ast.Assign)]
    assert len(assignments) == 1
    assignment = assignments[0]
    assert ast.unparse(assignment) == "elapsed, failed = await replay_quality_slice(prefix)", (
        "timing must consume the shared real full-corpus replay"
    )
    assert any(
        isinstance(node, ast.Assert) and ast.unparse(node.test) == "failed == []"
        for node in fn.body
    ), "perf replay must retain every correctness failure"
    assert any(
        isinstance(node, ast.Assert)
        and isinstance(node.test, ast.Compare)
        and ast.unparse(node.test) == "elapsed < 45"
        for node in fn.body
    ), "original strict 45s budget must remain enforced"


def test_quality_corpus_correctness_and_perf_are_both_required() -> None:
    source = (PERF_DIR / "test_quality_scenario_corpus_perf.py").read_text(encoding="utf-8")
    _assert_quality_corpus_perf_contract(source)
    correctness = REPO_ROOT / "tests/quality/test_quality_scenario_corpus.py"
    tree = ast.parse(correctness.read_text(encoding="utf-8"))
    functions = {
        node.name: node for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
    }
    assert any(
        isinstance(d, ast.Call)
        and ast.unparse(d.func) == "pytest.mark.parametrize"
        and ast.literal_eval(d.args[0]) == "prefix"
        and ast.literal_eval(d.args[1]) == ["basemap_", "science_"]
        for d in functions["test_slice_geometry_states_green"].decorator_list
    ), "both geometry slices must remain in covered correctness"
    for name, argument in (
        ("test_slice_geometry_states_green", "prefix"),
        ("test_slice_data_states_green", "'data_'"),
    ):
        fn = functions[name]
        assert not any("perf" in ast.unparse(d) or "skip" in ast.unparse(d)
                       for d in fn.decorator_list)
        assert any(
            isinstance(node, ast.Await)
            and ast.unparse(node.value) == f"replay_quality_slice({argument})"
            for node in ast.walk(fn)
        ), "covered correctness must still execute every original slice"
        assert any(
            isinstance(node, ast.Assert) and ast.unparse(node.test) == "failed == []"
            for node in fn.body
        ), "covered replay must assert every real case failure"
    assert not any(
        isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets)
        for node in tree.body
    ), "do not move the correctness module out of the coverage lane"
    assert "tests/quality" in _job_run_text("test-backend")
    assert "test-perf" in _workflow()["jobs"]["release-gate"]["needs"]


@pytest.mark.parametrize(
    "before,after",
    [
        ('["basemap_", "science_", "data_"]', '["basemap_", "science_"]'),
        ("elapsed < 45", "elapsed < 90"),
        ("failed == []", "True"),
        ("await replay_quality_slice(prefix)", "0.0"),
        ("pytestmark = pytest.mark.perf", "pytestmark = pytest.mark.asyncio"),
    ],
)
def test_quality_corpus_perf_mutations_fail_closed(before: str, after: str) -> None:
    source = (PERF_DIR / "test_quality_scenario_corpus_perf.py").read_text(encoding="utf-8")
    assert before in source
    with pytest.raises(AssertionError):
        _assert_quality_corpus_perf_contract(source.replace(before, after, 1))


def test_quality_corpus_perf_missing_from_pr_lane_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    workflow = _workflow()
    filename = "tests/benchmarks/test_quality_scenario_corpus_perf.py"
    for step in workflow["jobs"]["test-perf"]["steps"]:
        if filename in step.get("run", ""):
            step["run"] = step["run"].replace(filename, "", 1)
    patched = tmp_path / "production.yml"
    patched.write_text(yaml.safe_dump(workflow), encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "WORKFLOW", patched)
    with pytest.raises(AssertionError, match="test_quality_scenario_corpus_perf.py"):
        test_test_perf_pr_lane_runs_fast_subset_no_cov()


def _closest_facility_sources() -> tuple[str, str]:
    unit = REPO_ROOT / "tests/unit/test_network_issue600_need_paths_accumulation.py"
    perf = PERF_DIR / "test_network_closest_facility_perf.py"
    return unit.read_text(encoding="utf-8"), perf.read_text(encoding="utf-8")


def _assert_closest_facility_budget_contract(unit_source: str, perf_source: str) -> None:
    """Both lanes must run the same original cold workload, with a strict 2s gate."""
    unit_tree, perf_tree = ast.parse(unit_source), ast.parse(perf_source)
    unit_functions = {n.name: n for n in unit_tree.body if isinstance(n, ast.FunctionDef)}
    helper = unit_functions["run_closest_facility_8k_chain"]
    # Lock the original inputs, fresh service, single real invocation and timer
    # scope. This rejects hidden warmups, smaller graphs and constant timings.
    expected_workload = ast.parse("""
g, ds = _chain(8000)
svc = NetworkClosestFacilityService()
demand = DemandPoint(demand_id="d0", weight=1.0,
    geometry={"type": "Point", "coordinates": [116.0, 39.0]})
facilities = [Facility(facility_id="f0",
    geometry={"type": "Point", "coordinates": [116.7999, 39.0]})]
t0 = time.perf_counter()
res = svc.network_closest_facility(
    demand_points=[demand], facilities=facilities, graph=g, network_dataset=ds,
    target_facility_count=1)
elapsed = time.perf_counter() - t0
return res, elapsed
""").body
    body = helper.body[1:] if ast.get_docstring(helper) is not None else helper.body
    assert [ast.dump(n) for n in body] == [ast.dump(n) for n in expected_workload], (
        "preserve the original cold 8k workload and complete service timer"
    )
    assert not helper.decorator_list
    assert not any(
        isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in n.targets)
        for n in unit_tree.body
    ), "8k correctness must remain in covered unit tests"
    assert any(
        isinstance(n, ast.Assign) and ast.unparse(n) == "pytestmark = pytest.mark.perf"
        for n in perf_tree.body
    ), "the 2s gate must be explicitly perf-marked"
    assert any(
        isinstance(n, ast.ImportFrom)
        and n.module == "tests.unit.test_network_issue600_need_paths_accumulation"
        and any(a.name == "run_closest_facility_8k_chain" and a.asname is None for a in n.names)
        for n in perf_tree.body
    ), "perf must import the shared real workload"
    correctness = unit_functions["test_closest_facility_8k_chain_routes_are_correct"]
    perf = next(n for n in perf_tree.body if isinstance(n, ast.FunctionDef)
                and n.name == "test_closest_facility_8k_chain_under_acceptance_bound")
    correctness_assertions = [
        "len(res.routes) == 1",
        "res.routes[0].total_distance_m == pytest.approx(8000 * 8.3, rel=0.01)",
    ]
    for fn, assignment, assertions in (
        (correctness, "res, _ = run_closest_facility_8k_chain()", correctness_assertions),
        (perf, "res, elapsed = run_closest_facility_8k_chain()", correctness_assertions + ["elapsed < 2.0"]),
    ):
        assert not fn.decorator_list, "neither lane may skip or reclassify the original scenario"
        assert len(fn.body) == 1 + len(assertions)
        assert ast.unparse(fn.body[0]) == assignment, "both lanes must execute the shared workload"
        assert all(isinstance(n, ast.Assert) for n in fn.body[1:])
        assert [ast.unparse(n.test) for n in fn.body[1:]] == assertions, (
            "keep both original correctness assertions and the strict 2.0s perf bound"
        )


def test_closest_facility_correctness_and_original_budget_are_required() -> None:
    _assert_closest_facility_budget_contract(*_closest_facility_sources())
    run = _job_run_text("test-perf")
    assert "tests/benchmarks/test_network_closest_facility_perf.py" in run
    assert "-m perf" in run and "--no-cov" in run
    assert "tests/unit/test_*.py" in _job_run_text("test-backend")
    assert "test-perf" in _workflow()["jobs"]["release-gate"]["needs"]


@pytest.mark.parametrize("source,before,after", [
    ("unit", "_chain(8000)", "_chain(4000)"),
    ("unit", "svc = NetworkClosestFacilityService()", "svc = cached_service"),
    ("unit", "elapsed = time.perf_counter() - t0", "elapsed = 0.0"),
    ("unit", "return res, elapsed", "return res, 0.0"),
    ("unit", "[116.7999, 39.0]", "[116.0001, 39.0]"),
    ("unit", "target_facility_count=1,", "target_facility_count=0,"),
    ("unit", "assert len(res.routes) == 1", "assert True"),
    ("unit", "def test_closest_facility_8k_chain_routes_are_correct", "@pytest.mark.perf\ndef test_closest_facility_8k_chain_routes_are_correct"),
    ("perf", "elapsed < 2.0", "elapsed < 4.0"),
    ("perf", "elapsed < 2.0", "elapsed <= 2.0"),
    ("perf", "pytest.approx(8000 * 8.3, rel=0.01)", "pytest.approx(4000 * 8.3, rel=0.01)"),
    ("perf", "assert len(res.routes) == 1", "assert True"),
    ("perf", "res, elapsed = run_closest_facility_8k_chain()", "res, elapsed = None, 0.0"),
    ("perf", "pytestmark = pytest.mark.perf", "pytestmark = pytest.mark.asyncio"),
])
def test_closest_facility_budget_mutations_fail_closed(source: str, before: str, after: str) -> None:
    unit, perf = _closest_facility_sources()
    original = unit if source == "unit" else perf
    assert before in original
    changed = original.replace(before, after, 1)
    with pytest.raises(AssertionError):
        _assert_closest_facility_budget_contract(changed if source == "unit" else unit,
                                               changed if source == "perf" else perf)


@pytest.mark.parametrize("mutation", ["omit_file", "enable_coverage", "optional_gate"])
def test_closest_facility_perf_lane_mutations_fail_closed(
    mutation: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    workflow = _workflow()
    filename = "tests/benchmarks/test_network_closest_facility_perf.py"
    if mutation == "optional_gate":
        workflow["jobs"]["release-gate"]["needs"].remove("test-perf")
    else:
        for step in workflow["jobs"]["test-perf"]["steps"]:
            if filename in step.get("run", ""):
                before = filename if mutation == "omit_file" else "--no-cov"
                step["run"] = step["run"].replace(before, "", 1)
    patched = tmp_path / "production.yml"
    patched.write_text(yaml.safe_dump(workflow), encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "WORKFLOW", patched)
    with pytest.raises(AssertionError):
        test_closest_facility_correctness_and_original_budget_are_required()


def test_nightly_only_perf_files_are_documented_and_excluded_from_pr():
    """#618-29: remaining perf-marked files stay nightly because of timeouts.

    PR lane is an explicit file list (fast / structural). Nightly
    ``-m "cartography or perf"`` owns the wall-clock subset.
    """
    pr_run = _job_run_text("test-perf")
    nightly_run = _job_run_text("nightly-matrix")
    assert '-m "cartography or perf"' in nightly_run
    marked = _perf_marked_files()
    for name, reason in NIGHTLY_ONLY_PERF_FILES.items():
        assert name in marked, f"{name} is documented nightly-only but lost its perf mark"
        assert name not in pr_run, (
            f"{name} must stay nightly-only ({reason}) — do not add it to "
            "the PR test-perf file list"
        )
        assert reason.strip()
    unmarked_nightly = (
        marked
        - set(PR_LANE_PERF_FILES)
        - set(NIGHTLY_ONLY_PERF_FILES)
    )
    assert not unmarked_nightly, (
        "perf-marked file is neither on the PR lane nor documented as "
        f"nightly-only (timeouts): {sorted(unmarked_nightly)}"
    )


def test_backend_lane_excludes_perf_marker():
    run = _job_run_text("test-backend")
    assert "not perf" in run


def test_perf_harness_v2_is_perf_marked_and_nightly_runs_it_no_cov():
    src = (PERF_DIR / "test_perf_harness_v2.py").read_text(encoding="utf-8")
    assert "pytestmark = pytest.mark.perf" in src, (
        "v2 必须带 perf marker（否则被 test-backend 在 --cov 下收集，墙钟断言 flaky）"
    )
    nightly_run = _job_run_text("nightly-matrix")
    assert '-m "cartography or perf"' in nightly_run
    assert "--no-cov" in nightly_run


def test_backend_coverage_gate_is_non_decorative():
    """#618-29: contract must match production.yml's ratchet (75), not a
    decorative floor of 10. Do not raise this number independently of
    the workflow — if CI moves the gate, move both together.
    """
    run = _job_run_text("test-backend-gate")
    assert "combine_backend_coverage.py combine" in run
    m = re.search(r"--fail-under[=\s]+(\d+)", run)
    assert m, "required backend gate must enforce combined coverage"
    assert int(m.group(1)) >= 75, (
        "combined backend coverage must retain the 75% ratchet; "
        f"got {m.group(1)}"
    )
    assert "--cov-fail-under" not in _job_run_text("test-backend"), (
        "A partial shard is not the full-application coverage gate"
    )


def test_frontend_vitest_has_real_coverage_thresholds():
    cfg = (REPO_ROOT / "frontend" / "vitest.config.ts").read_text(encoding="utf-8")
    m = re.search(r"thresholds:\s*\{(.*?)\}", cfg, re.S)
    assert m, "vitest.config.ts 必须声明 coverage.thresholds"
    block = m.group(1)
    for key in ("lines", "functions", "statements", "branches"):
        km = re.search(rf"{key}:\s*(\d+)", block)
        assert km and int(km.group(1)) > 0, f"thresholds.{key} 必须为正数"


@pytest.mark.parametrize(
    ("pkg", "expected"),
    [
        # extras 形态（#1656）：[...] 必须剥离，否则 covered 集合与
        # import 名（sqlalchemy）永远对不上，主断言恒红
        ("sqlalchemy[asyncio]>=2.0.25,<2.1", "sqlalchemy"),
        ("sqlalchemy[asyncio]", "sqlalchemy"),
        ("psycopg[binary]>=3.1", "psycopg"),
        ("uvicorn[standard]>=0.27.0", "uvicorn"),
        ("pkg[extra1,extra2]", "pkg"),
        # 既有归一化语义不回退
        ("pytest-asyncio==1.4.0", "pytest_asyncio"),
        ("psycopg2-binary", "psycopg2"),
        ("types-requests", "types_requests"),
        ("pytest==9.1.1", "pytest"),
    ],
)
def test_module_of_pip_package_normalization(pkg, expected):
    assert _module_of_pip_package(pkg) == expected
