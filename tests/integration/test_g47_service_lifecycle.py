"""G47 领域服务生命周期集成（WP08 / WP09 / WP11 的服务面）。

打的是**生产服务函数**，不是路由替身：``inspect_layer`` → ``plan_layout`` →
``start_layout``（durable job）→ ``layout_status`` → ``load_layout`` →
``apply_layout``（单笔 CAS + checkpoint + undo）→ ``export_layout``。

四个"不能糊过去"的点：

* **作业**：完成不等于已应用，也不等于 publication-ready；
* **CAS**：修订不符时**什么都不写**（revision 与层证据块都不变）；
* **代次**：源被改写后旧布局不能贴上来（``STALE_SOURCE``）；
* **归属**：``layout_ref`` 只在所属会话可读 —— ID 不是权限。
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from app.services.cartogram_authoring import (
    AuthoringError,
    apply_layout,
    export_layout,
    get_layout,
    inspect_layer,
    layout_status,
    list_layouts,
    load_layout,
    parse_plan,
    plan_layout,
    start_layout,
)

LAYER_ID = "g47-layer"
PLAN = {
    "schema_version": 1,
    "mode": "dorling_circle",
    "quantity": {"field": "pop", "unit": "人", "meaning": "count",
                 "aggregation": "sum"},
    "area_scale": {"max_radius": 36.0, "extent": 1000.0},
    "title": "分区人口",
}


# ── 夹具：真实会话 + 真实 ref 源 + 真实 MapSpec 层 ──────────────────────

def _features(n: int = 12):
    feats = []
    for i in range(n):
        lon = 104.0 + (i % 4) * 0.25
        lat = 30.4 + (i // 4) * 0.25
        feats.append({
            "type": "Feature",
            "id": f"u{i}",
            "geometry": {"type": "Polygon", "coordinates": [[
                [lon, lat], [lon + 0.25, lat],
                [lon + 0.25, lat + 0.25], [lon, lat + 0.25], [lon, lat],
            ]]},
            "properties": {"name": f"U{i}", "pop": 100 * (i + 1)},
        })
    return {"type": "FeatureCollection", "features": feats}


async def _seed(tmp_path, monkeypatch, *, pop=None, source_alias=False):
    import app.services.mapspec.store as store_module

    monkeypatch.setattr(store_module, "BASE_STORAGE_DIR",
                        tmp_path / "mapspec_store")
    (tmp_path / "mapspec_store").mkdir(exist_ok=True)

    session_id = "g47svc-" + uuid.uuid4().hex[:10]
    from app.services.session_data import session_data_manager as sdm

    payload = _features()
    if pop is not None:
        for i, feat in enumerate(payload["features"]):
            feat["properties"]["pop"] = pop[i]
    ref = await sdm.store(session_id, {"kind": "geojson", "geojson": payload},
                          prefix="result")
    source_ref = str(ref)
    if source_alias:
        # 层源只记别名，规范化 ref 只在冻结/布局阶段出现 —— 复现
        # "apply 拿未解析的别名与布局锚比对" 的输入面。
        source_ref = "ref:g47-alias-" + uuid.uuid4().hex[:8]
        await sdm.set_alias(session_id, str(ref), source_ref)

    from app.services.gis_world_state.mutation import apply_gis_mutation
    from app.services.mapspec.intents import UpsertLayerIntent

    result = await apply_gis_mutation(
        session_id,
        UpsertLayerIntent(layer={
            "id": LAYER_ID, "type": "fill", "source": f"{LAYER_ID}-source",
            "paint": {"color": "#c2410c"},
            "data_binding": {"field": "pop", "field_type": "number"},
        }, source_data={"ref_id": source_ref}),
        origin="agent", actor="g47:test-seed")
    assert not result.is_error, result.error_msg
    return session_id, str(ref)


async def _run_to_layout(session_id: str, *, plan_overrides=None):
    """inspect → plan → run → 等作业落定 → 返回 (layout_ref, summary)。"""
    ins = await inspect_layer(session_id, LAYER_ID, trusted_dispatch=True)
    anchor = {k: ins["source_anchor"][k] for k in
              ("ref_id", "content_revision", "content_fingerprint")}
    body = {**PLAN, "layer_id": LAYER_ID, "source_anchor": anchor}
    if plan_overrides:
        body.update(plan_overrides)
    plan = parse_plan(body)

    await plan_layout(session_id, LAYER_ID, plan, trusted_dispatch=True)
    started = await start_layout(session_id, LAYER_ID, plan, trusted_dispatch=True)
    job_id = int(started["job_id"])

    deadline = asyncio.get_running_loop().time() + 60
    while asyncio.get_running_loop().time() < deadline:
        status = await layout_status(session_id, job_id)
        if status["status"] in ("completed", "failed", "cancelled", "stale"):
            break
        await asyncio.sleep(0.05)
    else:  # pragma: no cover - 只在真卡死时触发
        pytest.fail(f"cartogram job {job_id} did not settle")

    assert status["status"] == "completed", status
    summary = status["result_summary"] or {}
    assert summary.get("layout_ref"), status
    return summary["layout_ref"], summary, started


async def _layer(session_id: str):
    from app.services.mapspec_store import mapspec_store

    spec = await mapspec_store.get_mapspec(session_id) or {}
    for layer in spec.get("layers") or []:
        if layer.get("id") == LAYER_ID:
            return layer, spec
    return None, spec


# ── 全链 ────────────────────────────────────────────────────────────────

async def test_full_lifecycle_from_frozen_source_to_publication(tmp_path, monkeypatch):
    session_id, base_ref = await _seed(tmp_path, monkeypatch)

    ins = await inspect_layer(session_id, LAYER_ID, trusted_dispatch=True)
    assert ins["source"]["base_ref"] == base_ref
    assert ins["source"]["content_revision"] >= 1
    assert any(f.get("name") == "pop" for f in ins["fields"])
    assert ins["supported"]["dorling_circle"] is True
    # 诚实降级：泛 cartogram 仍未实现
    assert ins["supported"]["generic_cartogram"] is False

    layout_ref, summary, started = await _run_to_layout(session_id)
    assert layout_ref.startswith("ref:")
    assert summary["publication_ready"] is True
    assert summary["circle_count"] == 12
    assert summary["area"]["max_rel_error"] < 0.01
    assert started["note"].startswith("布局是示意坐标")

    layout = await load_layout(session_id, layout_ref)
    assert layout["coordinate_space"] == "diagram"
    assert layout["is_geographic"] is False
    assert len(layout["circles"]) == 12
    assert layout["source"]["ref_id"] == base_ref

    layer_before, _ = await _layer(session_id)
    revision_before = ins["revision"]
    applied = await apply_layout(
        session_id, LAYER_ID, layout_ref=layout_ref,
        expected_revision=revision_before)
    assert applied["committed"] is True
    assert applied["publication_ready"] is True
    assert applied["coordinate_space"] == "diagram"

    layer_after, spec = await _layer(session_id)
    block = layer_after["cartogram_authoring"]
    assert block["mode"] == "dorling_circle"
    assert block["layout_ref"] == layout_ref
    assert block["base_source_ref"] == base_ref
    assert block["base_content_revision"] == layout["source"]["content_revision"]
    assert block["coordinate_space"] == "diagram"
    assert block["diagnostic"] is False
    assert block["decided_by"] == "user"
    assert block["previous"] is None
    # 原样式/几何/字段未被改写
    assert layer_after["paint"] == layer_before["paint"]
    assert layer_after["data_binding"] == layer_before["data_binding"]
    # 源数据没被写回布局属性
    from app.services.session_data import session_data_manager as sdm
    payload = await sdm.get(session_id, base_ref)
    assert all("x" not in f["properties"] for f in payload["geojson"]["features"])

    for fmt, head in (("svg", b"<svg"), ("png", b"\x89PNG"), ("pdf", b"%PDF")):
        data, media, filename = await export_layout(session_id, layout_ref, fmt)
        assert data.startswith(head), (fmt, data[:12])
        assert filename.endswith("." + fmt)
        assert media.split("/")[-1] in (fmt, "svg+xml")


async def test_alias_source_ref_does_not_look_stale_on_apply(tmp_path, monkeypatch):
    """层源是别名：冻结/布局记的是 resolve_alias 后的 ref，apply 必须先解析再比对。

    未解析时 ``source.ref_id``（规范 ref）与层源（别名）恒不等 → 该源上的
    布局永远 STALE_SOURCE、无法应用。
    """
    session_id, base_ref = await _seed(tmp_path, monkeypatch, source_alias=True)
    layout_ref, summary, _ = await _run_to_layout(session_id)
    assert summary["publication_ready"] is True

    ins = await inspect_layer(session_id, LAYER_ID, trusted_dispatch=True)
    assert ins["source"]["base_ref"] == base_ref   # 布局锚 = 规范 ref

    applied = await apply_layout(session_id, LAYER_ID, layout_ref=layout_ref,
                                 expected_revision=ins["revision"])
    assert applied["committed"] is True
    assert applied["stale_source"] is False
    layer, _ = await _layer(session_id)
    assert layer["cartogram_authoring"]["base_source_ref"] == base_ref


async def test_second_apply_records_previous_for_undo(tmp_path, monkeypatch):
    session_id, _ = await _seed(tmp_path, monkeypatch)
    first_ref, _, started = await _run_to_layout(session_id)
    ins = await inspect_layer(session_id, LAYER_ID, trusted_dispatch=True)
    await apply_layout(session_id, LAYER_ID, layout_ref=first_ref,
                       expected_revision=ins["revision"])

    second_ref, _, _ = await _run_to_layout(
        session_id, plan_overrides={"title": "第二版"})
    ins2 = await inspect_layer(session_id, LAYER_ID, trusted_dispatch=True)
    assert ins2["revision"] > ins["revision"]
    await apply_layout(session_id, LAYER_ID, layout_ref=second_ref,
                       expected_revision=ins2["revision"])

    layer, _ = await _layer(session_id)
    block = layer["cartogram_authoring"]
    assert block["layout_ref"] == second_ref
    assert block["previous"]["layout_ref"] == first_ref


# ── CAS：修订不符时什么都不写 ───────────────────────────────────────────

async def test_cas_mismatch_writes_nothing(tmp_path, monkeypatch):
    session_id, _ = await _seed(tmp_path, monkeypatch)
    layout_ref, _, _ = await _run_to_layout(session_id)
    ins = await inspect_layer(session_id, LAYER_ID, trusted_dispatch=True)
    stale_revision = ins["revision"] - 1

    with pytest.raises(AuthoringError) as ei:
        await apply_layout(session_id, LAYER_ID, layout_ref=layout_ref,
                           expected_revision=stale_revision)
    assert ei.value.code == "CAS_SUPERSEDED"
    assert "nothing was written" in ei.value.message

    layer, spec = await _layer(session_id)
    assert "cartogram_authoring" not in layer
    assert (await inspect_layer(session_id, LAYER_ID,
                                trusted_dispatch=True))["revision"] == ins["revision"]


async def test_apply_requires_publication_ready_or_explicit_diagnostic(tmp_path, monkeypatch):
    session_id, _ = await _seed(tmp_path, monkeypatch)
    layout_ref, summary, _ = await _run_to_layout(
        session_id, plan_overrides={"solver": {"max_iterations": 1}})
    assert summary["publication_ready"] is False

    ins = await inspect_layer(session_id, LAYER_ID, trusted_dispatch=True)
    with pytest.raises(AuthoringError) as ei:
        await apply_layout(session_id, LAYER_ID, layout_ref=layout_ref,
                           expected_revision=ins["revision"])
    assert ei.value.code == "QUALITY_GATE_FAILED"
    layer, _ = await _layer(session_id)
    assert "cartogram_authoring" not in layer

    applied = await apply_layout(
        session_id, LAYER_ID, layout_ref=layout_ref,
        expected_revision=ins["revision"], allow_diagnostic=True)
    assert applied["diagnostic"] is True
    assert applied["publication_ready"] is False
    layer, _ = await _layer(session_id)
    assert layer["cartogram_authoring"]["diagnostic"] is True
    assert layer["cartogram_authoring"]["publication_ready"] is False


# ── 源代次漂移 ──────────────────────────────────────────────────────────

async def test_source_rewrite_makes_old_layout_stale(tmp_path, monkeypatch):
    session_id, base_ref = await _seed(tmp_path, monkeypatch)
    layout_ref, _, _ = await _run_to_layout(session_id)

    from app.services.session_data import session_data_manager as sdm
    rewritten = _features()
    for i, feat in enumerate(rewritten["features"]):
        feat["properties"]["pop"] = 5000 + i
    ok = await sdm.overwrite(session_id, base_ref, {"kind": "geojson",
                                                   "geojson": rewritten})
    assert ok is True

    ins = await inspect_layer(session_id, LAYER_ID, trusted_dispatch=True)
    with pytest.raises(AuthoringError) as ei:
        await apply_layout(session_id, LAYER_ID, layout_ref=layout_ref,
                           expected_revision=ins["revision"])
    assert ei.value.code == "STALE_SOURCE"

    applied = await apply_layout(
        session_id, LAYER_ID, layout_ref=layout_ref,
        expected_revision=ins["revision"], acknowledge_stale_source=True)
    assert applied["stale_source"] is True
    layer, _ = await _layer(session_id)
    block = layer["cartogram_authoring"]
    assert block["stale_source"] is True
    # 披露这是旧代次的布局，不冒充"当前数据的结果"
    assert block["base_content_revision"] != (
        await inspect_layer(session_id, LAYER_ID,
                            trusted_dispatch=True))["source_anchor"]["content_revision"]


# ── 归属与列举 ──────────────────────────────────────────────────────────

async def test_layout_ref_is_session_scoped(tmp_path, monkeypatch):
    session_id, _ = await _seed(tmp_path, monkeypatch)
    layout_ref, _, _ = await _run_to_layout(session_id)

    other = "g47svc-other-" + uuid.uuid4().hex[:6]
    with pytest.raises(AuthoringError) as ei:
        await load_layout(other, layout_ref)
    assert ei.value.code == "LAYOUT_NOT_FOUND"

    receipt = await get_layout(session_id, layout_ref)
    assert receipt["layout_ref"] == layout_ref
    assert receipt["is_geographic"] is False
    assert len(receipt["circles_sample"]) <= 8
    assert "circles" not in receipt        # 有界回执不含全量圆

    with pytest.raises(AuthoringError):
        await load_layout(session_id, "ref:not-a-layout")
    with pytest.raises(AuthoringError):
        await load_layout(session_id, "g47layout-abc")  # 裸 id 不是 ref


async def test_job_status_is_session_scoped(tmp_path, monkeypatch):
    session_id, _ = await _seed(tmp_path, monkeypatch)
    _, _, started = await _run_to_layout(session_id)
    other = "g47svc-other-" + uuid.uuid4().hex[:6]
    with pytest.raises(AuthoringError) as ei:
        await layout_status(other, int(started["job_id"]))
    assert ei.value.code == "JOB_NOT_FOUND"


async def test_list_layouts_reads_authoritative_job_rows(tmp_path, monkeypatch):
    session_id, _ = await _seed(tmp_path, monkeypatch)
    layout_ref, _, _ = await _run_to_layout(session_id)
    listed = await list_layouts(session_id, limit=10)
    assert listed["session_id"] == session_id
    assert any(item["layout_ref"] == layout_ref for item in listed["items"])
    assert all(item["status"] == "completed" for item in listed["items"])


# ── 幂等 ────────────────────────────────────────────────────────────────

async def test_same_plan_and_revision_reuses_the_job(tmp_path, monkeypatch):
    """幂等键 = 计划 + 源代次 + 算法版本：重复提交不产生第二个布局。"""
    session_id, _ = await _seed(tmp_path, monkeypatch)
    ins = await inspect_layer(session_id, LAYER_ID, trusted_dispatch=True)
    anchor = {k: ins["source_anchor"][k] for k in
              ("ref_id", "content_revision", "content_fingerprint")}
    plan = parse_plan({**PLAN, "layer_id": LAYER_ID, "source_anchor": anchor})

    first = await start_layout(session_id, LAYER_ID, plan, trusted_dispatch=True)
    second = await start_layout(session_id, LAYER_ID, plan, trusted_dispatch=True)
    assert first["job_id"] == second["job_id"]
    assert second["idempotent_reuse"] is True
    assert first["candidate_digest"] == second["candidate_digest"]
