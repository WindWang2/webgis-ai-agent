"""G06 CAPABILITY ECOSYSTEM — Oracle 红灯测试（M1「变更前失败测试」）。

覆盖 Oracle：CAP-O01 / O02 / O03 / O04 / O05 / O06 / O07 / O11 / O12 /
O13 / O14。原生原语 dispatch（CAP-O06 的 dispatch 段、O08、O09）与扩展包
全链（CAP-O10）见 tests/unit/tools/test_capability_ecosystem_primitives.py。

纪律
----
* 严格按 ``planning/G06-CAPABILITY-ECOSYSTEM/INTERFACE_CONTRACTS.md`` 定稿的
  模块名 / 函数签名 import 与断言，不自行发明接口名；既有权威源只读消费
  （ToolRegistry / CapabilityRegistry / AlgorithmRegistry / 八面 conformance /
  pi_native_surface / slim_tool_result / PARAM_UNIT_VOCABULARY）。
* 新实现模块由主 Agent 并行编写，M1 阶段尚不存在。模块顶部把它们的 import
  集中在**单个** ``try/except ModuleNotFoundError`` 里并置 ``MISSING=True``；
  每个用例开头 ``assert not MISSING`` —— 失败信息明确（AssertionError 而非
  collection error），实现落地后 import 立即生效（真实 import，无 stub、
  无 importorskip、无 skip）。
* 只用 M0 已存在的真实 API；绝不构造函数里写假的可用性或替实现打桩。
"""
from __future__ import annotations

import importlib
import inspect
import json
import time
from pathlib import Path
from typing import Any

import pytest

# ── 既有权威源（M0 present；正常 import）──────────────────────────────
from app.tools.registry import ToolRegistry
from app.tools import init_tools
from app.tools.descriptor import ToolStatus
from app.lib.gis.capability_registry import get_capability_registry
from app.lib.gis.algorithm_registry import get_algorithm_registry
from app.lib.gis.tool_capability_conformance import (
    audit_tool_conformance,
    collect_live_facts,
)
from app.lib.gis.science_final_gate import NON_EQUIVALENT_SEMANTICS
from app.services.chat.pi_native_surface import (
    EXECUTE_PROXY_NAME,
    resolve_pi_tool_call,
)
from app.services.llm_result_formatter import slim_tool_result
from app.extensions_platform.host import ExtensionHost, HostPolicy

# ── 新实现模块（主 Agent 待写）：集中在单个 try/except ─────────────────
MISSING = False
try:
    # 用 import_module 而非 ``from app.lib.gis import capability_availability``
    # ——后者在父包存在、子模块缺失时抛 ImportError（非 ModuleNotFoundError），
    # 会绕过 except 变成 collection error；import_module 明确抛
    # ModuleNotFoundError，保证 M1 红灯是「每个用例 assert not MISSING」。
    ca_module = importlib.import_module(
        "app.lib.gis.capability_availability")
    from app.lib.gis.capability_availability import (
        AVAILABILITY_SCHEMA_VERSION,
        AVAILABILITY_STATUS_VOCABULARY,
        compile_availability_snapshot,
        query_availability,
    )
    from app.lib.gis.method_eligibility import (
        ELIGIBILITY_VOCABULARY,
        FALLBACK_CLASSES,
        preflight_method,
    )
    from app.lib.gis.availability_rerank import (
        rerank_candidates,
        validate_pi_call,
    )
    from app.lib.gis.provider_eligibility import (
        extension_lifecycle_view,
        qualify_provider,
    )
    from app.lib.gis.capability_schema_parity import parity_report
except ModuleNotFoundError:
    MISSING = True


RED = "实现模块尚未落地（M1 红灯证据）"
REPO_ROOT = Path(__file__).resolve().parents[3]
LEXICON_PATH = REPO_ROOT / "app/lib/data/capability_lexicon_golden.json"

# CAP-O05：语料之外按契约补的 2 条用例（写进测试常量，不改 JSON）
SUPPLEMENTAL_CASES: tuple[dict[str, Any], ...] = (
    {
        "query": "缓冲区分析 / buffer zone（中英混合术语）",
        "expect_tool": "buffer_analysis",
        "expect_status": None,
        "forbid_tools": (),
        "note": "补充正例：中英『缓冲 / buffer』应投影到真实缓冲工具，"
                "不得命中 declared_only 同名声明",
    },
    {
        "query": "路网出行时间 / 秒 / travel time seconds 求服务距离",
        "expect_tool": None,
        "expect_status": None,
        "forbid_tools": ("buffer_analysis",),
        "note": "补充负例：『秒 ≠ 米』，路网时间语境不得把米制距离/缓冲"
                "工具抬为首选（CAP-O06 量纲冲突的检索面回归）",
    },
)

