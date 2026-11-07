"""SW-02 选择范围正确性（#2004）：框选 >8 要素不得静默截断。

Oracle（任务书 G05 SW-02）：
- 选 20 个要素 → Agent 必须看到准确 20 个（或明确拒绝/披露受限）；
- Selection 数量与 omitted 可披露 —— 禁止 UI 报 20、Agent 只见 8；
- 披露字段（matched_count / ids_truncated）由服务端权威派生，
  客户端申报不可信（截断判定以服务端实际保留数为准）。

链路断点（2026-11-08 基线实测）：
- backend contracts.py normalize_payload 对任意 list `value[:8]` 截断且无披露；
- payload_keys 白名单不含 matched_count —— 前端披露字段上报即被丢弃；
- >512B 载荷整事件拒绝 —— 只放行 ids 不放预算会把"截断"变"整事件消失"；
- frontend bus.ts emitFeatureSelected `.slice(0, 8)` 在发送端先行截断
 （前端侧失败测试见 frontend/lib/interaction/emitters.test.ts 同批）。
"""
from __future__ import annotations

from app.services.gis_harness.interaction_ir.contracts import (
    normalize_payload,
)
from app.services.gis_harness.interaction_ir.ingest import (
    VERDICT_ACCEPTED,
    LedgerState,
    apply_event,
)
from app.services.gis_harness.interaction_ir.projection import (
    project_interaction_snapshot,
)
from app.services.gis_situation.analysis_scope import derive_claimed_scope


def _ids(n: int, prefix: str = "school") -> list[str]:
    return [f"{prefix}-{i:04d}" for i in range(1, n + 1)]


def _selection_payload(n_ids: int, **extra) -> dict:
    payload: dict = {
        "layer_id": "schools",
        "feature_id": "school-0001",
        "selected_ids": _ids(n_ids),
        "id_field": "osm_id",
        "dataset_ref": "ref:geojson-schools",
    }
    payload.update(extra)
    return payload


# ── 1. normalize：范围保真 + 披露 ────────────────────────────────────────


def test_box_select_20_features_survive_normalize():
    """框选 20：归一化后 selected_ids 必须仍是 20（现状：静默截到 8）。"""
    out = normalize_payload("feature_selected", _selection_payload(20))
    assert out is not None
    assert len(out["selected_ids"]) == 20
    assert out["selected_ids"] == _ids(20)


def test_matched_count_whitelisted_and_passthrough():
    """matched_count（真实命中数）进入白名单并原样保留。"""
    out = normalize_payload(
        "feature_selected", _selection_payload(12, matched_count=20)
    )
    assert out is not None
    assert out["matched_count"] == 20


def test_selected_ids_cap_50_with_server_derived_truncation_flag():
    """60 个 id：保留 50（三方对齐上限），服务端派生 ids_truncated=True。"""
    out = normalize_payload(
        "feature_selected", _selection_payload(60, matched_count=60)
    )
    assert out is not None
    assert len(out["selected_ids"]) == 50
    assert out["ids_truncated"] is True
    assert out["matched_count"] == 60


def test_ids_truncated_false_when_not_truncated():
    """≤50：ids_truncated 明确为 False（显式而非缺席——缺席无法区分旧事件）。"""
    out = normalize_payload(
        "feature_selected", _selection_payload(20, matched_count=20)
    )
    assert out is not None
    assert out["ids_truncated"] is False


def test_frontend_precapped_sample_is_disclosed_as_truncated():
    """真实前端路径：selection-store 上行前已截到 50（50 ids + matched=80）
    —— 携带集是样本，服务端判据 2（matched > 携带数）必须派生 True，
    不得误判为完整集（否则 truncated_sample 披露永不触发）。"""
    out = normalize_payload(
        "feature_selected", _selection_payload(50, matched_count=80)
    )
    assert out is not None
    assert len(out["selected_ids"]) == 50
    assert out["ids_truncated"] is True
    # 声称面 → 验证面链路同样披露（validate 的 checks.features 依据）。
    claimed = derive_claimed_scope(
        {"selection": {"selected_ids": _ids(50), "matched_count": 80,
                        "ids_truncated": True, "layer_id": "schools",
                        "dataset_ref": "ref:geojson-schools",
                        "source": {"server_seq": 1}}, "event_count": 1},
        None,
    )
    assert claimed["matched_count"] == 80
    assert claimed["ids_truncated"] is True


