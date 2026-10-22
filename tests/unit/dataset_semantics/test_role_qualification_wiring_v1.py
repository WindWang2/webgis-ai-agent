"""G01/WP-B 生产接线测试：角色级 descriptor 事实进入资格裁决。

覆盖面：
- qualify_workflow_data_roles role_evidence：命中角色用**自己的**数据集
  事实（多数据 session 第二数据集可达），缺席角色回退全局（基线行为）；
- compile_workflow_v4 role_evidence 直通（base 资格阶段 + V4 角色状态
  重放消费同一份角色证据）；
- semantic_tools.compile_workflow_semantics 生产入口（registry dispatch）：
  session 多数据 → data_role_bindings 审计面 + role_grounded 基础披露；
  无 session / 单数据 / 证据面失败 = 原行为不变。
"""
import pytest

from app.lib.gis.dataset_profile import DatasetProfile
from app.services.dataset_semantics import derive_descriptor
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


def _point_descriptor(count=30, key="ref:data-schools"):
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


def _res(role, status="bound"):
    return DataRoleResolution(
        role=role, required=True, acquisition="local", status=status)


@pytest.fixture()
def sem_store(tmp_path, monkeypatch):
    import app.services.dataset_semantics.store as store_mod
    import app.services.session_data as session_data_mod
    from app.services.session_data import MemorySessionStore

    store = DatasetSemanticStore(base_dir=tmp_path)
    monkeypatch.setattr(store_mod, "_store", store)
    monkeypatch.setattr(
        session_data_mod, "session_data_manager", MemorySessionStore())
    yield store
    reset_dataset_semantic_store()


# ── 库面：qualify_workflow_data_roles 角色级事实 ────────────────────────

def test_role_evidence_gives_each_role_its_own_dataset_facts():
    schools = _point_descriptor()
    population = _population_descriptor()
    reqs = [
        DataRoleRequirement(role="subject", geometry_kinds=["point"]),
        DataRoleRequirement(role="denominator", geometry_kinds=["polygon"]),
    ]
    resolutions = [_res("subject"), _res("denominator")]

    # 基线（全局单 descriptor = 学校点数据）：denominator 用错数据集事实
    # → 几何不匹配 → degraded。
    baseline = qualify_workflow_data_roles(
        reqs, resolutions, descriptor=schools)
    by_role = {q.role: q for q in baseline}
    assert by_role["denominator"].state == "degraded"
    assert any(c.get("check") == "geometry_kind" and not c.get("passed")
               for c in by_role["denominator"].checks)

    # 角色级证据：denominator 用人口面数据自己的事实 → 几何匹配。
    grounded = qualify_workflow_data_roles(
        reqs, resolutions, descriptor=schools,
        role_evidence={"denominator": {
            "descriptor": population,
            "expected_descriptor_fingerprint": "",
        }})
    by_role2 = {q.role: q for q in grounded}
    assert by_role2["denominator"].state != "degraded" or all(
        c.get("passed") for c in by_role2["denominator"].checks
        if c.get("check") == "geometry_kind")
    geom = [c for c in by_role2["denominator"].checks
            if c.get("check") == "geometry_kind"]
    assert geom and geom[0].get("passed") is True


def test_role_evidence_stale_entry_degrades_with_descriptor_stale():
    schools = _point_descriptor()
    newer = _point_descriptor(count=99)
    assert schools.descriptor_fingerprint != newer.descriptor_fingerprint
    reqs = [DataRoleRequirement(role="subject", geometry_kinds=["point"])]
    quals = qualify_workflow_data_roles(
        reqs, [_res("subject")],
        role_evidence={"subject": {
            "descriptor": newer,
            "expected_descriptor_fingerprint": schools.descriptor_fingerprint,
        }})
    assert quals[0].state == "degraded"
    assert quals[0].reason_code == "DESCRIPTOR_FINGERPRINT_MISMATCH"