# forbidden 记录不得泄露的 descriptor 细节（契约 A AvailabilityRecord 字段）
_DETAIL_KEYS = (
    "input_artifacts", "output_artifacts", "fields", "units",
    "crs_semantics", "geometry_requirements", "evidence_sources",
    "scientific_status", "resource_class", "side_effect", "determinism",
)


def _registry() -> ToolRegistry:
    """真实 lifespan 注册面（BASELINE §1 同款）：ToolRegistry() + init_tools。"""
    reg = ToolRegistry()
    init_tools(reg)
    return reg


def _tool_name(reg: ToolRegistry, rec: Any) -> str:
    """把 AvailabilityRecord.tool_id 解析成可直接喂给 audit_tool_conformance /
    registry.descriptor 的注册名，绝不凭空造名。

    契约 A/CAP-O02 把 tool_id 当工具名使用；真实 ``ToolDescriptor.tool_id``
    是 ``name@version#cvN``（app/tools/descriptor.py:232-234）。为对拍真实
    registry，这里做兼容解析：原名优先，其次去 @/#，最后按 descriptor 反查。
    """
    tid = str(getattr(rec, "tool_id", "") or "")
    names = reg.tool_names()
    if tid in names:
        return tid
    head = tid.split("@", 1)[0].split("#", 1)[0]
    if head in names:
        return head
    for nm, desc in reg.descriptors().items():
        if getattr(desc, "tool_id", None) == tid:
            return nm
    return tid


def _as_dict(obj: Any) -> dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    return dict(vars(obj))


def _numeric_values(obj: Any, _depth: int = 0) -> list[float]:
    """递归收集嵌套结构中的数值（用于封套数值 oracle 的宽松匹配）。"""
    out: list[float] = []
    if _depth > 12:
        return out
    if isinstance(obj, bool):
        return out
    if isinstance(obj, (int, float)):
        return [float(obj)]
    if isinstance(obj, dict):
        for v in obj.values():
            out.extend(_numeric_values(v, _depth + 1))
    elif isinstance(obj, (list, tuple, set)):
        for v in obj:
            out.extend(_numeric_values(v, _depth + 1))
    return out


def _module_source(mod: Any) -> str:
    try:
        return inspect.getsource(mod)
    except (OSError, TypeError):
        f = getattr(mod, "__file__", None)
        return Path(f).read_text(encoding="utf-8") if f else ""


# ── CAP-O01 单一事实源 ────────────────────────────────────────────────


def test_cap_o01_no_second_registry_class_in_availability_module():
    """capability_availability 只投影既有权威源，不得自建 registry 类。"""
    assert not MISSING, RED
    defined_here = [
        obj for _, obj in inspect.getmembers(ca_module, inspect.isclass)
        if getattr(obj, "__module__", None) == ca_module.__name__
    ]
    registry_like = [c.__name__ for c in defined_here if "Registry" in c.__name__]
    assert registry_like == [], (
        f"capability_availability 自建了 registry 类：{registry_like}（违反唯一事实源）")
    src = _module_source(ca_module)
    for banned in ("class CapabilityRegistry", "class AlgorithmRegistry",
                   "class ToolRegistry", "class ExecutionCatalog"):
        assert banned not in src, f"不得自建 {banned}"
    # 可信状态词表来自契约 A，不另造一套
    assert set(AVAILABILITY_STATUS_VOCABULARY) == {
        "native_verified", "available_unverified", "declared_only",
        "temporarily_unavailable", "forbidden",
    }
    assert AVAILABILITY_SCHEMA_VERSION == "capability-availability/v1"


