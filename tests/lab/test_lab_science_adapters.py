"""Science oracle / 视觉 fixture 接线测试（E15 D8）。

science oracles 的加载器在 tests 域 —— lab 经 adapter 注册消费（本文件
即"外部注册方"）。app 侧零静态 tests 依赖：ScienceOracleAdapter 从
scripts/eval_lab.py 以文件路径惰性装载（与 CLI 同一装载路径）。
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

from app.lib.harness.lab.adapters import register_adapter
from app.lib.harness.lab.journeys import default_spec_paths, load_specs
from app.lib.harness.lab.runner import LabRunner

REPO = Path(__file__).resolve().parents[2]


def _load_science_adapter():
    scripts_dir = REPO / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    spec = importlib.util.spec_from_file_location(
        "eval_lab_module", scripts_dir / "eval_lab.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ScienceOracleAdapter()


@pytest.fixture(scope="module")
def science_specs():
    register_adapter(_load_science_adapter())
    specs = {s.spec_id: s for s in load_specs(default_spec_paths())}
    return specs["lab-j8-science-oracle-crs"]


def _run(coro):
    return asyncio.run(coro)


class TestScienceOracleIntegration:
    @pytest.mark.asyncio
    async def test_crs_domain_passes_under_unified_runner(
            self, science_specs):
        ev = await LabRunner(seed=0).run_spec(science_specs)
        semantic = ev.dimensions["gis_semantic_correctness"]
        assert semantic.status == "pass", ev.detail_failures
        obs = ev.observations.get("science_oracles") or {}
        assert obs.get("domains") == ["crs_units"]
        assert obs.get("cases", 0) > 0
        assert obs.get("passed") == obs.get("cases")

    @pytest.mark.asyncio
    async def test_unknown_domain_is_honest_violation(
            self, science_specs):
        from app.lib.harness.lab.spec import LabScenario

        ghost = LabScenario.from_dict({
            "spec_id": "ghost-science", "title": "t", "kind": "replay",
            "science_oracle_domains": ["no_such_domain"],
            "turns": [{"user_input": "u", "ops": []}],
        })
        adapter = _load_science_adapter()
        outcome = await adapter(ghost)
        assert any("no_such_domain" in v for v in outcome.violations)
        assert outcome.contributions == []  # 无 case → 无裁决贡献


class TestVisualFixtureIntegration:
    @pytest.mark.asyncio
    async def test_visual_judge_spec_drives_fake_vlm(self):
        specs = {s.spec_id: s for s in load_specs(default_spec_paths())}
        ev = await LabRunner(seed=0).run_spec(
            specs["lab-j1-chengdu-schools-distribution"])
        obs = ev.observations.get("visual_fixture") or {}
        assert obs.get("visual_status") == "evaluated"
        assert obs.get("fake_calls", 0) >= 1
        assert ev.dimensions["cartographic_compliance"].status == "pass"
