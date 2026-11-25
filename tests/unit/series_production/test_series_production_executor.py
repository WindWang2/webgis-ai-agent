"""WP5 测试 —— 冻结计划契约 / 指纹一致性 / 真实渲染执行器 / 增量缓存 / manifest。

诚实声明（同 atlas fixtures 纪律）：合成地理数据（simulated），渲染链全部
真实执行（mapspec_to_svg + WeasyPrint；importorskip 门控），无 mock 成功。
"""
from __future__ import annotations

import uuid

import pytest

from app.services.series_production.contract import (
    SeriesFreezePlan,
    output_formats,
    validate_freeze_plan_payload,
)

pytestmark = pytest.mark.unit

weasyprint = pytest.importorskip("weasyprint")


# ── 契约 ─────────────────────────────────────────────────────────────


def _member(member_id="m1", fp="a" * 8, session_id="sess-a") -> dict:
    return {
        "member_id": member_id,
        "title": "测试成员",
        "session_id": session_id,
        "mapspec_fingerprint": fp,
    }


def _plan(members=None, **kw) -> dict:
    base = {
        "spec_version": "series_freeze_plan.v1",
        "series_id": "series-1",
        "name": "测试系列",
        "output": {"series_formats": ["svg"], "dpi": 150},
        "members": members if members is not None else [_member()],
        "simulated_data": True,
    }
    base.update(kw)
    return base


class TestFreezePlanContract:
    def test_valid_plan_and_digest_deterministic(self):
        plan = validate_freeze_plan_payload(_plan())
        assert plan.plan_digest().startswith("splan-sha256:")
        plan2 = validate_freeze_plan_payload(_plan())
        assert plan.plan_digest() == plan2.plan_digest()

    def test_empty_members_rejected(self):
        with pytest.raises(ValueError):
            validate_freeze_plan_payload(_plan(members=[]))

    def test_duplicate_member_rejected(self):
        with pytest.raises(ValueError):
            validate_freeze_plan_payload(
                _plan(members=[_member("m1"), _member("m1", fp="b" * 8)]))

    def test_extra_field_rejected(self):
        bad = _plan()
        bad["no_such"] = 1
        with pytest.raises(Exception):
            validate_freeze_plan_payload(bad)

    def test_output_formats_normalization(self):
        assert output_formats({"series_formats": ["svg"]}) == ["svg"]
        assert output_formats({}) == ["pdf", "svg"]
        assert output_formats({"series_formats": "pdf"}) == ["pdf"]
        # 非法格式回落缺省（如实，不猜）
        assert output_formats({"series_formats": ["mp4"]}) == ["pdf", "svg"]


# ── 真实执行器 ───────────────────────────────────────────────────────


@pytest.fixture()
def store_root(tmp_path, monkeypatch):
    import app.services.mapspec.store as store_module

    root = tmp_path / "mapspec_store"
    root.mkdir()
    monkeypatch.setattr(store_module, "BASE_STORAGE_DIR", root, raising=False)
    yield root


@pytest.fixture()
def exports_dir(tmp_path, monkeypatch):
    from app.services import export_paths

    root = tmp_path / "exports"
    root.mkdir()
    monkeypatch.setattr(export_paths, "exports_root", lambda: root)
    return root


@pytest.fixture(autouse=True)
def _owner_kind(monkeypatch):
    monkeypatch.setattr(
        "app.services.session_data_protocol._conversation_owner_kind",
        lambda sid: "none",
    )


def _make_fc(prefix="zone", n=6) -> dict:
    features = []
    for i in range(n):
        base = 116.0 + i * 0.01
        features.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[
                [base, 39.0], [base + 0.01, 39.0],
                [base + 0.01, 39.01], [base, 39.01], [base, 39.0],
            ]]},
            "properties": {"name": f"{prefix}-{i}", "value": float(i * 10 + 5)},
        })
    return {"type": "FeatureCollection", "features": features}


