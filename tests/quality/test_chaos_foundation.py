"""Chaos 框架自身红线（ADR-0104 Wave 7+8）。

锁定 tests/fixtures/chaos.py 的三条平台契约：

1. **注册表完整性**：稳定 ID、``<SUBSYS>_<FAULT>`` 命名、每个故障点
   有攻击面/期望行为的文档与 file:line 审计证据；
2. **生产关闭是结构性的**：模块只活在 tests/ 下，app/ 全量源码零引用；
3. **journal 语义**：armed → fired → disarmed 事件序，``fired`` 只认
   实际注入动作（拿 armed 冒充故障 = 假红变假绿的温床，绝不放行）；
4. **注册表文档字节一致**：CHAOS_FAULT_REGISTRY.md 是
   ``fault_catalog()`` 的派生物（同 trace 认证表纪律）；
5. **无死故障**：每个注册 ID 至少被一个测试真实使用。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from gen_chaos_registry import DEFAULT_OUT, generate  # noqa: E402

from tests.fixtures.chaos import (  # noqa: E402
    FAULTS,
    FaultSpec,
    chaos,
    fault_catalog,
    journal_snapshot,
    reset_journal,
)

CHAOS_MODULE = REPO / "tests" / "fixtures" / "chaos.py"

_FAULT_ID_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")
_SUBSYSTEMS = {"CACHE", "LOCK", "REGISTRY", "INGEST", "CANCEL", "LLM"}


@pytest.fixture(autouse=True)
def _clean_journal():
    reset_journal()
    yield
    reset_journal()


# ── 注册表完整性 ──────────────────────────────────────────────────────────


def test_fault_ids_stable_and_well_named():
    for fid, spec in FAULTS.items():
        assert _FAULT_ID_RE.match(fid), f"{fid}: 不符合 <SUBSYS>_<FAULT> 命名"
        assert spec.subsystem in _SUBSYSTEMS, f"{fid}: 未知 subsystem"
        assert fid.startswith(spec.subsystem + "_"), f"{fid}: ID 与 subsystem 前缀不一致"
    assert len(FAULTS) == len(fault_catalog()), "fault_catalog 与 FAULTS 漂移"


@pytest.mark.parametrize("spec", list(FAULTS.values()), ids=lambda s: s.fault_id)
def test_every_fault_documented_with_audit_evidence(spec: FaultSpec):
    assert spec.description.strip(), f"{spec.fault_id}: 缺描述"
    assert spec.attack.strip(), f"{spec.fault_id}: 缺注入方式"
    assert spec.expected.strip(), f"{spec.fault_id}: 缺期望系统行为"
    # file:line 证据必须可定位（审计纪律：线索要能回到代码）
    assert re.search(r"\.py:\d+", spec.injection_point), (
        f"{spec.fault_id}: injection_point 缺 file:line 证据"
    )


def test_unregistered_id_fails_fast():
    with pytest.raises(KeyError):
        chaos("NOT_A_REAL_FAULT")


def test_every_registered_fault_is_exercised_by_a_test():
    """无死故障：每个注册 ID 至少被一个测试文件真实引用。"""
    test_sources = "\n".join(
        p.read_text(encoding="utf-8", errors="replace")
        for p in (REPO / "tests").rglob("test_*.py")
    )
    missing = sorted(
        fid
        for fid in FAULTS
        if f'"{fid}"' not in test_sources and f"'{fid}'" not in test_sources
    )
    assert not missing, f"注册了但无任何测试使用（死故障）: {missing}"


def test_fault_catalog_is_plain_data():
    catalog = fault_catalog()
    assert catalog and all(isinstance(item, dict) for item in catalog)
    for item in catalog:
        assert set(item) == {
            "fault_id",
            "subsystem",
            "description",
            "attack",
            "expected",
            "injection_point",
        }, f"{item.get('fault_id')}: 目录投影混入非数据字段（如工厂对象）"


# ── 生产关闭：结构性而非开关式 ────────────────────────────────────────────


def test_chaos_module_lives_under_tests_only():
    assert CHAOS_MODULE.exists()
    assert CHAOS_MODULE.resolve().is_relative_to((REPO / "tests").resolve())


def test_no_app_source_references_chaos_module():
    """app/ 任何 .py 都不许 import tests.fixtures.chaos（结构性生产关闭）。"""
    offenders = []
    for path in (REPO / "app").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "fixtures.chaos" in text or re.search(
            r"from\s+tests\.fixtures\s+import\s+.*chaos", text
        ):
            offenders.append(str(path.relative_to(REPO)))
    assert not offenders, f"生产代码引用了测试故障注入框架: {offenders}"


# ── journal 语义 ──────────────────────────────────────────────────────────


def test_journal_records_armed_fired_disarmed_order():
    with chaos("CACHE_CAP_SHRINK", max_bytes=1) as fault:
        assert fault.fired
    actions = [e.action for e in fault.events]
    assert actions == ["armed", "fired", "disarmed"], actions
    snapshot = [e for e in journal_snapshot() if e.fault_id == "CACHE_CAP_SHRINK"]
    assert [e.action for e in snapshot] == ["armed", "fired", "disarmed"]


def test_unfired_fault_is_detectable():
    """从未命中注入点的故障不得谎报 fired（CACHE_META_CORRUPT 需要 key 参数）。"""
    with pytest.raises(ValueError, match="key"):
        with chaos("CACHE_META_CORRUPT"):
            pass
    handle_events = [e for e in journal_snapshot() if e.fault_id == "CACHE_META_CORRUPT"]
    assert {e.action for e in handle_events} == {"armed"}  # armed 了但没开火


# ── 注册表文档：派生物字节一致 ────────────────────────────────────────────


def test_chaos_fault_registry_document_current_and_complete():
    content = generate()
    assert DEFAULT_OUT.exists(), (
        "CHAOS_FAULT_REGISTRY.md 未生成：python scripts/gen_chaos_registry.py"
    )
    assert DEFAULT_OUT.read_text(encoding="utf-8") == content
    # 诚实性：全部 fault ID 与审计证据都出现在文档里
    for fid, spec in FAULTS.items():
        assert fid in content, f"{fid} 未出现在注册表文档"
        assert spec.injection_point in content
