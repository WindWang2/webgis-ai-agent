"""Pi 边界 pre-dispatch 校验闸测试（ADR-0180 D3/D4 / typed-tool-surface M2）。

- 玩具工具：精确锁定分层规则（unknown-field / required / 标量硬错 /
  无字符串叶升级档 / oversized 跳过 / extra=allow 豁免）；
- 真实 registry：零误拒 parity —— ref 游标字符串值绝不误判；闸通过与
  registry 直派结论一致性（闸只许少拒不许多拒）。
"""
import pytest
from pydantic import BaseModel, Field

from app.services.chat.pi_input_gate import (
    GATE_ERROR_CODE,
    validate_pi_tool_arguments,
)


class ToyArgs(BaseModel):
    k: int = Field(ge=2, le=10)
    label: str = ""
    payload: dict = {}
    mode: str = Field(default="fast")


@pytest.fixture(scope="module")
def toy_registry():
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()

    @reg.tool("toy_gate_tool", "toy", args_model=ToyArgs)
    def _toy(k: int, label: str = "", payload: dict = {}, mode: str = "fast"):
        return {"success": True}

    return reg


@pytest.fixture(scope="module")
def real_registry():
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    init_tools(reg)
    return reg


def test_no_model_tool_passes(toy_registry):
    from typing import Any

    from app.tools.registry import ToolRegistry

    bare = ToolRegistry()

    # 无显式 args_model：registry 会按函数签名生成校验模型（闸与 dispatch
    # 共用同一生成模型 —— 结论天然一致）。
    @bare.tool("bare_tool", "no explicit args model")
    def _bare(anything: Any = None, **kwargs):
        return {"success": True}

    # 生成模型字段 = {anything: Any?, kwargs: Any(required)} —— 与 dispatch
    # 同一模型同语义：合法键面放行。
    assert validate_pi_tool_arguments(
        bare, "bare_tool", {"anything": 1, "kwargs": {}}
    ) is None
    # 生成模型同样拒绝未知键 —— 闸与 dispatch 结论一致（parity 而非宽松）。
    report = validate_pi_tool_arguments(bare, "bare_tool", {"hallucinated": 1})
    assert report is not None


def test_non_dict_arguments_pass_through(toy_registry):
    # 非 dict 由 registry 的 json.loads/VALIDATION_ERROR 分支处理，闸不管。
    assert validate_pi_tool_arguments(toy_registry, "toy_gate_tool", "not-a-dict") is None


def test_unknown_field_rejected_with_allowed_list(toy_registry):
    report = validate_pi_tool_arguments(
        toy_registry, "toy_gate_tool", {"k": 5, "hallucinated": True}
    )
    assert report is not None
    root = [i for i in report["issues"] if i["path"] == "(root)"]
    assert root and "hallucinated" in root[0]["message"]
    assert "k" in root[0]["message"]  # 合法参数集列出（自愈向导）


def test_required_missing_rejected(toy_registry):
    report = validate_pi_tool_arguments(toy_registry, "toy_gate_tool", {"label": "x"})
    assert report is not None
    paths = {i["path"] for i in report["issues"]}
    assert "k" in paths


def test_scalar_container_mismatch_rejected(toy_registry):
    report = validate_pi_tool_arguments(
        toy_registry, "toy_gate_tool", {"k": {"deep": 1}, "label": "x"}
    )
    assert report is not None
    paths = {i["path"] for i in report["issues"]}
    assert "k" in paths
    # 容器注解收到数值 → 注定无效（数值不是 ref 游标）
    report2 = validate_pi_tool_arguments(
        toy_registry, "toy_gate_tool", {"k": 5, "payload": 42}
    )
    assert report2 is not None
    assert {i["path"] for i in report2["issues"]} == {"payload"}


def test_string_value_never_type_judged_ref_parity(toy_registry):
    # dict 注解字段收到字符串：可能是 ref 游标/别名 —— 闸绝不误拒。
    assert validate_pi_tool_arguments(
        toy_registry, "toy_gate_tool", {"k": 5, "payload": "ref:abc123"}
    ) is None