def test_cap_o01_record_ids_all_come_from_real_registries():
    """records 的 tool/capability/algorithm id 必须能在真实 registry 找到。"""
    assert not MISSING, RED
    reg = _registry()
    cap_ids = set(get_capability_registry().all_ids)
    algo_ids = set(get_algorithm_registry().all_ids)
    snap = compile_availability_snapshot(bootstrap=True)
    assert snap.records, "快照不应为空（BASELINE §4：397 个工具应投影出真实分层）"

    # tool_id 必须来自真实 ToolRegistry：注册名或 descriptor.tool_id 复合名
    valid_tool_ids = set(reg.tool_names()) | {
        d.tool_id for d in reg.descriptors().values()
    }
    for rec in snap.records:
        tid = str(getattr(rec, "tool_id", "") or "")
        if getattr(rec, "availability_status", "") == "declared_only" and not tid:
            continue  # 契约：declared_only 项 tool_id 可为空
        assert tid in valid_tool_ids, (
            f"record tool_id {tid!r} 不在真实 ToolRegistry 中（非单一事实源）")
        cap = str(getattr(rec, "capability_id", "") or "")
        if cap:
            assert cap in cap_ids, f"capability_id {cap!r} 不在 CapabilityRegistry.all_ids"
        algo = str(getattr(rec, "algorithm_id", "") or "")
        if algo:
            assert algo in algo_ids, f"algorithm_id {algo!r} 不在 AlgorithmRegistry.all_ids"

    # 版本门：to_dict 输出 schema_version 且与常量一致
    assert snap.to_dict().get("schema_version") == AVAILABILITY_SCHEMA_VERSION


# ── CAP-O02 可用性真实 ────────────────────────────────────────────────


def test_cap_o02_native_verified_requires_conformance_pass():
    """native_verified 必须 8 面 conformance overall=pass 且 descriptor 可执行。"""
    assert not MISSING, RED
    reg = _registry()
    facts = collect_live_facts(reg)
    snap = compile_availability_snapshot(registry=reg)
    verified = [r for r in snap.records
                if getattr(r, "availability_status", "") == "native_verified"]
    assert verified, "BASELINE §4：213 个 pass 工具应投影出 native_verified"
    for rec in verified:
        name = _tool_name(reg, rec)
        report = audit_tool_conformance(name, facts)
        assert report.overall == "pass", (
            f"{name}: native_verified 但 conformance overall={report.overall}")
        desc = reg.descriptor(name)
        assert desc.status not in (
            ToolStatus.PLANNED, ToolStatus.EXTERNAL_UNAVAILABLE), (
            f"{name}: native_verified 但 descriptor.status={desc.status}")
        assert desc.executable, f"{name}: descriptor 不可执行却标 native_verified"
        assert not getattr(rec, "unverified_reasons", ()), (
            f"{name}: native_verified 不得带未验证原因")


def test_cap_o02_external_unavailable_downgrades_verified(monkeypatch):
    """monkeypatch 某工具 descriptor 成 EXTERNAL_UNAVAILABLE 后不得再 native_verified。"""
    assert not MISSING, RED
    reg = _registry()
    snap0 = compile_availability_snapshot(registry=reg)
    verified = [r for r in snap0.records
                if getattr(r, "availability_status", "") == "native_verified"]
    assert verified, "需要先有一个 native_verified 工具作为对拍基线"
    target = _tool_name(reg, verified[0])

    # descriptor(name) 从 _metadata 构造并缓存（registry.py:1001-1095）：
    # 改 status + 清缓存 = 真实 descriptor 投影被改成 EXTERNAL_UNAVAILABLE。
    monkeypatch.setitem(
        reg._metadata[target], "status",
        ToolStatus.EXTERNAL_UNAVAILABLE.value)
    reg._descriptor_cache.pop(target, None)
    assert reg.descriptor(target).status is ToolStatus.EXTERNAL_UNAVAILABLE

    snap1 = compile_availability_snapshot(registry=reg)
    still = [r for r in snap1.records
             if _tool_name(reg, r) == target
             and getattr(r, "availability_status", "") == "native_verified"]
    assert still == [], (
        f"{target} 已 EXTERNAL_UNAVAILABLE，不得再是 native_verified")


# ── CAP-O03 registry 漂移 ─────────────────────────────────────────────


def test_cap_o03_content_fingerprint_is_stable():
    """连续两次编译的 content_fingerprint / registry_generation 稳定。"""
    assert not MISSING, RED
    a = compile_availability_snapshot(bootstrap=True)
    b = compile_availability_snapshot(bootstrap=True)
    assert a.content_fingerprint == b.content_fingerprint
    assert a.registry_generation == b.registry_generation
    assert a.catalog_generation == b.catalog_generation
    assert a.to_dict()["schema_version"] == b.to_dict()["schema_version"]
    assert len(a.content_fingerprint) <= 128, "指纹应为短摘要，不得膨胀"


