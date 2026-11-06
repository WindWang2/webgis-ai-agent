"""链② 栅格→zonal→矢量（ADR-0282 D3/D4；断点 raster 链不入编排 + 覆盖率）。

- raster_zonal_summary 新族：subject(raster) ref 直通（不水合为 dict）+
  boundary 面域 → zonal_stats → 带 per-zone 统计的矢量 ref（MapSpec-ready）；
- optional resample 步剔除后 zonal 步回接 role:subject（rewire 语义）；
- zonal_statistics 逐 zone valid_pixel_coverage 披露（nodata 不可区分修复）；
- role_kind plan 门：subject 需求 raster 而绑定 vector → typed blocked。
"""
from __future__ import annotations

from typing import Dict

import numpy as np
import pytest

from app.services.gis_harness.analysis_workflows.contracts import (
    AWF_ROLE_KIND_MISMATCH,
    DataRoleBinding,
)
from app.services.gis_harness.analysis_workflows.executor import (
    WorkflowExecutor,
)
from app.services.gis_harness.analysis_workflows.families import (
    FAMILY_IDS,
    build_contract,
)
from app.services.gis_harness.analysis_workflows.orchestrator import (
    plan_workflow,
)
from app.services.gis_harness.analysis_workflows.state import (
    apply_plan_verdicts,
    new_state,
)
from app.services.session_data import session_data_manager

SESSION = "awf-raster-test-session"


def _districts_fc(n: int = 2) -> dict:
    """两 zone：西半 nw（x 104.00-104.02）与东半 ne（104.02-104.04），
    均覆盖整个栅格高度 y[30.600,30.632]（每 zone 恰罩住 2 个像元列带）。"""
    quads = [
        (104.00, 30.600, "nw"), (104.02, 30.600, "ne"),
    ][:n]
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "properties": {"district": name},
             "geometry": {"type": "Polygon", "coordinates": [[
                 [x, y], [x + 0.02, y], [x + 0.02, y + 0.032],
                 [x, y + 0.032], [x, y]]]}}
            for x, y, name in quads],
    }


@pytest.fixture
def raster_path() -> str:
    """合成 NDVI 栅格：EPSG:4326，2×2 像素（0.02°×0.016°），
    覆盖 x[104.00,104.04] × y[30.600,30.632]。

    列值：西列=1.0、东列=2.0；西北像素（row0,col0）为 nodata——
    nw zone（西半，2 像素）→ 1 有效 → coverage 0.5、mean 1.0；
    ne zone（东半）→ 全有效 → coverage 1.0、mean 2.0。

    写入仓库 data/（gitignored；栅格工具路径沙箱只允许 data/tmp 目录，
    与 G05 runner 的 ./tmp/g05-rasters 同纪律）。
    """
    from pathlib import Path

    import rasterio
    from rasterio.transform import from_origin

    data = np.array([[1.0, 2.0], [1.0, 2.0]], dtype="float32")
    nodata = -9999.0
    data[0, 0] = nodata
    transform = from_origin(104.00, 30.632, 0.02, 0.016)
    root = Path.cwd() / "data" / "awf-test-rasters"
    root.mkdir(parents=True, exist_ok=True)
    path = str(root / "ndvi_2x2.tif")
    with rasterio.open(
            path, "w", driver="GTiff", height=2, width=2,
            count=1, dtype="float32", crs="EPSG:4326",
            transform=transform, nodata=nodata) as dst:
        dst.write(data, 1)
    return path


@pytest.fixture
def registry():
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry
    r = ToolRegistry()
    init_tools(r)
    return r


async def _store(data: dict) -> str:
    return await session_data_manager.store(SESSION, data, prefix="geojson")


# ── 1. 覆盖率披露（raster_ops 层）─────────────────────────────────────────