def test_contradictory_disclosure_not_flagged():
    """矛盾披露（matched_count=3 < 携带 50）：不是截断（可能去重），
    不虚报 True；matched_count 原样保留供消费方自行判断。"""
    out = normalize_payload(
        "feature_selected", _selection_payload(50, matched_count=3)
    )
    assert out is not None
    assert out["ids_truncated"] is False
    assert out["matched_count"] == 3


# ── 4. 复审 P0-1：记录预算与宽载荷预算同层（防 accepted-but-dropped）──


def test_real_frontend_path_32char_ids_survive_ingest():
    """复审 P0-1 钉扎：真实前端路径（emit clip 32 字符 × 50 + matched=80
    + 完整框选键）必须 accepted **且入环**——修复前 _MAX_RECORD_BYTES=1536
    导致 accepted-but-dropped（ack 谎报 accepted、Agent 看到零个）。"""
    ids = [(f"node/1234567890abcdefghij{i:06d}" + "x" * 32)[:32] for i in range(1, 51)]
    state = LedgerState()
    event = {
        "event_id": "evt-p0-pin-00001",
        "client_instance_id": "tab-p0pin001",
        "client_seq": 1,
        "kind": "feature_selected",
        "actor": "user",
        "payload": {
            "layer_id": "schools",
            "dataset_ref": "ref:geojson-schools-cd",
            "id_field": "osm_id",
            "selected_ids": ids,
            "matched_count": 80,
            "ids_truncated": True,
            "bbox": [104.02, 30.64, 104.1, 30.7],
        },
    }
    verdict = apply_event(state, event)
    assert verdict.verdict == VERDICT_ACCEPTED
    assert len(state.events) == 1, "事件必须入环（accepted-but-dropped 即 #2004 形态）"
    assert state.dropped == 0
    snapshot = project_interaction_snapshot(state.events)
    selection = snapshot["selection"]
    assert selection is not None
    assert len(selection["selected_ids"]) == 50
    assert selection["matched_count"] == 80
    assert selection["ids_truncated"] is True


def test_record_budget_covers_widest_normalized_payload():
    """预算分层不变量：单条记录预算 ≥ 最宽载荷预算 + 信封余量。

    normalize 放行而 ledger 丢弃 = 静默丢失（accepted-but-dropped）。
    """
    from app.services.gis_harness.interaction_ir.contracts import (
        MAX_PAYLOAD_BYTES_SELECTION,
    )
    from app.services.gis_harness.interaction_ir.ingest import _MAX_RECORD_BYTES

    assert _MAX_RECORD_BYTES >= MAX_PAYLOAD_BYTES_SELECTION


# ── 5. 复审 P1-2/P2-2：验证面 bbox 分义 + pre-turn 披露上界 ─────────────