def test_cap_o03_registry_drift_changes_generation_and_visible_candidates():
    """register 一个临时工具改变 schema 后，代 / 指纹 / 可见候选按代变化。"""
    assert not MISSING, RED
    reg = _registry()
    snap0 = compile_availability_snapshot(registry=reg)
    names0 = set(reg.tool_names())

    def cap_o03_drift_probe(marker: str = "x") -> dict:
        return {"ok": True, "marker": marker}

    reg.register("cap_o03_drift_probe", "G06 CAP-O03 drift probe",
                 cap_o03_drift_probe)
    names1 = set(reg.tool_names())
    assert "cap_o03_drift_probe" in names1 and names1 != names0

    snap1 = compile_availability_snapshot(registry=reg)
    assert (snap1.registry_generation != snap0.registry_generation
            or snap1.content_fingerprint != snap0.content_fingerprint
            or snap1.counts != snap0.counts), (
        "registry 漂移必须反映到快照代 / 内容指纹 / counts")
    ids1 = {str(getattr(r, "tool_id", "")) for r in snap1.records}
    assert any("cap_o03_drift_probe" in i for i in ids1) or \
        snap1.counts != snap0.counts, (
        "新注册工具应改变快照可见候选")


# ── CAP-O04 权限隔离 ──────────────────────────────────────────────────


def test_cap_o04_permission_isolation_fail_closed():
    """高权限 / 跨 owner 记录 forbidden 且不泄露 descriptor 细节；空权限 fail-closed。"""
    assert not MISSING, RED
    snap = compile_availability_snapshot(bootstrap=True)
    scoped = query_availability(
        snap, owner="alice", permissions=frozenset({"read"}))
    for raw in scoped["records"]:
        d = _as_dict(raw)
        if d.get("availability_status") == "forbidden":
            for k in _DETAIL_KEYS:
                assert not d.get(k), f"forbidden 记录泄露 descriptor 细节 {k!r}"
        else:
            # 非 forbidden 的可用记录不得属于其他 owner，也不得要求更高权限
            assert str(d.get("ownership", "") or "") in ("", "alice")
            assert str(d.get("permission", "") or "") in ("", "read")

    # 权限收缩不得放大可见面
    full = query_availability(
        snap, owner="alice",
        permissions=frozenset({"read", "write", "admin", "tier3_confirm"}))
    assert scoped["returned"] <= full["returned"]

    # 空 permissions：fail-closed，不返回任何可用明细
    closed = query_availability(snap, owner="alice", permissions=frozenset())
    closed_dicts = [_as_dict(x) for x in closed["records"]]
    assert closed["returned"] == 0 or all(
        d.get("availability_status") == "forbidden" for d in closed_dicts), (
        "空 permissions 必须 fail-closed，不得返回可用明细")
    for d in closed_dicts:
        if d.get("availability_status") == "forbidden":
            for k in _DETAIL_KEYS:
                assert not d.get(k), f"空权限 forbidden 记录泄露 {k!r}"


def test_cap_o04_extension_lifecycle_view_fail_closed():
    """扩展生命周期只读视图：宿主缺席时诚实 not_evaluated，不虚构扩展状态。"""
    assert not MISSING, RED
    empty = extension_lifecycle_view(None)
    assert isinstance(empty, dict)
    assert "not_evaluated" in json.dumps(empty, default=str).lower()

    host = ExtensionHost(_registry(), HostPolicy())
    view = extension_lifecycle_view(host)
    assert view["schema_version"] == "extension-lifecycle-view/v1"
    assert "version_pins" not in view or isinstance(view["version_pins"], dict)
    # 公开 API 视图不得暴露私有 worker 句柄 / 凭据。
    blob = json.dumps(view, default=str).lower()
    assert "worker_pid" not in blob and "secret" not in blob


# ── CAP-O05 中英术语选择 ──────────────────────────────────────────────


def _run_lexicon_case(snap: Any, case: dict[str, Any], *,
                      soft_forbid: bool = False) -> None:
    reg = _registry()
    names = reg.tool_names()
    expect = case.get("expect_tool")
    candidates = [(n, 1.0) for n in names]
    if expect and expect not in names:
        candidates.append((expect, 1.0))
    result = rerank_candidates(
        candidates, snap, query=case["query"],
        max_candidates=max(8, len(names)))
    top = [c.tool for c in result.candidates]
    suppressed = [t for t, _ in result.suppressed]

    if expect:
        # 契约：expect_tool 必须保留在候选内；不再写 `top and top[0]==expect
        # or expect in top` 这类恒真断言（F-13 独立冷审）。
        assert expect in top, (
            f"query={case['query']!r} 未把期望工具 {expect!r} 保留在候选内 "
            f"(suppressed={suppressed[:8]})")
        # F-14：expect_status 不再是死字段，必须与真实快照逐例对拍。
        declared_status = case.get("expect_status")
        if declared_status:
            from app.lib.gis.capability_availability import (
                compile_availability_snapshot,
            )

            live = compile_availability_snapshot(registry=reg).for_tool(expect)
            assert live is not None, f"{expect} 不在真实可用性快照中"
            assert live.availability_status == declared_status, (
                f"{expect}: 语料声明 {declared_status} != 实际 "
                f"{live.availability_status}（不得伪造可用性）")
    for f in (case.get("forbid_tools") or ()):
        if soft_forbid:
            assert f in suppressed or f not in top[:1], (
                f"query={case['query']!r} 禁止工具 {f!r} 被抬为首选")
        else:
            assert f in suppressed or f not in top, (
                f"query={case['query']!r} 禁止工具 {f!r} 未被抑制且进入候选")


