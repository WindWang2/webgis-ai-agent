"""验收旗舰 —— 三图系列全链路（任务书 §6 A2/A5 值级证据）。

真实链路（无 mock 引擎/渲染）：
  设计规范（WP1）→ 三个真实成员会话（真实 lifecycle engine）→ 系列计划
  （WP3）→ 一致性检查 → 规范传播（逐图 CAS receipts）→ 冻结计划 →
  系列生产（WP5 durable 链）→ manifest + 真实 SVG 产物（checksum 对账）。

合成数据显式 simulated；断言全部机器可复核。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

pytest.importorskip("weasyprint")


@pytest.fixture()
def celery_eager(monkeypatch):
    from app.services.task_queue import celery_app

    monkeypatch.setitem(celery_app.conf, "result_backend", None)
    monkeypatch.setitem(celery_app.conf, "broker_url", "memory://")
    monkeypatch.setitem(celery_app.conf, "task_always_eager", True)


@pytest.fixture()
def job_db(tmp_path, monkeypatch):
    import contextlib

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models.db_model import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'series_flagship.db'}")
    Base.metadata.create_all(engine)
    sess = sessionmaker(bind=engine)

    @contextlib.contextmanager
    def fake_db_session():
        s = sess()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    monkeypatch.setattr("app.tools._utils.db_session", fake_db_session)
    monkeypatch.setattr("app.services.jobs.submit.db_session", fake_db_session)
    monkeypatch.setattr(
        "app.services.jobs.worker._default_session_factory", fake_db_session)
    yield sess
    engine.dispose()


def _fc(prefix: str, counts: list, field: str = "schools") -> dict:
    features = []
    for i, c in enumerate(counts):
        base = 116.0 + i * 0.01
        features.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[
                [base, 39.0], [base + 0.01, 39.0],
                [base + 0.01, 39.01], [base, 39.01], [base, 39.0],
            ]]},
            "properties": {"name": f"{prefix}-{i}", field: float(c),
                           "_simulated": True},
        })
    return {"type": "FeatureCollection", "features": features}


async def _seed_member(session_id: str, field: str, counts: list, color: str) -> str:
    """成员会话：区级面 + 专题分级（字段名各不相同 —— 角色绑定场景）。"""
    from app.lib.cartography.thematic_spec import build_graduated_spec, spec_to_paint
    from app.services.gis_world_state.mutation import apply_gis_mutation
    from app.services.mapspec.intents import (
        InitProjectIntent,
        SetLayoutIntent,
        UpsertLayerIntent,
    )
    from app.services.session_data import session_data_manager

    result = await apply_gis_mutation(
        session_id, InitProjectIntent(view={"zoom": 10}), origin="agent",
        actor="test:flagship",
    )
    assert not result.is_error, result.error_msg
    fc = _fc(session_id[-6:], counts, field=field)
    ref_id = await session_data_manager.store(
        session_id, {"kind": "geojson", "geojson": fc}, prefix="result",
    )
    legend = build_graduated_spec(fc, field=field)
    assert legend is not None
    legend["unit"] = "所"
    paint_color, _warn = spec_to_paint(legend)
    layer = {
        "id": "thematic", "type": "fill", "source": "thematic-source",
        "paint": {"color": paint_color},
        "legend_spec": legend,
        "data_binding": {"field": field, "field_type": "number"},
    }
    up = await apply_gis_mutation(
        session_id, UpsertLayerIntent(layer=layer, source_data={"ref_id": str(ref_id)}),
        origin="agent", actor="test:flagship",
    )
    assert not up.is_error, up.error_msg
    # 统一纸面版式（系列生产走 print_layout 链）
    from app.lib.cartography.print_layout_schema import empty_print_layout

    plan = empty_print_layout()
    await apply_gis_mutation(
        session_id, SetLayoutIntent(print_layout=plan),
        origin="agent", actor="test:flagship",
    )
    return str(ref_id)


@pytest.mark.asyncio
async def test_flagship_three_map_series_full_journey(
    store_root, exports_dir, celery_eager, job_db,
):
    from app.core.database import Base, Engine, SessionLocal

    from app.models.design_spec import CartographyDesignSpec, CartographyDesignSpecVersion
    from app.models.map_series import CartographyMapSeries, CartographyMapSeriesVersion
    from app.services.design_specs.versioning import create_spec
    from app.services.map_series.consistency import run_series_consistency
    from app.services.map_series.freeze import build_freeze_plan
    from app.services.map_series.propagation import (
        apply_propagation,
        build_propagation_preview,
    )
    from app.services.map_series.store import create_series
    from app.services.mapspec_store import mapspec_store
    from app.services.series_production.fingerprint import document_fingerprint
    from app.services.series_production.service import submit_series_production
    from app.services.series_production.submit import (
        execute_series_production_job,
        freeze_plan_from_series_payload,
    )

    Base.metadata.create_all(bind=Engine)
    try:
        # ── 1) 设计规范（palette 统一为 YlOrRd；成员初始 Blues → 传播场景）
        with SessionLocal() as db:
            spec = create_spec(
                db,
                {
                    "spec_version": "design_spec.v1",
                    "name": "旗舰·三城小学密度规范",
                    "color": {"theme_id": "cartographic.light_interactive",
                              "palette_sequential": "YlOrRd"},
                    "classification": {"default_method": "quantiles",
                                       "k_min": 3, "k_max": 3,
                                       "shared_required": True},
                    "components": {"required": [{"component_type": "legend"}]},
                    "output": {"targets": ["interactive", "svg"]},
                    "data_roles": [{
                        "name": "indicator", "value_type": "number",
                        "measurement": "count", "unit": "所",
                        "unit_required": True, "comparable_across_maps": True,
                    }],
                },
                creator_id="flagship-owner",
            )
            # ── 2) 三个真实成员会话（不同字段名：primaries/schools_num/sch_total）
            sessions = {}
            fields = {"city-a": "primaries", "city-b": "schools_num",
                      "city-c": "sch_total"}
            for sid, field in fields.items():
                await _seed_member(sid, field, [12, 9, 15, 6], "#1d4ed8")
            payload = {
                "series_version": "map_series.v1",
                "name": "旗舰三城系列",
                "design_spec_id": spec.id,
                "members": [
                    {"member_id": "a", "title": "城A小学分布",
                     "session_id": "city-a", "role": "primary"},
                    {"member_id": "b", "title": "城B小学分布",
                     "session_id": "city-b", "role": "comparison"},
                    {"member_id": "c", "title": "城C小学分布",
                     "session_id": "city-c", "role": "comparison"},
                ],
                "comparability": {"shared_classification": True,
                                  "shared_palette": True},
                "dimension": {"axis": "region",
                              "labels": ["城A", "城B", "城C"]},
            }
            series = create_series(db, payload, creator_id="flagship-owner")
            series_id = series.id

        # ── 3) 冻结前一致性检查（真实跨图检查）
        with SessionLocal() as db:
            report = await run_series_consistency(db, series_id)
        codes = {f["code"] for f in report["findings"]}
        assert report["ok"] or codes, report
        # 三成员同为 quantiles（种子一致性）→ 不应有分类冲突
        assert "CLASSIFICATION_SCOPE_CONFLICT" not in codes, codes

        # ── 4) 规范传播 preview + apply（YlOrRd ramp 落成员 paint）
        from app.lib.cartography.palettes import resolve_palette_colors

        ylorrd = resolve_palette_colors("YlOrRd")[:6]
        intents = {"palette": {"target_layer_id": "thematic", "ramp": ylorrd}}
        with SessionLocal() as db:
            preview = await build_propagation_preview(db, series_id, intents=intents)
            assert preview.get("members"), preview
            result = await apply_propagation(
                db, series_id, intents=intents, actor="flagship-owner")
        assert result["summary"]["committed"] >= 2, result
        for receipt in result["receipts"]:
            assert receipt["status"] in ("committed", "skipped_locked",
                                         "superseded", "failed"), receipt

        # ── 5) 冻结（传播后的新指纹）
        with SessionLocal() as db:
            freeze_payload = await build_freeze_plan(db, series_id)
        assert not freeze_payload["unresolvable_member_ids"]
        plan = freeze_plan_from_series_payload(freeze_payload)
        assert len(plan.members) == 3
        # 冻结指纹与当前会话一致
        for member in plan.members:
            doc = await mapspec_store.get_mapspec(member.session_id)
            current = document_fingerprint(doc)
            assert member.mapspec_fingerprint[:32] == current[:32]

        # ── 6) 系列生产（durable 链；eager 同步首跑）
        submitted = submit_series_production(plan, freeze_payload=freeze_payload)
        job_id = submitted["job_id"]
        outcome = await execute_series_production_job(job_id)
        produced = (outcome["summary"]["complete"]
                    + outcome["summary"]["cached_reused"])
        assert produced == 3, outcome["summary"]
        assert outcome["summary"]["failed"] == 0

        # ── 7) manifest + 真实产物对账（值级）
        manifest_path = Path(exports_dir) / outcome["manifest_file"]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["summary"]["all_complete"] is True
        # SIMULATED_DATA 披露机制由单测覆盖（WP3 freeze 载荷不携带该声明时
        # 如实缺席 —— 见 TestManifestDeterminism）；此处锁状态一致性
        assert not any(d["code"] == "MEMBERS_FAILED"
                       for d in manifest["disclosures"])
        for member in manifest["members"]:
            assert member["status"] in ("complete", "cached_reused")
            assert member["qa"]["ok"] is True, member
            for artifact in member["artifacts"]:
                fpath = Path(exports_dir) / artifact["filename"]
                assert fpath.is_file()
                digest = hashlib.sha256(fpath.read_bytes()).hexdigest()
                assert digest == artifact["checksum"]

        # ── 8) 传播真实性：ramp 色值应真实落到至少一个成员的 legend/paint
        changed = False
        seen_legends = []
        for sid in fields:
            doc = await mapspec_store.get_mapspec(sid)
            doc_text = json.dumps(doc.get("layers", []))
            for color in ylorrd:
                if color.lower() in doc_text.lower():
                    changed = True
            for layer in doc.get("layers", []):
                legend = layer.get("legend_spec")
                if isinstance(legend, dict):
                    seen_legends.append(legend.get("colors"))
        assert changed, (
            f"YlOrRd ramp 应真实落到至少一个成员：ylorrd={ylorrd[:3]}… "
            f"legends={seen_legends}"
        )
    finally:
        with SessionLocal() as db:
            db.query(CartographyMapSeriesVersion).delete()
            db.query(CartographyMapSeries).delete()
            db.query(CartographyDesignSpecVersion).delete()
            db.query(CartographyDesignSpec).delete()
            db.commit()
