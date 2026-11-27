"""ads-v1 成本看板脚本回归（DS8, ADR-0178）。

锁住 P3 dead-code 修复：``main()`` 曾把整张验证矩阵（864 组）原样再跑
一遍、结果累加进从未读取的 ``agg`` 后全弃。看板只应经由 ``run_matrix``
执行一次矩阵，不得自行重跑 ``run_group``。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

from app.services.data_fabric import matrix as matrix_mod

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ads_cost_dashboard.py"

#: 12 源 × 12 请求型 × 3 场景 × 2 语言 = 864 组（matrix.py 的矩阵维度）。
GROUPS = 12 * 12 * 3 * 2


def _load_dashboard_module():
    spec = importlib.util.spec_from_file_location("ads_cost_dashboard_mod", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("ads_cost_dashboard_mod", module)
    spec.loader.exec_module(module)
    return module


def test_main_runs_matrix_exactly_once(monkeypatch, tmp_path):
    dash = _load_dashboard_module()
    calls = {"groups": 0, "matrix": 0}

    def fake_run_group(*args, **kwargs):
        calls["groups"] += 1
        return SimpleNamespace(details={"cost_rows": 128})

    def fake_run_matrix(*args, **kwargs):
        calls["matrix"] += 1
        for source in matrix_mod.SOURCES:
            for rt in matrix_mod.REQUEST_TYPES:
                for scenario in matrix_mod.SCENARIOS:
                    for lang in matrix_mod.LANGUAGES:
                        fake_run_group(source, rt, scenario, lang)
        return {
            "total": GROUPS, "passed": GROUPS, "failed": 0,
            "core_total": 216, "core_passed": 216,
        }

    monkeypatch.setattr(matrix_mod, "run_matrix", fake_run_matrix)
    monkeypatch.setattr(matrix_mod, "run_group", fake_run_group)
    # 看板自身不得再引一份 run_group 重跑矩阵（P3：死聚合循环）。
    # raising=False：修复后该属性已从模块命名空间移除。
    monkeypatch.setattr(dash, "run_group", fake_run_group, raising=False)
    monkeypatch.setattr(dash, "OUT", tmp_path / "ads-v1-cost-dashboard.md")
    monkeypatch.setattr(dash, "REPO", tmp_path)

    assert dash.main() == 0
    assert calls["matrix"] == 1, "validation matrix must run exactly once"
    assert calls["groups"] == GROUPS, (
        f"matrix executed {calls['groups']} groups (expected {GROUPS}) — "
        "the dashboard re-runs the matrix itself (dead aggregation loop)"
    )
