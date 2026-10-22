"""G01/WP-A 角色绑定测试（role_binding + qualification_bridge 角色级入口）。

验收矩阵：
- 确定性：乱序 ref 入库 → 同一绑定结果（ref_id 排序，非 dict 迭代序）；
- 显式绑定：作用域内 bound（+期望指纹一致 freshness 确认）；
- stale：记录面期望 ≠ 当前语义 → ROLE_BINDING_STALE（复用 reuse verdict）；
- not_authorized：绑定 ref 不在会话作用域（跨会话/淘汰）→ fail-closed
  拒绝且不泄漏任何 descriptor/指纹供给；
- ambiguous：两个同名/同证据数据集 → 候选清单，绝不取首个；
- not_found：无正向证据 → 不猜测绑定；
- partial：唯一候选缺角色关键事实（几何 unknown / 分母字段缺席）；
- 损坏 descriptor：fail-closed 可见（ROLE_BINDING_DESCRIPTOR_UNREADABLE）；
- primary_session_descriptor 向后兼容（单数据 session 行为不变）；
- 资格投影：bound/partial 供 descriptor、stale 供 descriptor+expected、
  ambiguous/not_found 不供（绝不拿别的数据集顶替）。
"""
import pytest

from app.lib.gis.dataset_profile import DatasetProfile
from app.services.dataset_semantics import derive_descriptor
from app.services.dataset_semantics.role_binding import (
    CODE_AMBIGUOUS,
    CODE_BOUND,
    CODE_BOUND_BY_REF,
    CODE_DESCRIPTOR_UNREADABLE,
    CODE_FACTS_INCOMPLETE,
    CODE_NOT_FOUND,
    CODE_OUT_OF_SCOPE,
    CODE_STALE,
    RoleBindingReport,
    bind_session_roles,
    collect_session_evidence,
    primary_binding,
    role_qualification_evidence,
)
from app.services.dataset_semantics.store import (
    DatasetSemanticStore,
    reset_dataset_semantic_store,
)


def _schools_descriptor(key="ref:data-schools", count=30):
    p = DatasetProfile(
        source="ref_descriptor", feature_count=count,
        geometry_types=["Point"], crs="EPSG:4326",
        fields={"学校数量": "number", "name": "string"},
        numeric_fields=["学校数量"], fields_status="explicit",
    )
    feats = [
        {"geometry": {"type": "Point", "coordinates": [104.0 + i * 0.01, 30.6]},
         "properties": {"学校数量": i % 5, "name": f"小学{i % 3}"}}
        for i in range(count)
    ]
    return derive_descriptor(p, dataset_key=key, features=feats)


def _boundary_descriptor(key="ref:data-bounds"):
    p = DatasetProfile(
        source="ref_descriptor", feature_count=20,
        geometry_types=["Polygon"], crs="EPSG:4326",
        fields={"区名": "string"}, fields_status="explicit",
    )
    feats = [
        {"geometry": {"type": "Polygon", "coordinates": [[[104.0, 30.6], [104.1, 30.6], [104.1, 30.7], [104.0, 30.6]]]},
         "properties": {"区名": f"区{i}"}}
        for i in range(20)
    ]
    return derive_descriptor(p, dataset_key=key, features=feats)


def _population_descriptor(key="ref:data-pop"):
    p = DatasetProfile(
        source="ref_descriptor", feature_count=20,
        geometry_types=["Polygon"], crs="EPSG:4326",
        fields={"人口": "number", "区名": "string"},
        numeric_fields=["人口"], fields_status="explicit",
    )
    feats = [
        {"geometry": {"type": "Polygon", "coordinates": [[[104.0, 30.6], [104.1, 30.6], [104.1, 30.7], [104.0, 30.6]]]},
         "properties": {"人口": 10000 * (i + 1), "区名": f"区{i}"}}
        for i in range(20)
    ]
    return derive_descriptor(p, dataset_key=key, features=feats)


def _rec(ref_id, alias, descriptor):
    from app.services.dataset_semantics.role_binding import RefSemanticRecord

    return RefSemanticRecord(
        ref_id=ref_id, alias=alias, read_status="ok",
        descriptor=descriptor,
        fingerprint=descriptor.descriptor_fingerprint,
    )


def _role_req(role, **kw):
    from app.services.gis_harness.workflow_schema import DataRoleRequirement

    return DataRoleRequirement(role=role, **kw)


