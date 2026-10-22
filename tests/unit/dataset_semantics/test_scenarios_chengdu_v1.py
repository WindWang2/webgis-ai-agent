"""G01 验收：六类任务场景的贯通链路（role binding → preflight → 资格 → lineage）。

场景（DoD：至少 6 个任务场景形成含源身份/单位/空间时间依据的产物证据）：
S1 成都小学的分布情况      —— 单数据分布，全链 compatible；
S2 成都各区小学数量        —— schools + boundary 联结，数量口径；
S3 每平方公里学校密度      —— 无分母 → typed gap（供分母或改数量）；
   有人口分母 → compatible，分母证据入 lineage；
S4 2020/2025 学校变化      —— 双期不重叠 → TEMPORAL_DISJOINT；同期 → 过；
S5 缺行政边界             —— ROLE_MISSING typed gap（需要什么数据）；
S6 两个同名数据集          —— 角色绑定 ambiguous（候选清单，绝不取首个）。

全链断言：零数据扫描（session_data 载荷读 = 0；语义 store 读 = O(refs)）。
"""
import time

import pytest

from app.lib.gis.dataset_profile import DatasetProfile
from app.services.dataset_semantics import derive_descriptor
from app.services.dataset_semantics.preflight import (
    preflight_multi_dataset,
)
from app.services.dataset_semantics.role_binding import (
    bind_session_roles,
    collect_session_evidence,
    role_qualification_evidence,
)
from app.services.dataset_semantics.store import (
    DatasetSemanticStore,
    reset_dataset_semantic_store,
)
from app.services.gis_harness.data_qualification import (
    qualify_workflow_data_roles,
)
from app.services.gis_harness.workflow_schema import (
    DataRoleRequirement,
    DataRoleResolution,
)

CD_BBOX = [103.9, 30.5, 104.2, 30.7]      # 成都 bbox（经纬度）
BJ_BBOX = [115.9, 39.7, 116.3, 40.1]      # 北京 bbox（不相交对照）


