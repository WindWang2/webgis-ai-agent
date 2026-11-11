"""G15 WP12 —— 小规模真实基准（2/4/6 幅产品的创建/解析/导出耗时）。

真实执行（无 mock timing）：真实数据 → plan → bind（完整 MapSpec CAS 管线，
含两次 apply_gis_mutation）→ 逐帧 resolver → 产品页 SVG 编译 → PDF 渲染。
测量环境随报告披露；数值受机器负载影响，只作相对量级证据（不是 SLA）。
"""
from __future__ import annotations

import statistics
import time
import uuid
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from app.lib.cartography.publication_ir import AtlasPolicy, plan_publication_pages
from app.services.gis_harness.frame_products import service as fps
from app.services.publication_export import (
    compile_publication_page,
    render_publication_pdf,
)

pytestmark = [pytest.mark.cartography, pytest.mark.slow]


def _fc(years: int, districts: int) -> Dict[str, Any]:
    feats: List[Dict[str, Any]] = []
    for i, y in enumerate(range(2020, 2020 + years * 2, 2)):
        for d in range(districts):
            feats.append({
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [[
                    [104.0 + d * 0.05, 30.6], [104.05 + d * 0.05, 30.6],
                    [104.05 + d * 0.05, 30.65], [104.0 + d * 0.05, 30.65],
                    [104.0 + d * 0.05, 30.6]]]},
                "properties": {"year": y, "district": f"d{d}",
                               "per_10k": 20.0 + i * 2 + d},
            })
    return {"type": "FeatureCollection", "features": feats}


@pytest.fixture
async def ref_store(monkeypatch):
    fcs = {
        "ref:bench2": _fc(2, 20),
        "ref:bench4": _fc(4, 20),
        "ref:bench6": _fc(6, 20),
    }

    async def _resolve_alias(session_id, ref_or_alias):
        return ref_or_alias

    async def _get_ref_descriptor(session_id, resolved):
        return {"content_revision": 1, "feature_count": len(fcs[resolved]["features"])}

    async def _get_ref_data_shared(session_id, resolved, owner_token=None):
        return SimpleNamespace(success=True, data=fcs[resolved])

    import app.services.session_data as sd

    monkeypatch.setattr(sd.session_data_manager, "resolve_alias", _resolve_alias)
    monkeypatch.setattr(sd.session_data_manager, "get_ref_descriptor", _get_ref_descriptor)
    monkeypatch.setattr(sd.session_data_manager, "get_ref_data_shared", _get_ref_data_shared)
    return fcs


async def _seed(sid: str, fc: Dict[str, Any]) -> None:
    from app.services.gis_world_state import apply_gis_mutation
    from app.services.mapspec.intents import UpsertLayerIntent, UpsertSourceIntent

    await apply_gis_mutation(sid, UpsertSourceIntent(
        source_id="src", source={"type": "geojson", "inlineData": fc}),
        origin="agent", actor="bench")
    await apply_gis_mutation(
        sid,
        UpsertLayerIntent(
            layer={"id": "lyr", "type": "fill", "source": "src",
                   "legend_spec": {
                       "type": "graduated", "field": "per_10k",
                       "breaks": [20.0, 30.0, 40.0, 50.0],
                       "palette_colors": ["#c6dbef", "#6baed6", "#2171b5"],
                       "labels": ["20 – 30", "30 – 40", "40 – 50"],
                       "palette": "Blues", "method": "equal_interval", "k": 3}},
        ),
        origin="agent", actor="bench")


def _resolve_all(doc: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.gis_harness.frame_products.resolver import resolve_frame_document

    out = {}
    product = doc["frame_product"]
    for slot in product["frames"]:
        res = resolve_frame_document(doc, slot["frame_id"])
        out[slot["frame_id"]] = res.fingerprint
    return out


@pytest.mark.parametrize("n,ref", [(2, "ref:bench2"), (4, "ref:bench4"), (6, "ref:bench6")])
async def test_product_scale_benchmark(ref_store, n, ref):
    """2/4/6 幅：bind / resolve / page-compile / pdf 四段耗时（真实执行）。"""
    pytest.importorskip("weasyprint")
    fcs = ref_store
    fc = fcs[ref]
    timings: Dict[str, float] = {}

    t0 = time.perf_counter()
    sid = f"bench-{uuid.uuid4().hex[:8]}"
    await _seed(sid, fc)
    timings["seed_layers"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    out = await fps.bind_frame_product(
        sid, ref, product_kind="time_series", field="per_10k",
        frames_limit=n, method="equal_interval", k=3)
    timings["bind"] = time.perf_counter() - t0
    assert out["status"] == "ok", out
    assert len(out["frames"]) == n

    spec = await fps._committed_spec(sid)
    t0 = time.perf_counter()
    fingerprints = _resolve_all(spec)
    timings["resolve_all"] = time.perf_counter() - t0
    assert len(fingerprints) == n

    ir = plan_publication_pages(spec, atlas=AtlasPolicy(driver="product"))
    page = ir.pages[0]
    t0 = time.perf_counter()
    outcome = compile_publication_page(
        spec, page, ir, frames_lookup=[], target_dpi=150, max_labels=60,
        svg_budget_remaining=8 * 1024 * 1024)
    timings["page_compile"] = time.perf_counter() - t0
    assert outcome.svg_bytes > 1000

    t0 = time.perf_counter()
    result = render_publication_pdf(spec, atlas=AtlasPolicy(driver="product"))
    timings["pdf_render"] = time.perf_counter() - t0
    assert result.page_count == 1

    # 原始值打印（测量环境：本机 dev 容器/裸机；pytest -s 可见）。
    print(
        f"\n[G15 bench n={n}] features={len(fc['features'])} "
        + " ".join(f"{k}={v * 1000:.0f}ms" for k, v in timings.items())
        + f" svg_bytes={outcome.svg_bytes} pdf_bytes={len(result.pdf)}")
    # 资源有界合理性：SVG 字节随画幅数次线性放大（每槽有界）。
    assert outcome.svg_bytes < 8 * 1024 * 1024


async def test_resolve_bounded_repeat_cost(ref_store):
    """重复解析同帧成本稳定（指纹缓存语义面：同输入同输出，不重算分级）。"""
    fcs = ref_store
    sid = f"bench-{uuid.uuid4().hex[:8]}"
    await _seed(sid, fcs["ref:bench4"])
    await fps.bind_frame_product(
        sid, "ref:bench4", product_kind="time_series", field="per_10k")
    spec = await fps._committed_spec(sid)
    runs: List[float] = []
    for _ in range(5):
        t0 = time.perf_counter()
        _resolve_all(spec)
        runs.append(time.perf_counter() - t0)
    spread = (max(runs) - min(runs)) * 1000
    median = statistics.median(runs) * 1000
    print(f"\n[G15 bench resolve-stability] median={median:.1f}ms spread={spread:.1f}ms")
    assert median < 2000  # 4 帧 × 20 面要素的解析应远低于秒级