def test_zonal_valid_pixel_coverage_disclosure(raster_path):
    from app.lib.geo_analysis.raster_ops import zonal_statistics

    rows = zonal_statistics(_districts_fc(), raster_path)
    assert len(rows) == 2
    by_cov = sorted(rows, key=lambda r: (r.get("valid_pixel_coverage") or 0))
    # nw：北像元 nodata → 覆盖率 0.5、mean=1.0（仅南像元有效）
    # ne：全有效 → 覆盖率 1.0、mean=2.0；nodata 区不再与全有效区不可区分
    assert by_cov[0]["valid_pixel_coverage"] == pytest.approx(0.5)
    assert by_cov[0]["mean"] == pytest.approx(1.0)
    assert by_cov[1]["valid_pixel_coverage"] == pytest.approx(1.0)
    assert by_cov[1]["mean"] == pytest.approx(2.0)


# ── 2. 族模板与 rewiring ──────────────────────────────────────────────────

def test_raster_zonal_summary_family_registered_and_valid():
    assert "raster_zonal_summary" in FAMILY_IDS
    contract = build_contract(
        "raster_zonal_summary", workflow_id="awf.rzs.selfcheck",
        params_overrides={"resample": {"target_resolution": 0.002}})
    step_ids = [s.step_id for s in contract.steps]
    assert step_ids == ["resample", "zonal_stats"]
    zonal = contract.steps[1]
    assert zonal.role_inputs["raster_path"] == "step:resample"
    assert zonal.role_inputs["geojson"] == "role:boundary"
    # optional resample 剔除 → zonal 回接 role:subject（rewire）
    contract2 = build_contract(
        "raster_zonal_summary", workflow_id="awf.rzs.selfcheck2",
        include_optional_steps=False)
    assert [s.step_id for s in contract2.steps] == ["zonal_stats"]
    assert contract2.steps[0].role_inputs["raster_path"] == "role:subject"


# ── 3. role_kind plan 门 ─────────────────────────────────────────────────

def test_plan_role_kind_mismatch_typed_blocked():
    plan = plan_workflow(
        "raster_zonal_summary",
        workflow_id="awf.rzs.kind",
        bindings={
            "subject": DataRoleBinding(
                role="subject", ref="ref:geojson-vector"),   # vector 绑定
            "boundary": DataRoleBinding(role="boundary", ref="ref:zones"),
        })
    assert not plan.runnable
    assert any(
        v.reason_code.startswith(AWF_ROLE_KIND_MISMATCH)
        for v in plan.step_verdicts)


# ── 4. 端到端：ref 直通 + envelope 栅格事实 + 矢量结果 ───────────────────

async def test_raster_zonal_summary_e2e(registry, raster_path):
    zones_ref = await _store(_districts_fc())
    bindings: Dict[str, DataRoleBinding] = {
        "subject": DataRoleBinding(
            role="subject", ref=raster_path, role_kind="raster"),
        "boundary": DataRoleBinding(
            role="boundary", ref=zones_ref,
            qualifier={"geometry_types": ["Polygon"]}),
    }
    plan = plan_workflow(
        "raster_zonal_summary",
        workflow_id="awf.rzs.e2e",
        bindings=bindings,
        include_optional_steps=False)
    assert plan.runnable, plan.blocked_reasons
    state = new_state(plan.contract, contract_fingerprint=plan.fingerprint)
    apply_plan_verdicts(state, plan.step_verdicts)
    executor = WorkflowExecutor(registry=registry,
                                session_data=session_data_manager)
    state = await executor.execute(SESSION, plan.contract, state)
    assert state.steps["zonal_stats"].state == "satisfied", (
        state.steps["zonal_stats"].message)
    env = state.envelopes["zonal_stats"]
    assert env.aggregation_basis == "zonal_mean"
    assert env.feature_count == 2
    assert env.ref.startswith("ref:")
    # 矢量结果落 store：per-zone 均值/覆盖率可回读（nw=1.0/0.5，ne=2.0/1.0）
    payload = await session_data_manager.get(SESSION, env.ref)
    means = sorted(
        f["properties"]["mean"] for f in payload["features"])
    assert means == pytest.approx([1.0, 2.0])
    covs = sorted(
        f["properties"]["valid_pixel_coverage"]
        for f in payload["features"])
    assert covs[0] == pytest.approx(0.5)
    assert covs[1] == pytest.approx(1.0)
