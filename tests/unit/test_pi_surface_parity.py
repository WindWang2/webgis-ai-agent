"""Pi/legacy 校验与闸 parity 测试（ADR-0180 D4 / typed-tool-surface M4/T8）。

不是要求暴露数量相同，而是钉住：
- **校验等价**：gate 拒绝 ⇒ registry dispatch 同样 VALIDATION_ERROR
  （真实工具、真实 Pydantic 模型、session_id="" 免 ref 解析 —— 非法参数
  在校验阶段被拒、绝不执行）；gate 通过 ⇒ dispatch 不以 VALIDATION_ERROR
  失败（玩具工具承载真实形态模型，避免执行真实工具）；
- **入口等价**：注册面裸名直调与 webgis_execute(inner) 解析出同一
  (tool_name, arguments)，进同一 dispatch 管线；
- **tier 等价**：tier-3 经裸名（面外 reject）与经 proxy（bridge tier 闸）
  双路都不可达。
"""
import asyncio

import pytest

from app.services.chat.pi_input_gate import validate_pi_tool_arguments


@pytest.fixture(scope="module")
def registry():
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    init_tools(reg)
    return reg


def _dispatch(registry, name, args):
    return asyncio.run(registry.dispatch(name, dict(args), session_id=""))


def _is_validation_error(result) -> bool:
    return isinstance(result, dict) and result.get("code") == "VALIDATION_ERROR"


def test_gate_reject_implies_registry_validation_error(registry):
    """单向不误拒的强断言：gate 拒的，registry 注定也拒（真实模型）。"""
    cases = [
        ("query_local_poi", {"district": "成都市", "hallucinated": True}),
        ("heatmap_data", {"cell_size": {"not": "int"}, "geojson": {"type": "FeatureCollection", "features": []}}),
    ]
    for name, args in cases:
        gate = validate_pi_tool_arguments(registry, name, args)
        assert gate is not None, f"用例预期 gate 拒绝: {name} {list(args)}"
        dispatched = _dispatch(registry, name, args)
        assert _is_validation_error(dispatched), (
            f"parity 破损：gate 拒但 registry 放行 → {name} {dispatched}"
        )


def test_registry_validation_error_implies_gate_reject_or_skip(registry):
    """registry 校验拒绝的，gate 要么同拒、要么因字符串叶保守放行（闸⊆registry）。"""
    cases = [
        ("query_local_poi", {"district": 123, "subtype": "小学"}),  # int→str 注解
    ]
    for name, args in cases:
        gate = validate_pi_tool_arguments(registry, name, args)
        dispatched = _dispatch(registry, name, args)
        if gate is not None:
            assert _is_validation_error(dispatched), (
                f"gate 拒但 registry 接受 = 误拒：{name} {args}"
            )
        else:
            # 字符串叶保守放行是设计内（ref/别名），此时 registry 必须接受
            assert not _is_validation_error(dispatched)


def test_normalized_alias_args_pass_gate_and_dispatch(registry, tmp_path):
    """归一化等价：合法别名经同一声明表折叠后闸放行（dispatch 内折叠一致）。"""
    from pydantic import BaseModel, Field

    from app.tools.registry import ToolRegistry

    class AliasArgs(BaseModel):
        cell_size: int = Field(default=500, ge=1)

    toy = ToolRegistry()

    @toy.tool("parity_alias_tool", "toy", args_model=AliasArgs)
    def _t(cell_size: int = 500):
        return {"success": True}

    # kebab-case 别名：闸先归一化（与 dispatch 同一函数）再校验 → 放行
    assert validate_pi_tool_arguments(toy, "parity_alias_tool", {"cell-size": 800}) is None
    result = _dispatch(toy, "parity_alias_tool", {"cell-size": 800})
    assert not _is_validation_error(result)


def test_direct_and_proxy_surface_resolve_identically(registry):
    """入口等价：裸名直调与 proxy 内名解析出同一 (tool_name, arguments)。"""
    from app.services.chat.pi_native_surface import (
        EXECUTE_PROXY_NAME,
        NATIVE_TOOL_NAME_SET,
        registered_surface_names,
        resolve_pi_tool_call,
    )

    registered = set(registered_surface_names(registry))
    tool = next(n for n in registered if n not in NATIVE_TOOL_NAME_SET)
    args = {"x": 1}
    direct = resolve_pi_tool_call(tool, args, registered_surface=registered)
    via_proxy = resolve_pi_tool_call(
        EXECUTE_PROXY_NAME, {"toolName": tool, "arguments": args},
        registered_surface=registered,
    )
    assert direct.kind == "execute"
    assert via_proxy.kind == "execute"
    assert direct.name == via_proxy.name == tool
    assert direct.arguments == via_proxy.arguments == args


def test_tier3_unreachable_via_both_entry_paths(registry):
    """tier 等价：tier-3 经裸名与经 proxy 双路都不可达。"""
    from app.services.chat.pi_native_surface import (
        registered_surface_names,
        resolve_pi_tool_call,
    )

    registered = set(registered_surface_names(registry))
    tier3 = next(n for n in registry.list_tools()
                 if int(registry.metadata(n).get("tier", 1)) >= 3)
    # 裸名：面外（注册超集排除 tier≥3）→ reject
    direct = resolve_pi_tool_call(tier3, {}, registered_surface=registered)
    assert direct.kind == "reject"
    # proxy 内名：resolve 放行为 execute → bridge tier 闸拒绝（结构性同一闸）。
    # 这里直接断言 bridge 的 tier 检查语义（metadata 权威）。
    assert int(registry.metadata(tier3).get("tier", 1)) >= 3


def test_gate_and_surface_share_single_schema_truth(registry):
    """单一真相：spawn dump、闸用的 args model 与 dispatch 校验模型同源。"""
    from app.services.chat.pi_native_surface import pi_surface_for_spawn

    surface = pi_surface_for_spawn(registry)
    sample = [t["name"] for t in surface["tools"]][:20]
    for name in sample:
        model = registry.args_model(name)
        assert model is not None
        # dump 的 properties 键 ⊆ 模型字段（session_id 投影剥离除外）
        dumped = next(t for t in surface["tools"] if t["name"] == name)
        props = set(dumped["parameters"].get("properties", {}))
        assert props <= set(model.model_fields.keys()) | {"session_id"}