def test_cap_o05_golden_lexicon_cases():
    """读黄金语料 cases，逐条断言中英正例 / forbid_tools。"""
    assert not MISSING, RED
    data = json.loads(LEXICON_PATH.read_text(encoding="utf-8"))
    cases = list(data["cases"])
    assert cases, "黄金语料 cases 不应为空"
    snap = compile_availability_snapshot(bootstrap=True)
    for case in cases:
        _run_lexicon_case(snap, case)


def test_cap_o05_supplemental_cases():
    """语料之外按契约自补的 2 条中英用例（测试常量，不改 JSON）。"""
    assert not MISSING, RED
    assert len(SUPPLEMENTAL_CASES) == 2
    snap = compile_availability_snapshot(bootstrap=True)
    for case in SUPPLEMENTAL_CASES:
        _run_lexicon_case(snap, case, soft_forbid=True)


def test_cap_o05_seconds_never_hits_metric_distance_tools():
    """负例：路网时间/秒 语境不得命中米制距离工具（即使给高分）。"""
    assert not MISSING, RED
    reg = _registry()
    metric = [n for n in reg.tool_names()
              if any(k in n for k in ("distance", "buffer", "euclid"))]
    assert metric, "需要真实米制距离类工具作为负例对拍"
    candidates = [(n, 5.0) for n in metric]
    snap = compile_availability_snapshot(registry=reg)
    result = rerank_candidates(
        candidates, snap,
        query="路网出行时间 / 秒 / travel time seconds 计算服务距离",
        method_constraints=("seconds",),
        max_candidates=max(8, len(metric)))
    top = [c.tool for c in result.candidates]
    blob = " ".join(str(x) for x in (
        result.diagnostics, result.suppressed, top)).upper()
    assert ("UNIT_DIMENSION_CONFLICT" in blob
            or "GEOGRAPHIC_LINEAR_DISTANCE" in blob
            or not any("distance" in t for t in top[:1])), (
        "『路网时间/秒』不得把米制距离工具抬为首选，且须给出量纲冲突证据")


# ── CAP-O06 参数 / CRS / 量纲 ─────────────────────────────────────────


def _preflight(algorithm_id: str, *, space: dict | None = None,
               params: dict | None = None) -> tuple[Any, str]:
    res = preflight_method(algorithm_id, space=space or {},
                           params=params or {}, allow_transform=False)
    reasons = " ".join(str(x) for x in getattr(res, "reason_codes", ()))
    return res, reasons


def test_cap_o06_unit_dimension_conflict_seconds_vs_meters():
    """秒与米绝不混合：任一混用组合都 ineligible + UNIT_DIMENSION_CONFLICT。"""
    assert not MISSING, RED
    algo_ids = list(get_algorithm_registry().all_ids)[:60]
    assert algo_ids
    calls = (
        # 任务口径：space.distance_unit=seconds + 米制距离操作（params 声明米）
        dict(space={"distance_unit": "seconds", "crs_class": "projected"},
             params={"distance_unit": "meters", "unit": "m"}),
        # 契约 B 口径：space.distance_unit ∈ {m,km} 与 seconds 混用
        dict(space={"distance_unit": "m", "crs_class": "projected"},
             params={"unit": "seconds"}),
    )
    hits = 0
    for aid in algo_ids:
        for kw in calls:
            res, reasons = _preflight(aid, **kw)
            assert res.verdict != "eligible", (
                f"{aid}: 秒/米混用不得判 eligible")
            assert res.verdict in ELIGIBILITY_VOCABULARY
            if "UNIT_DIMENSION_CONFLICT" in reasons:
                hits += 1
    assert hits > 0, (
        "秒与米混用必须产生 UNIT_DIMENSION_CONFLICT reason code")