async def _seed_member_session(*, with_print_layout: bool) -> tuple:
    """生产式建会话：内联 GeoJSON 面层 + 可选最小 print_layout 计划。"""
    import asyncio

    from app.services.gis_world_state.mutation import apply_gis_mutation
    from app.services.mapspec.intents import (
        InitProjectIntent,
        SetLayoutIntent,
        UpsertLayerIntent,
    )
    from app.services.mapspec_store import mapspec_store
    from app.services.series_production.fingerprint import document_fingerprint

    session_id = "sess-sp-" + uuid.uuid4().hex[:10]
    result = await apply_gis_mutation(
        session_id, InitProjectIntent(view={"zoom": 10}), origin="agent", actor="test:sp",
    )
    assert not result.is_error, result.error_msg
    fc = _make_fc()
    layer = {
        "id": "zones", "type": "fill", "source": "zones-source",
        "paint": {"fill-color": "#1d4ed8", "fill-opacity": 0.5},
    }
    up = await apply_gis_mutation(
        session_id, UpsertLayerIntent(layer=layer, source_data={"inline": fc}),
        origin="agent", actor="test:sp",
    )
    assert not up.is_error, up.error_msg
    if with_print_layout:
        from app.lib.cartography.print_layout_schema import empty_print_layout

        plan = await asyncio.to_thread(empty_print_layout)
        await apply_gis_mutation(
            session_id, SetLayoutIntent(print_layout=plan),
            origin="agent", actor="test:sp",
        )
    document = await mapspec_store.get_mapspec(session_id)
    return session_id, document_fingerprint(document), document


def _freeze_plan_for(members: list, *, formats=("svg",), dpi=150) -> SeriesFreezePlan:
    from app.services.series_production.contract import validate_freeze_plan_payload

    return validate_freeze_plan_payload({
        "spec_version": "series_freeze_plan.v1",
        "series_id": "series-" + uuid.uuid4().hex[:8],
        "name": "成都小学密度系列（合成）",
        "output": {"series_formats": list(formats), "dpi": dpi},
        "members": members,
        "simulated_data": True,
    })


class TestFingerprintConsistency:
    async def test_document_fingerprint_matches_store_sidecar(self, store_root):
        # 冻结对账的指纹必须与 store 的 .fp sidecar 同源

        session_id, fp, _ = await _seed_member_session(with_print_layout=False)
        sidecar = store_root / session_id / "mapspec.json.fp"
        assert sidecar.is_file(), "store 必须落 .fp sidecar"
        assert sidecar.read_text(encoding="utf-8").strip() == fp