@pytest.fixture()
def sem_store(tmp_path, monkeypatch):
    import app.services.dataset_semantics.store as store_mod
    import app.services.session_data as session_data_mod
    from app.services.session_data import MemorySessionStore

    store = DatasetSemanticStore(base_dir=tmp_path)
    monkeypatch.setattr(store_mod, "_store", store)
    # 会话数据面钉扎内存后端（环境 REDIS_URL 可能指向不可达实例）。
    monkeypatch.setattr(
        session_data_mod, "session_data_manager", MemorySessionStore())
    yield store
    reset_dataset_semantic_store()


# ── 确定性：乱序入库 → 同一绑定 ─────────────────────────────────────────

def test_bind_is_order_invariant():
    schools = _rec("ref:data-b", "学校", _schools_descriptor())
    bounds = _rec("ref:data-a", "边界", _boundary_descriptor())
    reqs = [_role_req("subject", geometry_kinds=["point"])]
    r1 = bind_session_roles(reqs, [schools, bounds])
    r2 = bind_session_roles(reqs, [bounds, schools])
    b1, b2 = r1.binding_by_role()["subject"], r2.binding_by_role()["subject"]
    assert b1.status == b2.status == "bound"
    assert b1.ref_id == b2.ref_id == "ref:data-b"
    assert r1.to_bounded_dict() == r2.to_bounded_dict()


def test_evidence_tie_uses_ref_id_order_for_candidates():
    # 两个同证据点数据集 → ambiguous；候选按 ref_id 升序（确定性披露）。
    a = _rec("ref:data-zzz", "同名", _schools_descriptor())
    b = _rec("ref:data-aaa", "同名", _schools_descriptor())
    report = bind_session_roles([_role_req("subject", geometry_kinds=["point"])], [a, b])
    binding = report.binding_by_role()["subject"]
    assert binding.status == "ambiguous"
    assert binding.reason_code == CODE_AMBIGUOUS
    assert [c.ref_id for c in binding.candidates] == ["ref:data-aaa", "ref:data-zzz"]


# ── 显式绑定 / stale / 作用域拒绝 ──────────────────────────────────────

def test_explicit_binding_with_fresh_fingerprint():
    d = _schools_descriptor()
    rec = _rec("ref:data-s", "学校", d)
    report = bind_session_roles(
        [_role_req("subject")], [rec],
        bound_refs={"subject": "ref:data-s"},
        expected_fingerprints={"ref:data-s": d.descriptor_fingerprint},
    )
    b = report.binding_by_role()["subject"]
    assert b.status == "bound"
    assert b.reason_code == CODE_BOUND_BY_REF
    assert "freshness" in b.detail


def test_explicit_binding_stale_on_fingerprint_drift():
    old = _schools_descriptor(count=30)
    new = _schools_descriptor(count=99)  # 内容证据变化 → 新指纹
    assert old.descriptor_fingerprint != new.descriptor_fingerprint
    rec = _rec("ref:data-s", "学校", new)
    report = bind_session_roles(
        [_role_req("subject")], [rec],
        bound_refs={"subject": "ref:data-s"},
        expected_fingerprints={"ref:data-s": old.descriptor_fingerprint},
    )
    b = report.binding_by_role()["subject"]
    assert b.status == "stale"
    assert b.reason_code == CODE_STALE
    # 资格投影必须带 expected：让资格层 DESCRIPTOR_STALE_* 精确降级。
    ev = role_qualification_evidence(report)["subject"]
    assert ev["descriptor"] is new
    assert ev["expected_descriptor_fingerprint"] == old.descriptor_fingerprint


def test_out_of_scope_ref_is_not_authorized_and_leaks_nothing():
    # 绑定的 ref 不在会话 refs（跨会话/跨项目/已淘汰）→ fail-closed。
    rec = _rec("ref:data-s", "学校", _schools_descriptor())
    report = bind_session_roles(
        [_role_req("subject")], [rec],
        bound_refs={"subject": "ref:other-session-9999"},
        expected_fingerprints={"ref:other-session-9999": "dsd-v1:deadbeef"},
    )
    b = report.binding_by_role()["subject"]
    assert b.status == "not_authorized"
    assert b.reason_code == CODE_OUT_OF_SCOPE
    assert b.fingerprint == ""  # 不泄漏他人指纹
    assert "subject" not in role_qualification_evidence(report)