def test_cap_o06_geographic_linear_distance_ineligible():
    """crs_class=geographic + 线性距离操作 → ineligible + GEOGRAPHIC_LINEAR_DISTANCE。"""
    assert not MISSING, RED
    algo_ids = list(get_algorithm_registry().all_ids)[:60]
    space = {"crs_class": "geographic", "distance_unit": "m"}
    params = {"unit": "meters", "requires_linear_distance": True}
    hits = 0
    for aid in algo_ids:
        res, reasons = _preflight(aid, space=space, params=params)
        assert res.verdict != "eligible", (
            f"{aid}: 经纬度 CRS 下线性距离不得判 eligible")
        if "GEOGRAPHIC_LINEAR_DISTANCE" in reasons:
            hits += 1
            if "REQUIRES_TRANSFORM" in reasons:
                assert str(getattr(res, "transform_hint", "") or ""), (
                    "REQUIRES_TRANSFORM 时应给出 transform_hint")
    assert hits > 0, (
        "geographic CRS + 线性距离必须产生 GEOGRAPHIC_LINEAR_DISTANCE")


# ── CAP-O07 fallback 诚实性 ───────────────────────────────────────────


def test_cap_o07_fallback_semantics_never_upgraded_to_equivalent():
    """proxy / approximation / degraded 绝不抬升为 equivalent。"""
    assert not MISSING, RED
    ar = get_algorithm_registry()
    buckets: dict[str, list[str]] = {
        "proxy": [], "approximation": [], "degraded": [],
    }
    for aid in ar.all_ids:
        algo = ar.get(aid)
        for _target, sem in dict(getattr(algo, "fallback_semantics", {}) or {}).items():
            val = str(getattr(sem, "value", sem)).lower()
            if val in buckets:
                buckets[val].append(aid)
    assert any(buckets.values()), (
        "AlgorithmRegistry 应含非等价 fallback_semantics 声明（BASELINE §3）")
    assert set(NON_EQUIVALENT_SEMANTICS) == {
        "approximation", "proxy", "degraded"}
    for cls in ("proxy", "approximation", "degraded"):
        for aid in dict.fromkeys(buckets[cls]):
            res = preflight_method(aid, space={}, params={})
            fcls = str(getattr(res, "fallback_class", ""))
            assert fcls != "equivalent", (
                f"{aid}: {cls} 语义被抬升为 equivalent（违反 CAP-O07）")
            assert fcls in FALLBACK_CLASSES, (
                f"{aid}: fallback_class {fcls!r} 不在契约词表")
            assert fcls in (cls, "not_allowed"), (
                f"{aid}: {cls} 语义被错标为 {fcls!r}（不得抬升/串类）")


# ── CAP-O11 Pi 工具投影 ───────────────────────────────────────────────


def test_cap_o11_pi_projection_and_rejects():
    """validate_pi_call 与 pi_native_surface.resolve_pi_tool_call 只读对拍。"""
    assert not MISSING, RED
    reg = _registry()
    names = reg.tool_names()
    snap = compile_availability_snapshot(registry=reg)

    # native 工具：Pi 原生语义；validate_pi_call 不得把它当 execute 代理
    ok_native, _ = validate_pi_call(
        "webgis_map_intent", {}, snapshot=snap, registry=reg)
    assert isinstance(ok_native, bool)
    native = resolve_pi_tool_call(
        "webgis_map_intent", {}, registered_surface=names)
    assert native.kind == "native"

    # registered tool 的 execute 归属对拍
    real = next(n for n in names if n != EXECUTE_PROXY_NAME)
    ok_real, _ = validate_pi_call(real, {}, snapshot=snap, registry=reg)
    assert isinstance(ok_real, bool)
    assert resolve_pi_tool_call(
        real, {}, registered_surface=names).kind == "execute"

    # hallucinated 工具名被拒绝
    ok_h, _ = validate_pi_call(
        "definitely_not_a_tool_xyz", {}, snapshot=snap, registry=reg)
    assert ok_h is False
    assert resolve_pi_tool_call(
        "definitely_not_a_tool_xyz", {},
        registered_surface=names).kind == "reject"

    # 多余字段被拒绝（pi-schema 宽松 coercion 不得放行）
    ok_extra, _ = validate_pi_call(
        "webgis_map_intent", {"query": "x", "unexpected_extra": 1},
        snapshot=snap, registry=reg)
    assert ok_extra is False

    # native 工具被包裹成 execute 时被拒绝（只读对拍，不修改 pi_native_surface）
    wrapped = resolve_pi_tool_call(
        EXECUTE_PROXY_NAME,
        {"toolName": "webgis_map_intent", "arguments": {}},
        registered_surface=names)
    assert wrapped.kind == "reject"
    assert "native" in wrapped.error.lower()