def test_role_evidence_absent_roles_fall_back_to_global():
    schools = _point_descriptor()
    population = _population_descriptor()
    reqs = [
        DataRoleRequirement(role="subject", geometry_kinds=["point"]),
        DataRoleRequirement(role="measure"),
    ]
    quals = qualify_workflow_data_roles(
        reqs, [_res("subject"), _res("measure")],
        descriptor=schools,
        role_evidence={"measure": {
            "descriptor": population,
            "expected_descriptor_fingerprint": "",
        }})
    by_role = {q.role: q for q in quals}
    # subject 无条目 → 全局 schools 事实（几何匹配 → 非 degraded）。
    geom = [c for c in by_role["subject"].checks
            if c.get("check") == "geometry_kind"]
    assert geom and geom[0].get("passed") is True


# ── 编译链：compile_workflow_v4 role_evidence 直通 ──────────────────────

def test_compile_workflow_v4_threads_role_evidence():
    from app.services.gis_harness.workflow_v4.compiler_v4 import (
        compile_workflow_v4,
    )

    # facility_coverage_ratio：subject/boundary/denominator 三角色 recipe
    # （review P1：必须选带 data_roles 的 recipe，否则资格阶段空转、
    # threading 断言恒真）。subject 是 local 获取角色（capability bound →
    # 资格事实评估真实可达；denominator 是 data_fabric 外部获取角色，
    # 规划期按设计 early-return unknown，不用它断言 threading）。
    kw = {"recipe_id": "facility_coverage_ratio"}
    baseline = compile_workflow_v4("成都各区的学校覆盖率", **kw)
    base_by_role = {
        q["role"]: q for q in baseline.base.data_qualifications}
    # 基线（无角色证据）：subject 无事实 → 不虚构资格。
    assert base_by_role["subject"]["state"] in ("unknown", "degraded")

    schools = _point_descriptor()
    grounded = compile_workflow_v4(
        "成都各区的学校覆盖率", role_evidence={"subject": {
            "descriptor": schools,
            "expected_descriptor_fingerprint": "",
        }}, **kw)
    grounded_by_role = {
        q["role"]: q for q in grounded.base.data_qualifications}
    # 角色证据生效：subject 用学校点数据自己的事实（几何 point 匹配的
    # 事实检查在场），与基线（无事实检查）显著不同。
    d = grounded_by_role["subject"]
    geom = [c for c in d["checks"] if c.get("check") == "geometry_kind"]
    assert geom and geom[0].get("passed") is True
    assert len(d["checks"]) > len(base_by_role["subject"]["checks"])


def test_compile_workflow_v4_default_unchanged_without_role_evidence():
    from app.services.gis_harness.workflow_v4.compiler_v4 import (
        compile_workflow_v4,
    )

    a = compile_workflow_v4("分析成都便利店的空间密度")
    b = compile_workflow_v4("分析成都便利店的空间密度")
    assert a.to_bounded_dict() == b.to_bounded_dict()


# ── 生产入口：semantic_tools.compile_workflow_semantics ────────────────

def _make_registry():
    from app.tools.registry import ToolRegistry
    from app.tools.semantic_tools import register_semantic_tools

    reg = ToolRegistry()
    register_semantic_tools(reg)
    return reg


