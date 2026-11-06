"""Static integration checks share one consistent artifact snapshot per run."""
import importlib.util
from pathlib import Path
import subprocess


def test_preflight_hashes_artifacts_once(monkeypatch):
    from app.lib.quality import artifact_graph

    calls = []

    def snapshot():
        calls.append(True)
        return {}

    monkeypatch.setattr(artifact_graph, "build_graph_state", snapshot)
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "preflight_snapshot_probe", root / "scripts/check_integration_preflight.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = module.run_preflight(root)
    assert len(calls) == 1
    assert {row["check"] for row in report["checks"]} >= {
        "generated_staleness", "generated_hand_edits"}


def test_ownership_inventory_does_not_visit_ignored_runtime_tree(tmp_path, monkeypatch):
    from app.lib.integration.ownership import _walk_repo_bounded

    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    (tmp_path / '.gitignore').write_text('data/\n')
    (tmp_path / 'source.py').write_text('source = True\n')
    runtime = tmp_path / 'data'
    runtime.mkdir()
    original = Path.iterdir

    def checked(path):
        if path == runtime:
            raise AssertionError('mutable runtime tree must not be inventoried')
        return original(path)

    monkeypatch.setattr(Path, 'iterdir', checked)
    files = _walk_repo_bounded(tmp_path)
    assert 'source.py' in files
    assert not any(name.startswith('data/') for name in files)


def test_selector_keeps_coarse_mapping_when_detail_budget_is_full(tmp_path):
    from app.lib.integration.impact import ImportGraph, select_tests

    (tmp_path / 'tests/unit/tools').mkdir(parents=True)
    graph = ImportGraph(tests={f'tests/test_{i}.py': ['app.tools.example'] for i in range(10)})
    result = select_tests(['app/tools/example.py', 'app/core/example.py'],
                          repo_root=tmp_path, graph=graph, max_targets=2)
    assert set(result.targets) == {'tests', 'tests/unit/tools'}


def test_changed_test_is_selected_directly(tmp_path):
    from app.lib.integration.impact import ImportGraph, select_tests

    (tmp_path / 'tests').mkdir()
    (tmp_path / 'tests/test_changed.py').write_text('def test_changed(): assert True\n')
    result = select_tests(['tests/test_changed.py'], repo_root=tmp_path, graph=ImportGraph())
    assert result.complete
    assert result.targets == ['tests/test_changed.py']