def test_explicit_binding_with_unreadable_descriptor(tmp_path):
    # ref 在作用域内但语义损坏 → partial + 可见 reason（不吞）。
    from app.services.dataset_semantics.role_binding import RefSemanticRecord

    rec = RefSemanticRecord(
        ref_id="ref:data-bad", alias="坏数据", read_status="corrupt",
        reason_code="DESCRIPTOR_STORE_CORRUPT")
    report = bind_session_roles(
        [_role_req("boundary")], [rec],
        bound_refs={"boundary": "ref:data-bad"})
    b = report.binding_by_role()["boundary"]
    assert b.status == "partial"
    assert b.reason_code == CODE_DESCRIPTOR_UNREADABLE


# ── 证据推导路径 ────────────────────────────────────────────────────────

def test_geometry_evidence_binds_unique_candidate():
    schools = _rec("ref:data-s", "学校", _schools_descriptor())
    bounds = _rec("ref:data-b", "边界", _boundary_descriptor())
    report = bind_session_roles(
        [_role_req("boundary", geometry_kinds=["polygon"]),
         _role_req("subject", geometry_kinds=["point"])],
        [schools, bounds])
    by = report.binding_by_role()
    assert by["boundary"].status == "bound"
    assert by["boundary"].ref_id == "ref:data-b"
    assert by["boundary"].reason_code == CODE_BOUND
    assert by["subject"].status == "bound"
    assert by["subject"].ref_id == "ref:data-s"


def test_denominator_field_evidence():
    pop = _rec("ref:data-pop", "人口", _population_descriptor())
    bounds = _rec("ref:data-b", "边界", _boundary_descriptor())
    # 人口数据集：几何匹配 boundary 兼容（polygon）+ 分母字段 → 分数更高。
    report = bind_session_roles(
        [_role_req("denominator", geometry_kinds=["polygon"])], [pop, bounds])
    b = report.binding_by_role()["denominator"]
    assert b.status == "bound"
    assert b.ref_id == "ref:data-pop"
    assert "denominator_field" in b.matched_on


def test_not_found_without_positive_evidence():
    schools = _rec("ref:data-s", "学校", _schools_descriptor())
    report = bind_session_roles(
        [_role_req("elevation")], [schools])  # 无栅格/高程证据
    b = report.binding_by_role()["elevation"]
    assert b.status == "not_found"
    assert b.reason_code == CODE_NOT_FOUND


def test_partial_when_role_facts_missing():
    # 唯一候选但缺时间字段证据 → partial（时间角色）。
    d = _population_descriptor()
    d = d.model_copy(update={"temporal": d.temporal.model_copy(update={"has_time_field": None})})
    rec = _rec("ref:data-pop", "人口", d)
    report = bind_session_roles(
        [_role_req("baseline", geometry_kinds=["polygon"])], [rec])
    b = report.binding_by_role()["baseline"]
    assert b.status == "partial"
    assert b.reason_code == CODE_FACTS_INCOMPLETE
    assert "time_field" in b.detail


def test_no_evidence_records_means_not_found():
    report = bind_session_roles([_role_req("subject")], [])
    assert report.binding_by_role()["subject"].status == "not_found"


# ── 资格投影 / 主绑定 ───────────────────────────────────────────────────

def test_role_qualification_evidence_semantics():
    schools = _rec("ref:data-s", "学校", _schools_descriptor())
    # measure 显式绑定到另一个数据集（面状）——与 subject 的点证据不竞争。
    stale_new = _boundary_descriptor()
    stale_rec = _rec("ref:data-t", "旧", stale_new)
    report = bind_session_roles(
        [_role_req("subject", geometry_kinds=["point"]),
         _role_req("measure"),
         _role_req("denominator")],
        [schools, stale_rec],
        bound_refs={"measure": "ref:data-t"},
        expected_fingerprints={"ref:data-t": "dsd-v1:mismatch"},
    )
    ev = role_qualification_evidence(report)
    assert set(ev) == {"subject", "measure"}  # denominator not_found 不供
    assert ev["subject"]["descriptor"] is not None
    assert ev["subject"]["expected_descriptor_fingerprint"] == ""
    assert ev["measure"]["expected_descriptor_fingerprint"] == "dsd-v1:mismatch"


def test_primary_binding_prefers_subject_role():
    schools = _rec("ref:data-s", "学校", _schools_descriptor())
    bounds = _rec("ref:data-b", "边界", _boundary_descriptor())
    report = bind_session_roles(
        [_role_req("boundary", geometry_kinds=["polygon"]),
         _role_req("subject", geometry_kinds=["point"])],
        [schools, bounds])
    p = primary_binding(report)
    assert p is not None and p.role == "subject" and p.ref_id == "ref:data-s"


def test_primary_binding_none_when_all_ambiguous():
    a = _rec("ref:data-zzz", "同名", _schools_descriptor())
    b = _rec("ref:data-aaa", "同名", _schools_descriptor())
    report = bind_session_roles([_role_req("subject")], [a, b])
    assert primary_binding(report) is None