# ── CAP-O12 资源纪律 ──────────────────────────────────────────────────


def test_cap_o12_query_and_rerank_are_bounded():
    """limit / max_candidates 硬上界；长结果字符串不进 fingerprint 异常膨胀。"""
    assert not MISSING, RED
    snap = compile_availability_snapshot(bootstrap=True)

    q = query_availability(snap, limit=5)
    assert q["returned"] <= 5
    assert q["truncated"] is True
    assert q["total"] >= q["returned"]

    reg = _registry()
    candidates = [(n, 1.0) for n in reg.tool_names()[:200]]
    assert len(candidates) == 200
    result = rerank_candidates(candidates, snap, query="buffer",
                               max_candidates=8)
    assert len(result.candidates) <= 8
    assert result.bounded is True
    assert result.diagnostics or result.suppressed, (
        "bounded=True 时应披露截断")

    # fingerprint 是短摘要；长结果字符串不得让它膨胀
    assert len(snap.content_fingerprint) <= 128
    q2 = query_availability(snap, limit=5)
    assert len(q2["content_fingerprint"]) == len(snap.content_fingerprint)
    long_str = "x" * 100_000
    slim = slim_tool_result({"data": long_str}, long_str, None)
    assert isinstance(slim, str)
    assert len(slim) < len(long_str), (
        "长结果必须被压缩为有界摘要或 ref，不得原样透出")


@pytest.mark.timeout(10)
def test_cap_o12_slow_provider_returns_not_evaluated_within_budget(monkeypatch):
    """慢 provider 失效注入：注入的慢探测**必须被真实调用**，且资格有界降级。

    R1 独立冷审 F-04 修复：旧版 monkeypatch ``ExtensionHost.get_record``
    sleep(30) 从未被触发（qualify_provider 不调用它，且空权限提前返回），
    「慢 provider 已验证」属失真。这里改用 ``probe`` 显式注入点，断言
    probe 真实执行、遵守协作式 deadline、结果降级 not_evaluated。
    """
    assert not MISSING, RED
    host = ExtensionHost(_registry(), HostPolicy())
    calls: list[float] = []

    def _slow_but_cooperative_probe(provider_id, remaining_s):
        # 真实执行且真实慢（0.35s），但遵守传入的协作式预算：
        # 若预算小于实际耗时，主动抛 TimeoutError 而不是硬睡到底。
        calls.append(float(remaining_s) if remaining_s is not None else -1.0)
        sleep_for = 0.35
        if remaining_s is not None and sleep_for > remaining_s:
            time.sleep(min(remaining_s, 0.02))
            raise TimeoutError("provider probe exceeded cooperative deadline")
        time.sleep(sleep_for)

    started = time.monotonic()
    res = qualify_provider(
        "cap_o12_slow_provider", budget={"deadline_s": 0.05},
        permissions=frozenset({"read", "model_infer"}),
        host=host, probe=_slow_but_cooperative_probe)
    elapsed = time.monotonic() - started

    assert calls, "注入的慢探测必须被真实调用（否则该 Oracle 是空桩）"
    assert elapsed < 5.0, f"慢 provider 挂起 {elapsed:.2f}s（应有预算闸）"
    assert res.qualified is False, (
        "预算内不得虚构 qualified（fail-closed / not_evaluated）")
    assert all(v == "not_evaluated" or v == "failed"
               for k, v in res.facets.items() if k != "permission"), (
        "超时探测的资格面必须降级，不得保留 proven_pass")
    blob = " ".join(
        [str(x) for x in getattr(res, "reason_codes", ())]
        + [str(v) for v in dict(getattr(res, "facets", {}) or {}).values()]
    ).lower()
    assert ("not_evaluated" in blob or "budget" in blob
            or "timeout" in blob or "degraded" in blob), (
        "慢 provider 应显式给出 not_evaluated / budget 证据，不猜可用性")
    assert any("timeout" in str(c).lower() or "not_evaluated" in str(c).lower()
               for c in res.reason_codes), (
        "应带 PROBE_DEADLINE_TIMEOUT_NOT_EVALUATED 之类的显式理由码")

    # 另一路：空权限仍必须 fail-closed（不因注入探测而绕过）。
    closed = qualify_provider(
        "cap_o12_slow_provider", permissions=frozenset(),
        host=host, probe=_slow_but_cooperative_probe)
    assert closed.qualified is False
    assert "permission" in closed.facets
    assert closed.facets["permission"] == "failed"