def test_selection_bbox_survives_validation_distinct_from_dataset_bbox():
    """复审 P1-2：框选范围经验证面独立键（selection_bbox）透传——
    bbox 键保留数据集描述符语义，二者不得混用（范围放大误导）。"""
    import asyncio

    from app.services.gis_situation.analysis_scope import validate_analysis_scope

    claimed = {
        "kind": "feature_ids",
        "layer_id": "schools",
        "dataset_ref": "ref:geojson-schools-cd",
        "feature_ids": [f"sch-{i:04d}" for i in range(1, 6)],
        "matched_count": 5,
        "ids_truncated": False,
        "bbox": [104.04, 30.65, 104.06, 30.67],  # 框选街区范围
        "sources": ["ir_ledger"],
    }

    class _FakeStore:
        async def resolve_alias(self, _sid, ref):
            return ref

        async def get(self, _sid, ref):
            return {
                "geojson": {
                    "type": "FeatureCollection",
                    "features": [
                        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [104.05, 30.66]},
                         "properties": {"osm_id": f"sch-{i:04d}"}} for i in range(1, 6)
                    ],
                }
            }

        async def get_ref_descriptor(self, _sid, ref):
            return {
                "content_revision": 3,
                "feature_count": 5,
                "crs": "EPSG:4326",
                "bbox": [103.9, 30.5, 104.2, 30.9],  # 数据集全量范围（全成都量级）
            }

    class _FakeSpecStore:
        async def get_mapspec(self, _sid):
            return {
                "layers": [{
                    "id": "schools", "source": "src-schools",
                    "provenance": {"content_revision": 3},
                }],
                "sources": {"src-schools": {"ref": "ref:geojson-schools-cd"}},
            }

    validated = asyncio.run(validate_analysis_scope(
        "sess-p12", claimed, store=_FakeStore(), mapspec_store=_FakeSpecStore(),
    ))
    assert validated["status"] == "usable", validated
    assert validated["bbox"] == [103.9, 30.5, 104.2, 30.9]  # 数据集语义保留
    assert validated["selection_bbox"] == [104.04, 30.65, 104.06, 30.67]  # 框选范围独立


def test_pre_turn_matched_count_upper_bounded():
    """复审 P2-2：pre-turn 快照路径（不经 IR 摄入）的 matched_count 同受
    ≤10M 上界（在 _scope_disclosure 收敛，不改 chat.py —— #2012 避让）。"""
    snapshot = {
        "event_count": 1,
        "selection": {
            "layer_id": "schools",
            "dataset_ref": "ref:geojson-schools-cd",
            "selected_ids": ["sch-1", "sch-2"],
            "matched_count": 10 ** 12,  # 伪造巨数（pre-turn 观察路径可携带）
            "source": {"server_seq": 1},
        },
    }
    claimed = derive_claimed_scope(snapshot, None)
    # 巨数不是有界披露 —— 丢键不猜，回退携带数（2），不得直达 scope 输出。
    assert claimed.get("matched_count") == 2


def test_server_rewrites_false_truncation_claim():
    """客户端申报 ids_truncated=False 但实际送 60 → 服务端覆写为 True。"""
    out = normalize_payload(
        "feature_selected", _selection_payload(60, matched_count=60, ids_truncated=False)
    )
    assert out is not None
    assert out["ids_truncated"] is True


def test_selection_kinds_get_wider_byte_budget():
    """50×32 字符 id 集（≈1.9KB）对 selection kind 必须可承载。

    现状：>512B 整事件拒绝 —— 修复若只放 ids 不放预算，截断会恶化为
    整事件消失（更隐蔽的假象：Agent 连 8 个都看不到）。
    """
    ids = [f"s-{i:04d}-{'x' * 24}" for i in range(50)]  # 32 字符/个
    out = normalize_payload(
        "feature_selected", {
            "layer_id": "schools",
            "selected_ids": ids,
            "id_field": "osm_id",
            "matched_count": 50,
        }
    )
    assert out is not None
    assert len(out["selected_ids"]) == 50
    # 非选择 kind 的 512B 纪律不放松（回归护栏，见 test_normalize_payload_byte_budget）。


def test_generic_lists_remain_capped_at_8():
    """非覆写键的通用 list（如 field_names）仍守 ≤8 纪律
    （selected_ids/categories 是 per-key 覆写，不放松通用面）。"""
    out = normalize_payload(
        "feature_selected", {
            "layer_id": "schools",
            "selected_ids": _ids(3),
            "field_names": [f"field_{i}" for i in range(20)],
        }
    )
    assert out is not None
    assert len(out["field_names"]) == 8


def test_matched_count_bounded_and_coerced():
    """matched_count 只接受有界整数（防浮点/巨数注入）。"""
    out = normalize_payload(
        "feature_selected", _selection_payload(5, matched_count=10**12)
    )
    assert out is None or out.get("matched_count") != 10**12


# ── 2. ingest → projection → claimed scope：全链 20 保真 ────────────────