def test_no_string_leaf_upgrades_to_full_validate(toy_registry):
    # 全标量参数（无字符串叶）→ 升级档 model_validate：ge/le 违规被闸拦截，
    # 与 registry 全量校验同语义。
    report = validate_pi_tool_arguments(
        toy_registry, "toy_gate_tool", {"k": 99, "payload": {}}
    )
    assert report is not None
    assert any("k" in i["path"] for i in report["issues"])
    # 合法全标量参数通过
    assert validate_pi_tool_arguments(
        toy_registry, "toy_gate_tool", {"k": 5, "payload": {"a": 1}}
    ) is None


def test_oversized_args_skip_upgrade_but_keep_hard_rules(toy_registry, monkeypatch):
    # 大 GeoJSON 载体（模拟 oversized）：升级档跳过（registry 同款旁路），
    # 但 1-3 层硬规则仍生效。
    big_feature = {"type": "Feature", "geometry": {"coordinates": [[0.0, 0.0]]}}
    big_payload = {
        "type": "FeatureCollection",
        "features": [big_feature] * 8000,  # 远超 256KB 预算线
    }
    args = {"k": 5, "payload": big_payload}
    from app.tools.registry import _is_args_oversized

    assert _is_args_oversized(args), "测试前置：构造的参数应判 oversized"
    # oversized → 不做全量校验：非法的字符串叶内容（此处无字符串叶问题）
    # 不触发升级档；同时硬规则仍拒绝非法标量
    bad = dict(args, k={"not": "scalar"})
    report = validate_pi_tool_arguments(toy_registry, "toy_gate_tool", bad)
    assert report is not None and {i["path"] for i in report["issues"]} == {"k"}
    report_ok = validate_pi_tool_arguments(toy_registry, "toy_gate_tool", args)
    # payload 无字符串叶？Feature 字符串存在 → 升级档本就不触发；oversized 同样跳过
    assert report_ok is None


def test_gate_failure_is_fail_open(monkeypatch, toy_registry):
    # 闸自身异常 → 放行（registry 权威校验兜底），绝不阻断合法调用。
    # 闸在函数内 from 源头 import —— 直接 patch 源模块属性。
    monkeypatch.setattr(
        "app.tools.argument_normalization.normalize_tool_arguments",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert validate_pi_tool_arguments(
        toy_registry, "toy_gate_tool", {"k": 5}
    ) is None


def test_real_tool_ref_cursor_geojson_not_falsely_rejected(real_registry):
    # 真实工具：geojson 载体字段收到 ref 游标字符串 → 闸放行
    # （registry 会在 ref 解析后校验 —— 边界不能抢答）。
    report = validate_pi_tool_arguments(
        real_registry, "heatmap_data", {"geojson": "ref:session-artifact-1"}
    )
    assert report is None


def test_gate_error_code_contract(toy_registry):
    report = validate_pi_tool_arguments(
        toy_registry, "toy_gate_tool", {"k": 5, "oops": 1}
    )
    assert report is not None
    assert report["tool"] == "toy_gate_tool"
    assert isinstance(report["issues"], list)
    assert GATE_ERROR_CODE == "SCHEMA_VALIDATION_REJECTED"


def test_dispatch_reject_path_returns_typed_details(toy_registry, monkeypatch):
    """_dispatch_tool_bound 拒绝路径返回 typed details 且不进 dispatch。"""
    import asyncio

    from app.agent_pi_bridge import PiToolRequest, _dispatch_tool_bound, set_tool_registry

    set_tool_registry(toy_registry)
    request = PiToolRequest(
        toolCallId="tc-1", name="toy_gate_tool", arguments={"k": 5, "oops": 1},
        sessionId="s-gate",
    )
    resp = asyncio.run(_dispatch_tool_bound(request, toy_registry, "toy_gate_tool",
                                            {"k": 5, "oops": 1}, "s-gate"))
    assert resp.isError is True
    assert resp.details["code"] == "SCHEMA_VALIDATION_REJECTED"
    assert resp.details["retryable"] is True
    assert resp.details["issues"]