# ── collect_session_evidence（store/session 集成）───────────────────────

async def test_collect_session_evidence_sorted_and_bounded(sem_store):
    from app.services.session_data import session_data_manager

    sid = "g01-rb-evidence"
    # 乱序入库（ref id 带随机后缀，生成顺序与字典序无关）。
    ref_zzz = await session_data_manager.store(sid, {"k": 1}, prefix="data-zzz")
    ref_aaa = await session_data_manager.store(sid, {"k": 2}, prefix="data-aaa")
    # store 键 = ref id；descriptor.dataset_key 必须同键（DATASET_KEY_MISMATCH 纪律）。
    d1 = _schools_descriptor(key=ref_zzz)
    d2 = _boundary_descriptor(key=ref_aaa)
    assert (await sem_store.put(sid, ref_zzz, d1)).ok
    assert (await sem_store.put(sid, ref_aaa, d2)).ok
    assert ref_zzz.startswith("ref:data-zzz-")
    assert ref_aaa.startswith("ref:data-aaa-")

    records = await collect_session_evidence(sid)
    ids = [r.ref_id for r in records]
    assert ids == sorted(ids)  # 排序确定性
    assert len(records) == 2
    by_ref = {r.ref_id: r for r in records}
    assert by_ref[ref_aaa].read_status == "ok"
    assert by_ref[ref_zzz].read_status == "ok"
    assert by_ref[ref_aaa].fingerprint.startswith("dsd-v1:")
    # 有界：max_refs=1 只读第一个（排序后）。
    only_first = await collect_session_evidence(sid, max_refs=1)
    assert [r.ref_id for r in only_first] == [ref_aaa]


async def test_collect_session_evidence_missing_descriptor(sem_store):
    from app.services.session_data import session_data_manager

    sid = "g01-rb-missing"
    await session_data_manager.store(sid, {"k": 1})
    records = await collect_session_evidence(sid)
    assert len(records) == 1
    assert records[0].read_status == "missing"


async def test_session_role_binding_bridge(sem_store, monkeypatch):
    from app.services.dataset_semantics import session_role_binding
    from app.services.session_data import session_data_manager

    sid = "g01-rb-bridge"
    real_ref = await session_data_manager.store(sid, {"k": 1}, prefix="data-s")
    d = _schools_descriptor(key=real_ref)
    await sem_store.put(sid, real_ref, d)
    # durable 记录面：绑定事实落 recovery_state（record 时从 store 现读
    # 第一版指纹）→ 之后 store 内容前进 → 绑定对账必须判 stale。
    from app.services.gis_harness.durable_context import record_source_fingerprints

    await record_source_fingerprints(sid, [
        {"ref": real_ref, "fingerprint": "aaaa"}])
    d2 = _schools_descriptor(count=55, key=real_ref)
    await sem_store.put(sid, real_ref, d2)

    report = await session_role_binding(
        sid, [_role_req("subject")],
        bound_refs={"subject": real_ref})
    b = report.binding_by_role()["subject"]
    # 记录面指纹 = 第一版；store 现读 = 第二版 → stale。
    assert b.status == "stale"
    assert b.expected_fingerprint == d.descriptor_fingerprint
    assert b.fingerprint == d2.descriptor_fingerprint


# ── primary_session_descriptor 向后兼容 ────────────────────────────────

async def test_primary_session_descriptor_unchanged_single_ref(sem_store):
    from app.services.dataset_semantics import primary_session_descriptor
    from app.services.session_data import session_data_manager

    sid = "g01-rb-compat"
    real_ref = await session_data_manager.store(sid, {"k": 1}, prefix="data-only")
    d = _schools_descriptor(key=real_ref)
    await sem_store.put(sid, real_ref, d)
    got = await primary_session_descriptor(sid)
    assert got is not None
    assert got.descriptor_fingerprint == d.descriptor_fingerprint
    assert await primary_session_descriptor("") is None


def test_report_bounded_dict_shape():
    a = _rec("ref:data-zzz", "同名", _schools_descriptor())
    b = _rec("ref:data-aaa", "同名", _schools_descriptor())
    report = bind_session_roles(
        [_role_req("subject", geometry_kinds=["point"])], [a, b], truncated_refs=2)
    out = report.to_bounded_dict()
    assert out["truncated_refs"] == 2
    assert out["status_counts"]["ambiguous"] == 1
    assert isinstance(report, RoleBindingReport)