class TestSeriesExecution:
    async def test_two_members_real_svg_production(self, store_root, exports_dir):
        sid1, fp1, _ = await _seed_member_session(with_print_layout=True)
        sid2, fp2, _ = await _seed_member_session(with_print_layout=True)
        plan = _freeze_plan_for([
            {"member_id": "m1", "title": "甲区", "session_id": sid1,
             "mapspec_fingerprint": fp1},
            {"member_id": "m2", "title": "乙区", "session_id": sid2,
             "mapspec_fingerprint": fp2},
        ], formats=("svg",), dpi=96)
        outcome = await execute(plan)
        assert outcome["summary"]["failed"] == 0, outcome
        assert outcome["summary"]["complete"] == 2
        manifest = outcome["manifest"]
        assert manifest["summary"]["all_complete"] is True
        assert manifest["inputs"]["freeze_plan_digest"].startswith("splan-sha256:")
        for member in manifest["members"]:
            assert member["status"] == "complete"
            svg_items = [a for a in member["artifacts"] if a["fmt"] == "svg"]
            assert svg_items, "print_layout 成员必须产出真实 SVG"
            from app.services.export_paths import exports_root
            import hashlib
            from pathlib import Path

            fpath = Path(exports_root()) / svg_items[0]["filename"]
            assert fpath.is_file()
            digest = hashlib.sha256(fpath.read_bytes()).hexdigest()
            assert digest == svg_items[0]["checksum"]

    async def test_increment_cache_reuse_and_stale_block(self, store_root, exports_dir):
        from app.services.gis_world_state.mutation import apply_gis_mutation

        sid1, fp1, _ = await _seed_member_session(with_print_layout=True)
        sid2, fp2, _ = await _seed_member_session(with_print_layout=True)
        members = [
            {"member_id": "m1", "title": "甲区", "session_id": sid1,
             "mapspec_fingerprint": fp1},
            {"member_id": "m2", "title": "乙区", "session_id": sid2,
             "mapspec_fingerprint": fp2},
        ]
        plan = _freeze_plan_for(members, formats=("svg",), dpi=96)
        first = await execute(plan)
        assert first["summary"]["complete"] == 2

        # 第二次：全部命中缓存（同输入）
        second = await execute(plan)
        assert second["summary"]["cached_reused"] == 2
        assert second["summary"]["complete"] == 0

        # m2 的会话被改动（真实 mutation：同层换色）→ 指纹漂移 →
        # 该成员 stale blocked，m1 仍复用缓存（增量语义：只有受影响成员失效）
        from app.services.mapspec.intents import UpsertLayerIntent

        mut = await apply_gis_mutation(
            sid2,
            UpsertLayerIntent(layer={
                "id": "zones", "type": "fill", "source": "zones-source",
                "paint": {"fill-color": "#dc2626", "fill-opacity": 0.5},
            }),
            origin="agent", actor="test:sp",
        )
        assert not mut.is_error, mut.error_msg
        third = await execute(plan)  # 仍用旧冻结计划
        by_id = {r.member_id: r for r in third["results"]}
        assert by_id["m1"].status == "cached_reused"
        assert by_id["m2"].status == "blocked"
        assert by_id["m2"].code == "STALE_MEMBER"
        assert third["manifest"]["summary"]["all_complete"] is False
        assert any(d["code"] == "MEMBERS_BLOCKED" for d in third["manifest"]["disclosures"])

    async def test_missing_session_is_blocked_not_failed_silently(self, store_root, exports_dir):
        plan = _freeze_plan_for([
            {"member_id": "ghost", "title": "幽灵成员",
             "session_id": "sess-no-such", "mapspec_fingerprint": "c" * 8},
        ], formats=("svg",))
        outcome = await execute(plan)
        result = outcome["results"][0]
        assert result.status == "blocked"
        assert result.code == "SESSION_UNAVAILABLE"
        assert outcome["manifest"]["summary"]["all_complete"] is False

    async def test_partial_failure_reports_per_member(self, store_root, exports_dir):
        # svg 格式 + 无 print_layout 成员 → 该成员失败（svg 需要纸面链），
        # 另一 print_layout 成员完整 —— 分批语义如实
        sid1, fp1, _ = await _seed_member_session(with_print_layout=True)
        sid2, fp2, _ = await _seed_member_session(with_print_layout=False)
        plan = _freeze_plan_for([
            {"member_id": "paper", "title": "有版式", "session_id": sid1,
             "mapspec_fingerprint": fp1},
            {"member_id": "nopl", "title": "无版式", "session_id": sid2,
             "mapspec_fingerprint": fp2},
        ], formats=("svg",), dpi=96)
        outcome = await execute(plan)
        by_id = {r.member_id: r for r in outcome["results"]}
        assert by_id["paper"].status == "complete"
        assert by_id["nopl"].status == "failed"
        assert by_id["nopl"].message
        assert outcome["manifest"]["summary"]["complete"] == 1
        assert outcome["manifest"]["summary"]["failed"] == 1


async def execute(plan):
    from app.services.series_production.executor import execute_series_production

    return await execute_series_production(plan)


class TestManifestDeterminism:
    async def test_same_inputs_same_manifest_digest(self, store_root, exports_dir):
        from app.services.series_production.manifest import build_series_manifest

        sid1, fp1, _ = await _seed_member_session(with_print_layout=True)
        members = [{"member_id": "m1", "title": "甲区", "session_id": sid1,
                    "mapspec_fingerprint": fp1}]
        plan = _freeze_plan_for(members, formats=("svg",), dpi=96)
        r1 = await execute(plan)
        # manifest 是确定性函数：同结果集两次构建 ⇒ 同 digest
        m1 = build_series_manifest(plan, r1["results"])
        m2 = build_series_manifest(plan, r1["results"])
        assert m1["manifest_digest"] == m2["manifest_digest"]
        # 两次缓存命中运行的状态集一致 ⇒ digest 一致（complete≠cached_reused
        # 是诚实差异，manifest 不同是正确行为）
        r2 = await execute(plan)
        r3 = await execute(plan)
        assert r2["manifest"]["manifest_digest"] == r3["manifest"]["manifest_digest"]
        assert {mm["status"] for mm in r2["manifest"]["members"]} == \
               {mm["status"] for mm in r3["manifest"]["members"]}

    def test_simulated_disclosure_always_present(self):
        from app.services.series_production.manifest import build_series_manifest

        plan = _freeze_plan_for([_member()])
        results = []
        manifest = build_series_manifest(plan, results)
        assert any(d["code"] == "SIMULATED_DATA" for d in manifest["disclosures"])
        assert manifest["summary"]["all_complete"] is False  # 空 results 不算完成
