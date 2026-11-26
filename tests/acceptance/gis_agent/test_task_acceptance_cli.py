"""scripts/g05_task_acceptance.py 的选场景契约（薄壳行为，不跑真实 dispatch）。

smoke profile 的 SMOKE_IDS 与语料脱同步（改名/删场景）时必须给出与
--scenario 一致的干净 SystemExit，而不是裸 KeyError 堆栈。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.cartography

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "g05_task_acceptance.py"


def _load_cli():
    spec = importlib.util.spec_from_file_location("g05_cli_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def cli():
    return _load_cli()


def _stubs(ids: list[str]) -> list:
    """_select 只读 .id 的最小替身，避免拉起真实语料。"""
    return [SimpleNamespace(id=i) for i in ids]


class TestSmokeSelection:
    def test_smoke_ids_all_exist_in_real_corpus(self, cli):
        """漂移守卫：SMOKE_IDS 必须是语料 id 的子集（否则 smoke profile 跑不动）。"""
        from . import corpus
        known = {s.id for s in corpus.all_scenarios()}
        stale = [i for i in cli.SMOKE_IDS if i not in known]
        assert not stale, f"SMOKE_IDS 引用了不存在的场景：{stale}"

    def test_smoke_profile_returns_declared_order(self, cli, monkeypatch):
        monkeypatch.setattr(cli, "_load_corpus", lambda: _stubs(cli.SMOKE_IDS))
        assert [s.id for s in cli._select("smoke", None)] == cli.SMOKE_IDS

    def test_smoke_missing_id_raises_clean_system_exit(self, cli, monkeypatch):
        dropped = cli.SMOKE_IDS[-1]
        monkeypatch.setattr(
            cli, "_load_corpus",
            lambda: _stubs([i for i in cli.SMOKE_IDS if i != dropped]))
        with pytest.raises(SystemExit) as excinfo:
            cli._select("smoke", None)
        assert dropped in str(excinfo.value)

    def test_smoke_and_scenario_paths_report_unknown_ids_identically(
            self, cli, monkeypatch):
        """两处报错必须同形：--scenario 是干净报错，smoke 不能是裸 KeyError。"""
        dropped = cli.SMOKE_IDS[0]
        monkeypatch.setattr(
            cli, "_load_corpus",
            lambda: _stubs([i for i in cli.SMOKE_IDS if i != dropped]))
        with pytest.raises(SystemExit) as smoke_exc:
            cli._select("smoke", None)
        with pytest.raises(SystemExit) as only_exc:
            cli._select("smoke", [dropped])
        assert str(smoke_exc.value) == str(only_exc.value)
