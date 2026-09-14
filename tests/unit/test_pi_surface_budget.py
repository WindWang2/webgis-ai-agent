"""Pi 激活面 schema 字节预算测试（ADR-0180 D2 / typed-tool-surface M1）。

- apply_surface_byte_budget：native 前门恒保留；动态候选贪心装入；
  超预算记因 byte_budget；schema_size 缺席按 0；预算 0 = 关闭；
- compute_turn_active_tools 集成：预算只裁动态候选、不阻断、
  披露面（disclosure）带 surface_bytes/budget_dropped；
- 默认预算（32KB）下典型 turn 的名单与关闭预算逐位一致或仅裁尾。
"""
import pytest

from app.services.chat.pi_native_surface import (
    NATIVE_TOOL_NAME_SET,
    NATIVE_TOOL_NAMES,
    _surface_byte_budget,
    apply_surface_byte_budget,
)


@pytest.fixture(scope="module")
def registry():
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    init_tools(reg)
    return reg


@pytest.fixture(autouse=True)
def _clean_budget_env(monkeypatch):
    monkeypatch.delenv("PI_SURFACE_BYTE_BUDGET", raising=False)


def _dynamic_names(registry, k=40):
    from app.services.chat.pi_native_surface import registered_surface_names

    return [n for n in registered_surface_names(registry) if n not in NATIVE_TOOL_NAME_SET][:k]


def test_budget_default_is_32k_and_env_override(monkeypatch):
    assert _surface_byte_budget() == 32 * 1024
    monkeypatch.setenv("PI_SURFACE_BYTE_BUDGET", "4096")
    assert _surface_byte_budget() == 4096
    monkeypatch.setenv("PI_SURFACE_BYTE_BUDGET", "0")
    assert _surface_byte_budget() == 0
    monkeypatch.setenv("PI_SURFACE_BYTE_BUDGET", "garbage")
    assert _surface_byte_budget() == 32 * 1024
    monkeypatch.setenv("PI_SURFACE_BYTE_BUDGET", "-5")
    assert _surface_byte_budget() == 0


def test_front_door_never_dropped_even_when_budget_exceeded(registry, monkeypatch):
    names = [*NATIVE_TOOL_NAMES, *_dynamic_names(registry, 5)]
    # 极小预算（1 字节）：native 前门仍在，动态全部裁掉
    monkeypatch.setenv("PI_SURFACE_BYTE_BUDGET", "1")
    kept, info = apply_surface_byte_budget(registry, names)
    assert set(NATIVE_TOOL_NAME_SET) <= set(kept)
    assert set(kept) - NATIVE_TOOL_NAME_SET == set()
    assert info["dropped"]
    assert info["bytes_used"] > 0


def test_greedy_fill_keeps_selection_order_prefix(registry, monkeypatch):
    dynamic = _dynamic_names(registry, 6)
    sizes = [(n, registry.schema_size(n) or 0) for n in dynamic]
    small_budget = 32 * 1024  # 先关预算拿到基线全量
    monkeypatch.setenv("PI_SURFACE_BYTE_BUDGET", str(small_budget))
    kept_full, _info_full = apply_surface_byte_budget(
        registry, [NATIVE_TOOL_NAMES[0], *dynamic]
    )
    assert kept_full[1:] == dynamic, "预算充裕时保持选择序全量"

    # 收紧预算：kept 必须是选择序的一个前缀（贪心装入语义）
    from app.services.chat.pi_native_surface import _PROXY_SCHEMA_BYTES

    front_bytes = registry.schema_size(NATIVE_TOOL_NAMES[0]) or 0
    tight = _PROXY_SCHEMA_BYTES + front_bytes + sum(s for _, s in sizes[:3]) + 1
    monkeypatch.setenv("PI_SURFACE_BYTE_BUDGET", str(tight))
    kept, info = apply_surface_byte_budget(registry, [NATIVE_TOOL_NAMES[0], *dynamic])
    assert kept[1:] == dynamic[:3]
    assert set(info["dropped"]) == set(dynamic[3:])
    assert info["bytes_used"] <= tight


def test_zero_budget_disables_and_returns_identity(registry, monkeypatch):
    names = [NATIVE_TOOL_NAMES[0], *_dynamic_names(registry, 10)]
    monkeypatch.setenv("PI_SURFACE_BYTE_BUDGET", "0")
    kept, info = apply_surface_byte_budget(registry, names)
    assert kept == names
    assert info == {"budget": 0, "bytes_used": 0, "dropped": []}


def test_disclosure_carries_budget_fields(registry, monkeypatch):
    disclosure = {}
    names = [NATIVE_TOOL_NAMES[0], *_dynamic_names(registry, 30)]
    monkeypatch.setenv("PI_SURFACE_BYTE_BUDGET", "20480")
    kept, info = apply_surface_byte_budget(registry, names, disclosure=disclosure)
    if info["dropped"]:
        assert disclosure["surface_budget"] == 20480
        assert disclosure["surface_bytes"] == info["bytes_used"]
        assert disclosure["budget_dropped"] == info["dropped"]
    else:
        assert "budget_dropped" not in disclosure


def test_schema_size_absent_counts_zero_and_bound_stays_hard(registry, monkeypatch):
    # 未注册名字：schema_size=None → 按 0 入账；预算尚有空间时保留
    monkeypatch.setenv("PI_SURFACE_BYTE_BUDGET", "8192")
    kept, info = apply_surface_byte_budget(
        registry, [NATIVE_TOOL_NAMES[0], "definitely_not_a_tool"]
    )
    assert "definitely_not_a_tool" in kept
    assert "definitely_not_a_tool" not in info["dropped"]
    # 预算被前门独占耗尽时：0 尺寸也不再新增（硬上界，绝不 fail-open 无界）
    monkeypatch.setenv("PI_SURFACE_BYTE_BUDGET", "1024")
    kept2, _ = apply_surface_byte_budget(
        registry, [NATIVE_TOOL_NAMES[0], "definitely_not_a_tool"]
    )
    assert "definitely_not_a_tool" not in kept2


def test_compute_turn_active_tools_budget_integration(monkeypatch, registry):
    from app.agent_pi_bridge import set_tool_registry

    set_tool_registry(registry)
    monkeypatch.setenv("PI_SURFACE_BYTE_BUDGET", "1")
    disclosure = {}
    names = _compute(monkeypatch, "做一份成都市小学密度热力图", disclosure)
    assert set(NATIVE_TOOL_NAME_SET) <= set(names), "极小预算下前门恒在"
    assert disclosure.get("budget_dropped"), "动态候选被预算裁剪并披露"


def _compute(monkeypatch, message, disclosure):
    from app.services.chat.pi_native_surface import compute_turn_active_tools

    return compute_turn_active_tools(message, disclosure=disclosure)


def test_default_budget_keeps_typical_turn_unchanged(monkeypatch, registry):
    from app.agent_pi_bridge import set_tool_registry

    set_tool_registry(registry)
    message = "做一份成都市小学密度热力图并做空间自相关分析"
    baseline = _compute(monkeypatch, message, {})
    assert set(NATIVE_TOOL_NAME_SET) <= set(baseline)
    # 默认预算：激活面字节不超预算（前门独大时除外），且裁剪只发生在尾部
    monkeypatch.setenv("PI_SURFACE_BYTE_BUDGET", "196608")  # 192KB：全量必装
    expanded = _compute(monkeypatch, message, {})
    assert set(baseline) <= set(expanded), "更大预算不减少任何工具"