async def test_semantic_tool_role_bindings_multidataset_session(sem_store):
    import app.services.session_data as session_data_mod

    reg = _make_registry()
    sid = "g01-w2-tool"
    manager = session_data_mod.session_data_manager
    ref_schools = await manager.store(sid, {"k": 1}, prefix="data-schools")
    ref_pop = await manager.store(sid, {"k": 2}, prefix="data-pop")
    d_schools = _point_descriptor(key=ref_schools)
    d_pop = _population_descriptor(key=ref_pop)
    await sem_store.put(sid, ref_schools, d_schools)
    await sem_store.put(sid, ref_pop, d_pop)

    # facility_coverage_ratio：subject/boundary/denominator 三角色 recipe
    # （多角色资格链的最小真实入口；recipe_id 显式指定避免路由漂移）。
    out = await reg.dispatch(
        "compile_workflow_semantics",
        {"query": "成都各区的学校覆盖率与公平性",
         "recipe_id": "facility_coverage_ratio"}, session_id=sid)
    assert out["success"] is True, out.get("error")
    bindings = out.get("data_role_bindings") or []
    assert bindings, "角色绑定审计面必须在场（多数据 session）"
    statuses = {b["role"]: b["status"] for b in bindings}
    # 至少一个角色绑定到具体数据集（非全 not_found）。
    assert any(s in ("bound", "partial", "ambiguous", "stale")
               for s in statuses.values())
    # 审计面有界且形状稳定。
    for b in bindings:
        assert set(b) >= {"role", "status", "reason_code"}
    assert out["qualification_basis"] in ("role_grounded", "profile_grounded")


async def test_semantic_tool_without_session_unchanged():
    reg = _make_registry()
    out = await reg.dispatch(
        "compile_workflow_semantics",
        {"query": "分析成都便利店的空间密度"})
    assert out["success"] is True
    assert out.get("data_role_bindings") == []
    assert out["qualification_basis"] == "profile_absent_neutral"


async def test_semantic_tool_single_dataset_session_binding(sem_store):
    import app.services.session_data as session_data_mod

    reg = _make_registry()
    sid = "g01-w2-single"
    manager = session_data_mod.session_data_manager
    ref = await manager.store(sid, {"k": 1}, prefix="data-schools")
    d = _point_descriptor(key=ref)
    await sem_store.put(sid, ref, d)
    out = await reg.dispatch(
        "compile_workflow_semantics",
        {"query": "分析成都便利店的空间密度"}, session_id=sid)
    assert out["success"] is True
    bindings = out.get("data_role_bindings") or []
    # 单数据 session：有绑定为 bound/partial，且不出现 ambiguous。
    assert all(b["status"] != "ambiguous" for b in bindings)


async def test_semantic_tool_stale_reachable_via_recorded_face(sem_store):
    """review P2-1：生产入口（无 bound_refs）stale 判据可达。

    durable 记录面留下指纹锚 → store 语义前进 → 绑定（证据推导路径）
    必须产出 stale + data_preflight 在场。
    """
    import app.services.session_data as session_data_mod

    reg = _make_registry()
    sid = "g01-w2-stale"
    manager = session_data_mod.session_data_manager
    ref = await manager.store(sid, {"k": 1}, prefix="data-schools")
    d1 = _point_descriptor(key=ref)
    await sem_store.put(sid, ref, d1)
    from app.services.gis_harness.durable_context import record_source_fingerprints

    await record_source_fingerprints(sid, [{"ref": ref, "fingerprint": "aa"}])
    d2 = _point_descriptor(count=77, key=ref)
    await sem_store.put(sid, ref, d2)

    out = await reg.dispatch(
        "compile_workflow_semantics",
        {"query": "成都各区的学校覆盖率与公平性",
         "recipe_id": "facility_coverage_ratio"}, session_id=sid)
    assert out["success"] is True, out.get("error")
    bindings = out.get("data_role_bindings") or []
    # subject 角色经证据推导绑定到该 ref（唯一点数据）→ 记录面锚过期 → stale。
    stales = [b for b in bindings if b["status"] == "stale"]
    assert stales, f"生产链 stale 必须可达：{[b['status'] for b in bindings]}"
    assert stales[0]["expected_descriptor_fingerprint"] == (
        d1.descriptor_fingerprint)
    # WP-C：preflight 审计面在场（有界形状）。
    pre = out.get("data_preflight") or {}
    assert pre.get("verdict") in ("compatible", "gaps", "unknown")
    assert isinstance(pre.get("gaps"), list)