# ── CAP-O13 文档可执行 ────────────────────────────────────────────────


def test_cap_o13_docs_and_examples_are_locally_executable():
    """两份 dev 文档 + examples/gis_capability_ecosystem 的 manifest 与 demo 脚本。"""
    assert not MISSING, RED
    doc1 = REPO_ROOT / "docs/dev/GIS_CAPABILITY_ECOSYSTEM.md"
    doc2 = REPO_ROOT / "docs/dev/GIS_TOOL_AUTHORING_CONTRACT.md"
    for p in (doc1, doc2):
        assert p.is_file(), f"缺少文档 {p}"
    combined = (doc1.read_text(encoding="utf-8") + "\n"
                + doc2.read_text(encoding="utf-8"))
    assert "pytest" in combined, "文档未含可本地运行的 pytest 命令"
    assert "python -m app.lib.gis.capability_availability" in combined, (
        "文档未含 CLI 命令 python -m app.lib.gis.capability_availability")

    ex = REPO_ROOT / "examples/gis_capability_ecosystem"
    assert ex.is_dir(), "缺少 examples/gis_capability_ecosystem/"
    assert list(ex.glob("*manifest*")), "examples 缺 manifest"
    scripts = sorted(ex.rglob("*.py"))
    assert len(scripts) >= 2, (
        f"examples 至少需要 2 个 demo 脚本，实际 {len(scripts)}")


# ── CAP-O14 回归保护 ──────────────────────────────────────────────────


def _parity_required(report: dict, name: str) -> set[str] | None:
    """从 parity_report 的（契约未细化的）dict 形状里稳健取 required 集合。"""
    candidate = None
    if isinstance(report, dict):
        candidate = report.get(name)
        tools = report.get("tools")
        if candidate is None and isinstance(tools, dict):
            candidate = tools.get(name)
        if candidate is None and isinstance(report.get("entries"), dict):
            candidate = report["entries"].get(name)
    if isinstance(candidate, dict):
        r = candidate.get("required") or candidate.get("required_params")
        if r is not None:
            return set(r)
    return None


def test_cap_o14_parity_regression_guard():
    """既有工具名/required 参数集四处一致；slim 摘要丢值须记只读诊断。"""
    assert not MISSING, RED
    reg = _registry()
    report = parity_report(bootstrap=True, registry=reg)
    assert isinstance(report, dict) and report, "parity_report 应返回非空 dict"

    legacy = ("zonal_stats", "buffer_analysis", "network_od_matrix",
              "idw_interpolation", "spatial_aggregate")
    for name in legacy:
        assert name in reg.tool_names(), f"{name} 名称未变（仍须真实注册）"
        model = reg.args_model(name)
        assert model is not None, f"{name} 缺少 args_model"
        required = {f for f, fi in model.model_fields.items() if fi.is_required()}
        r_required = _parity_required(report, name)
        if r_required is not None:
            assert r_required == required, (
                f"{name}: parity_report required {sorted(r_required)} != "
                f"args_model required {sorted(required)}")
        # F-13：不再用 `name in json.dumps(report)` 这种宽松包含，
        # 精确断言该工具在 tools 面里有条目。
        assert name in report["tools"], f"parity_report 未覆盖既有工具 {name}"
        assert report["tools"][name]["name"] == name

    # 只读诊断（不修改 llm_result_formatter.py，避免抢 Issue #2006）：
    # slim 摘要要么含关键数值，要么有 ref；否则 parity_report 必须记
    # SUMMARY_VALUE_LOSS 诊断。此处精确查 diagnostics，不用
    # json.dumps(report)（monitored_codes 恒含该码会让断言恒真）。
    payload = {"data": {"count": 42, "mean": 3.14},
               "artifact": {"ref": "artifact://cap-o14"}}
    summary = slim_tool_result(payload, json.dumps(payload), None)
    has_value = "42" in summary or "3.14" in summary
    has_ref = "ref" in summary.lower() or "artifact" in summary.lower()
    assert has_value or has_ref, (
        "代表性封套的 slim 摘要应保留关键数值或 ref")
    assert "SUMMARY_VALUE_LOSS" in report["monitored_codes"], (
        "SUMMARY_VALUE_LOSS 必须在受监控诊断码表内")
    if not (has_value or has_ref):
        assert "SUMMARY_VALUE_LOSS" in report["diagnostics"], (
            "slim 摘要丢失关键数值且无 ref，parity_report 必须记 SUMMARY_VALUE_LOSS")