def _schools(key, count=40, coverage=None, crs="EPSG:4326", bbox=None):
    p = DatasetProfile(
        source="ref_descriptor", feature_count=count,
        geometry_types=["Point"], crs=crs,
        fields={"学校数量": "number", "name": "string"},
        numeric_fields=["学校数量"], fields_status="explicit",
    )
    feats = [
        {"geometry": {"type": "Point",
                      "coordinates": [104.0 + (i % 8) * 0.02, 30.55 + (i // 8) * 0.02]},
         "properties": {"学校数量": i % 5, "name": f"小学{i % 6}"}}
        for i in range(count)
    ]
    d = derive_descriptor(p, dataset_key=key, features=feats)
    return _with(d, bbox=bbox, coverage=coverage)


def _bounds(key, coverage=None, bbox=None, crs="EPSG:4326"):
    p = DatasetProfile(
        source="ref_descriptor", feature_count=20,
        geometry_types=["Polygon"], crs=crs,
        fields={"区名": "string"}, fields_status="explicit",
    )
    feats = [
        {"geometry": {"type": "Polygon", "coordinates": [[
            [103.9 + i * 0.05, 30.5], [103.95 + i * 0.05, 30.5],
            [103.95 + i * 0.05, 30.7], [103.9 + i * 0.05, 30.7],
            [103.9 + i * 0.05, 30.5]]]},
         "properties": {"区名": f"区{i}"}}
        for i in range(20)
    ]
    d = derive_descriptor(p, dataset_key=key, features=feats)
    return _with(d, bbox=bbox, coverage=coverage)


def _pop(key, coverage=None, bbox=None):
    p = DatasetProfile(
        source="ref_descriptor", feature_count=20,
        geometry_types=["Polygon"], crs="EPSG:4326",
        fields={"人口": "number", "区名": "string"},
        numeric_fields=["人口"], fields_status="explicit",
    )
    feats = [
        {"geometry": {"type": "Polygon", "coordinates": [[
            [103.9 + i * 0.05, 30.5], [103.95 + i * 0.05, 30.5],
            [103.95 + i * 0.05, 30.7], [103.9 + i * 0.05, 30.7],
            [103.9 + i * 0.05, 30.5]]]},
         "properties": {"人口": 80000 * (i + 1), "区名": f"区{i}"}}
        for i in range(20)
    ]
    d = derive_descriptor(
        p, dataset_key=key, features=feats,
        # user-wins：用户/计划显式声明分母字段（低置信语义闸的消歧通道）
        user_roles={"人口": "normalization_denominator"})
    return _with(d, bbox=bbox, coverage=coverage)


def _with(d, bbox=None, coverage=None):
    updates = {}
    if bbox:
        updates["bbox"] = list(bbox)
    if coverage:
        updates["temporal"] = d.temporal.model_copy(update={
            "has_time_field": True,
            "coverage_start": coverage[0], "coverage_end": coverage[1]})
    return d.model_copy(update=updates) if updates else d


def _rec(ref_id, alias, descriptor):
    from app.services.dataset_semantics.role_binding import RefSemanticRecord

    return RefSemanticRecord(ref_id=ref_id, alias=alias, read_status="ok",
                             descriptor=descriptor,
                             fingerprint=descriptor.descriptor_fingerprint)


def _reqs(*roles):
    geom = {"subject": ["point"], "boundary": ["polygon"],
            "denominator": ["polygon"], "baseline": ["point"],
            "target_time": ["point"]}
    return [DataRoleRequirement(role=r, geometry_kinds=geom.get(r, []))
            for r in roles]


def _res(*roles):
    return [DataRoleResolution(role=r, required=(r != "baseline"),
                               acquisition="local", status="bound")
            for r in roles]


@pytest.fixture()
def sem_store(tmp_path, monkeypatch):
    import app.services.dataset_semantics.store as store_mod

    store = DatasetSemanticStore(base_dir=tmp_path)
    monkeypatch.setattr(store_mod, "_store", store)
    yield store
    reset_dataset_semantic_store()


# ── S1 分布：单数据 → compatible ────────────────────────────────────────

def test_s1_distribution_chain():
    records = [_rec("ref:cd-schools", "成都小学",
                    _schools("ref:cd-schools", bbox=CD_BBOX))]
    report = bind_session_roles(_reqs("subject"), records)
    b = report.binding_by_role()["subject"]
    assert b.status == "bound" and b.ref_id == "ref:cd-schools"
    ev = role_qualification_evidence(report)
    pre = preflight_multi_dataset(ev, expected_roles=("subject",))
    assert pre.verdict == "compatible"
    quals = qualify_workflow_data_roles(_reqs("subject"), _res("subject"),
                                        role_evidence=ev)
    assert quals[0].state == "eligible"
    # lineage：源身份（ref + 指纹）与空间依据（bbox）入证据面。
    assert b.fingerprint.startswith("dsd-v1:")
    assert b.to_bounded_dict()["descriptor_fingerprint"] == b.fingerprint


# ── S2 各区数量：schools + boundary 联结 ────────────────────────────────

def test_s2_count_by_district_chain():
    records = [
        _rec("ref:cd-schools", "成都小学", _schools("ref:cd-schools", bbox=CD_BBOX)),
        _rec("ref:cd-bounds", "成都区界", _bounds("ref:cd-bounds", bbox=CD_BBOX)),
    ]
    report = bind_session_roles(_reqs("subject", "boundary"), records)
    by = report.binding_by_role()
    assert by["subject"].ref_id == "ref:cd-schools"
    assert by["boundary"].ref_id == "ref:cd-bounds"
    ev = role_qualification_evidence(report)
    pre = preflight_multi_dataset(ev, expected_roles=("subject", "boundary"))
    assert pre.verdict == "compatible"  # CRS 同、bbox 相交
    quals = qualify_workflow_data_roles(
        _reqs("subject", "boundary"), _res("subject", "boundary"),
        role_evidence=ev)
    assert {q.role: q.state for q in quals}["boundary"] == "eligible"


# ── S3 密度：无分母 → typed gap；有分母 → compatible + 分母证据 ────────

def test_s3_density_without_denominator_gap():
    # 只有学校点数据：无分母字段、无面单元 → 密度缺口必须typed披露。
    records = [
        _rec("ref:cd-schools", "成都小学", _schools("ref:cd-schools", bbox=CD_BBOX)),
    ]
    report = bind_session_roles(_reqs("subject"), records)
    ev = role_qualification_evidence(report)
    pre = preflight_multi_dataset(ev, analysis_kind="density")
    gap = next(g for g in pre.gaps
               if g.code == "PREFLIGHT_DENSITY_NEEDS_DENOMINATOR")
    actions = {a.action for a in gap.alternatives}
    assert actions == {"provide_denominator", "switch_to_count"}
    # 面单元在场（行政边界）→ 面积分母可派生，密度成立（科学判定：
    # 每平方公里密度 = 数量 / 面单元面积，不强制要人口数据）。
    records2 = [
        _rec("ref:cd-schools", "成都小学", _schools("ref:cd-schools", bbox=CD_BBOX)),
        _rec("ref:cd-bounds", "成都区界", _bounds("ref:cd-bounds", bbox=CD_BBOX)),
    ]
    report2 = bind_session_roles(_reqs("subject", "boundary"), records2)
    ev2 = role_qualification_evidence(report2)
    pre2 = preflight_multi_dataset(ev2, analysis_kind="density")
    assert not any(g.code == "PREFLIGHT_DENSITY_NEEDS_DENOMINATOR"
                   for g in pre2.gaps)


def test_s3b_density_with_population_denominator():
    records = [
        _rec("ref:cd-schools", "成都小学", _schools("ref:cd-schools", bbox=CD_BBOX)),
        _rec("ref:cd-pop", "成都人口", _pop("ref:cd-pop", bbox=CD_BBOX)),
    ]
    report = bind_session_roles(
        _reqs("subject", "denominator"), records)
    by = report.binding_by_role()
    assert by["denominator"].ref_id == "ref:cd-pop"
    assert "denominator_field" in by["denominator"].matched_on
    ev = role_qualification_evidence(report)
    pre = preflight_multi_dataset(ev, analysis_kind="density")
    assert not any(g.code == "PREFLIGHT_DENSITY_NEEDS_DENOMINATOR"
                   for g in pre.gaps)
    # 资格：denominator 用人口数据自己的事实（分母字段强证据）。
    quals = qualify_workflow_data_roles(
        _reqs("subject", "denominator"), _res("subject", "denominator"),
        role_evidence=ev)
    assert quals[1].state == "eligible"


# ── S4 双期变化：不重叠 → TEMPORAL_DISJOINT；同期 → 过 ─────────────────

def test_s4_temporal_change_chain():
    y2020 = _schools("ref:cd-2020", coverage=("2020-01", "2020-12"),
                     bbox=CD_BBOX)
    y2025 = _schools("ref:cd-2025", coverage=("2025-01", "2025-12"),
                     bbox=CD_BBOX)
    records = [_rec("ref:cd-2020", "2020学校", y2020),
               _rec("ref:cd-2025", "2025学校", y2025)]
    report = bind_session_roles(_reqs("baseline", "target_time"), records)
    # 两个点数据集对 baseline 角色：证据并列（几何同分、时间同证据）→
    # ambiguous —— 双期场景必须显式指定（bound_refs），诚实不猜。
    b = report.binding_by_role()["baseline"]
    assert b.status == "ambiguous"
    # 显式绑定（计划行/用户点名）→ 双期各归其位。
    report2 = bind_session_roles(
        _reqs("baseline", "target_time"), records,
        bound_refs={"baseline": "ref:cd-2020",
                    "target_time": "ref:cd-2025"})
    by = report2.binding_by_role()
    assert by["baseline"].status == "bound"
    assert by["target_time"].ref_id == "ref:cd-2025"
    ev = role_qualification_evidence(report2)
    pre = preflight_multi_dataset(ev)
    gap = next(g for g in pre.gaps if g.code == "PREFLIGHT_TEMPORAL_DISJOINT")
    # preflight 输入键 = 角色标签（绑定审计面另有 ref 身份 + 指纹）。
    assert set(gap.refs) == {"baseline", "target_time"}
    assert gap.alternatives[0].action == "provide_matching_period"
    # 同期对照 → 无时间缺口。
    y2020b = _schools("ref:cd-2020b", coverage=("2020-06", "2020-12"),
                      bbox=CD_BBOX)
    pre2 = preflight_multi_dataset({
        "baseline": y2020, "target_time": y2020b})
    assert not any(g.code == "PREFLIGHT_TEMPORAL_DISJOINT" for g in pre2.gaps)


# ── S5 缺行政边界 → ROLE_MISSING typed gap ─────────────────────────────

def test_s5_missing_boundary_typed_gap():
    records = [_rec("ref:cd-schools", "成都小学",
                    _schools("ref:cd-schools", bbox=CD_BBOX))]
    report = bind_session_roles(_reqs("subject", "boundary"), records)
    by = report.binding_by_role()
    assert by["boundary"].status == "not_found"
    ev = role_qualification_evidence(report)  # 只供 subject
    pre = preflight_multi_dataset(ev, expected_roles=("subject", "boundary"))
    gap = next(g for g in pre.gaps if g.code == "PREFLIGHT_ROLE_MISSING")
    assert "boundary" in gap.detail
    assert gap.alternatives[0].action == "provide_role_data"
    assert "行政" in gap.alternatives[0].needs  # 需要什么数据（typed）
    # 资格面：boundary 角色 blocked/degraded（缺数据不得伪制图）。
    quals = qualify_workflow_data_roles(
        _reqs("subject", "boundary"), [
            DataRoleResolution(role="subject", required=True,
                               acquisition="local", status="bound"),
            DataRoleResolution(role="boundary", required=True,
                               acquisition="local", status="unresolved"),
        ], role_evidence=ev)
    assert quals[1].state == "blocked"


# ── S6 两个同名数据集 → ambiguous 候选清单 ─────────────────────────────

def test_s6_same_name_datasets_ambiguous():
    a = _rec("ref:cd-schools-a", "成都小学", _schools("ref:cd-schools-a"))
    b = _rec("ref:cd-schools-b", "成都小学", _schools("ref:cd-schools-b"))
    report = bind_session_roles(_reqs("subject"), [a, b])
    binding = report.binding_by_role()["subject"]
    assert binding.status == "ambiguous"
    cands = {c.ref_id for c in binding.candidates}
    assert cands == {"ref:cd-schools-a", "ref:cd-schools-b"}
    # 资格投影不供任何 descriptor（绝不拿其中一个顶替）。
    assert role_qualification_evidence(report) == {}
    # preflight 无输入 → unknown（不伪装 compatible）。
    pre = preflight_multi_dataset({})
    assert pre.verdict == "unknown"
    # 审计面候选带指纹（澄清回路可展示两个数据集的身份差异）。
    assert all(c.fingerprint.startswith("dsd-v1:") for c in binding.candidates)


# ── 全链资源纪律：零数据扫描 + O(refs) 语义读 ──────────────────────────

async def test_full_chain_zero_payload_scans(sem_store, monkeypatch):
    import app.services.session_data as session_data_mod
    from app.services.session_data import MemorySessionStore

    manager = MemorySessionStore()
    monkeypatch.setattr(session_data_mod, "session_data_manager", manager)
    sid = "g01-s-chain"
    refs = []
    for name, builder in (("schools", _schools), ("bounds", _bounds),
                          ("pop", _pop)):
        ref = await manager.store(sid, {"seed": name}, prefix=f"cd-{name}")
        await sem_store.put(sid, ref, builder(ref, bbox=CD_BBOX))
        refs.append(ref)

    payload_reads = {"n": 0}
    orig_get = manager.get

    async def _counting_get(*a, **k):
        payload_reads["n"] += 1
        return await orig_get(*a, **k)

    monkeypatch.setattr(manager, "get", _counting_get)
    store_reads = {"n": 0}
    orig_sem_get = DatasetSemanticStore.get

    async def _counting_sem_get(*a, **k):
        store_reads["n"] += 1
        return await orig_sem_get(*a, **k)

    monkeypatch.setattr(DatasetSemanticStore, "get", _counting_sem_get)

    evidence = await collect_session_evidence(sid)
    report = bind_session_roles(
        _reqs("subject", "boundary", "denominator"), evidence)
    ev = role_qualification_evidence(report)
    preflight_multi_dataset(ev, expected_roles=("subject", "boundary",
                                                "denominator"),
                            analysis_kind="density")
    qualify_workflow_data_roles(
        _reqs("subject", "boundary", "denominator"),
        _res("subject", "boundary", "denominator"), role_evidence=ev)

    assert payload_reads["n"] == 0, "全链零数据载荷读（metadata-only）"
    assert store_reads["n"] == len(refs), "语义读 = O(refs)，无扫描"


def test_binding_chain_latency_budget():
    """性能参照：绑定+preflight 全链（3 数据集）单次耗时实测记录。

    不是硬性 CI 预算（防环境抖动），而是与 master 对比的量化披露：
    master 无此链路（首个命中 O(refs) 单读）；本链同为 O(refs) 读 +
    O(roles×refs) 纯函数评分。断言宽松上界防退化数量级。
    """
    records = [
        _rec("ref:cd-schools", "成都小学", _schools("ref:cd-schools", bbox=CD_BBOX)),
        _rec("ref:cd-bounds", "成都区界", _bounds("ref:cd-bounds", bbox=CD_BBOX)),
        _rec("ref:cd-pop", "成都人口", _pop("ref:cd-pop", bbox=CD_BBOX)),
    ]
    reqs = _reqs("subject", "boundary", "denominator")

    durations = []
    for _ in range(50):
        t0 = time.perf_counter()
        report = bind_session_roles(reqs, records)
        ev = role_qualification_evidence(report)
        preflight_multi_dataset(ev, expected_roles=tuple(
            r.role for r in reqs), analysis_kind="density")
        durations.append((time.perf_counter() - t0) * 1000)
    durations.sort()
    p95 = durations[int(len(durations) * 0.95)]
    # 宽松数量级上界（纯函数 + 3 数据集；超 50ms 即数量级退化）。
    assert p95 < 50.0, f"p95={p95:.2f}ms 超退化上界"