def _ingest_selection(n_ids: int, seq: int = 1, **extra) -> LedgerState:
    state = LedgerState()
    event = {
        "event_id": f"evt-range-{seq:08d}",
        "client_instance_id": "tab-range0001",
        "client_seq": seq,
        "kind": "feature_selected",
        "actor": "user",
        "payload": _selection_payload(n_ids, **extra),
    }
    verdict = apply_event(state, event)
    assert verdict.verdict == VERDICT_ACCEPTED, verdict
    return state


def test_full_chain_20_ids_reach_claimed_scope():
    """框选 20 → ingest → 投影 → 声称面：feature_ids 必须 20 个。"""
    state = _ingest_selection(20, matched_count=20)
    snapshot = project_interaction_snapshot(state.events)
    selection = snapshot["selection"]
    assert selection is not None
    assert len(selection["selected_ids"]) == 20
    assert selection["matched_count"] == 20
    claimed = derive_claimed_scope(snapshot, None)
    assert claimed["kind"] == "feature_ids"
    assert claimed["feature_ids"] == _ids(20)


def test_full_chain_truncation_is_disclosed_end_to_end():
    """框选 60：声称面携带 50 id + matched_count=60 → 消费方可诚实披露。"""
    state = _ingest_selection(60, matched_count=60)
    snapshot = project_interaction_snapshot(state.events)
    selection = snapshot["selection"]
    assert selection is not None
    assert len(selection["selected_ids"]) == 50
    assert selection["ids_truncated"] is True
    assert selection["matched_count"] == 60
    claimed = derive_claimed_scope(snapshot, None)
    assert claimed["kind"] == "feature_ids"
    assert len(claimed["feature_ids"]) == 50


def test_table_selection_same_range_semantics():
    """table_selection_changed 同为 selection kind：范围语义一致。"""
    out = normalize_payload(
        "table_selection_changed", {
            "layer_id": "schools",
            "selected_ids": _ids(15),
            "action": "select",
        }
    )
    assert out is not None
    assert len(out["selected_ids"]) == 15


# ── 3. SW-02 残余：chart categories 对齐 + bbox 谓词进 IR ────────────────


def test_chart_categories_aligned_to_selection_store_bound():
    """图表类别上行与 selection-store selected_categories ≤20 对齐。

    现状（基线）：前端 emit 截 8 + 后端 list 面纪律 8 —— 图表 12 个类别
    的联动对 Agent 只见 8 个。新语义：20 对齐 + 宽预算承载。
    """
    out = normalize_payload(
        "chart_selection_changed", {
            "layer_id": "schools",
            "field": "district",
            "categories": [f"cat-{i:02d}" for i in range(24)],
        }
    )
    assert out is not None
    assert len(out["categories"]) == 20


def test_brush_bbox_predicate_whitelisted_and_claimed():
    """框选 bbox 谓词进 IR："刚才框选的区域"作为空间范围可解析。"""
    out = normalize_payload(
        "feature_selected", {
            "layer_id": "schools",
            "selected_ids": _ids(5),
            "matched_count": 5,
            "bbox": [104.04, 30.65, 104.1, 30.71],
        }
    )
    assert out is not None
    assert out["bbox"] == [104.04, 30.65, 104.1, 30.71]
    # 声称面合并 bbox（空间工具可用 extent 兜底）。
    state = LedgerState()
    event = {
        "event_id": "evt-bbox-0001",
        "client_instance_id": "tab-range0001",
        "client_seq": 1,
        "kind": "feature_selected",
        "actor": "user",
        "payload": {
            "layer_id": "schools",
            "selected_ids": _ids(5),
            "matched_count": 5,
            "bbox": [104.04, 30.65, 104.1, 30.71],
        },
    }
    verdict = apply_event(state, event)
    assert verdict.verdict == VERDICT_ACCEPTED, verdict
    snapshot = project_interaction_snapshot(state.events)
    claimed = derive_claimed_scope(snapshot, None)
    assert claimed["bbox"] == [104.04, 30.65, 104.1, 30.71]
    assert claimed["kind"] == "feature_ids"
