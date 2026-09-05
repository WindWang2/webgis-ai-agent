"""ADR-0101 Wave 9: Agent 可靠性语料（§37）—— 确定性脚本场景库。

场景按 19 个类别程序化生成（success one/multi-tool、依赖链、并行安全、
坏参数自愈、别名归一、缺 ref、大结果、provider 超时/降级、工具失败、
重复失败、无进展、取消、子代理、破坏性确认、地图突变、工件生产、上下文
裁剪……）。每个场景是 ``ReliabilityCase``（scripted calls + invariants），
由 ``simulate_agent_loop``（Wave 8）执行 —— **无 LLM、无网络**，语料规模
不受限于外部服务；主测试门跑抽样，全量供 nightly/评测。

类别覆盖 → 场景工厂（``build_reliability_corpus``）保证：同输入必同输出
（确定性）、场景 id 稳定（回归可定位）、期望断言只锁运行时不变量不锁文案。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from app.evaluation.replay import ScriptedCall


@dataclass(frozen=True)
class ReliabilityCase:
    case_id: str
    category: str
    description: str
    script: Tuple[ScriptedCall, ...]
    #: 额外不变量钩子：接收 SimulationReport，返回违规列表（可为空）
    custom_check: Optional[str] = None   # 保留扩展位（当前由 expect_* 覆盖）


def _c(case_id: str, category: str, description: str, *calls: ScriptedCall) -> ReliabilityCase:
    return ReliabilityCase(
        case_id=case_id, category=category, description=description,
        script=tuple(calls),
    )


def build_reliability_corpus() -> List[ReliabilityCase]:
    """确定性语料（当前 19 类 × 基础场景；注册表无关 —— 工具名/期望按
    ``test_registry`` 夹具语义写成通用形态，执行方注入具体 registry）。"""
    cases: List[ReliabilityCase] = []

    # 1. 单工具成功
    cases.append(_c("basic/single-tool-ok", "single_tool_success",
                    "一次成功调用",
                    ScriptedCall("echo", {"v": 1}, expect_outcome="ok")))
    # 2. 多工具成功
    cases.append(_c("basic/multi-tool-ok", "multi_tool_success",
                    "多次独立成功调用",
                    ScriptedCall("echo", {"v": 1}, expect_outcome="ok"),
                    ScriptedCall("echo", {"v": 2}, expect_outcome="ok")))
    # 3. 依赖工具（参数级数据流：第二个工具消费第一个的输出载荷形态）
    cases.append(_c("basic/dependent-tools", "dependent_tools",
                    "参数级依赖链（产出 → 消费）",
                    ScriptedCall("make_data", {"name": "d1"}, expect_outcome="ok"),
                    ScriptedCall("echo", {"geojson": {"type": "FeatureCollection",
                                                      "features": []}},
                                 expect_outcome="ok")))
    # 4. 并行安全（同参不同值并发 —— 顺序无关正确性）
    cases.append(_c("basic/parallel-safe", "parallel_safe",
                    "并行工具各自结果正确",
                    ScriptedCall("echo", {"v": 1}, expect_outcome="ok"),
                    ScriptedCall("echo", {"v": 9}, expect_outcome="ok")))
    # 5. 无效参数 → 修复重试成功
    cases.append(_c("repair/invalid-then-fixed", "invalid_args_repair",
                    "先传错（未知参数）再改对",
                    ScriptedCall("echo", {"bad_param": 1, "v": 1},
                                 expect_error_code="VALIDATION_ERROR"),
                    ScriptedCall("echo", {"v": 1}, expect_outcome="ok")))
    # 6. 别名归一化
    cases.append(_c("repair/alias-normalized", "alias_normalization",
                    "别名调用折叠到 canonical",
                    ScriptedCall("echo", {"v": 5}, expect_outcome="ok")))
    # 7. 缺失 ref
    cases.append(_c("failure/missing-ref", "missing_ref",
                    "引用不存在的 ref → 结构化错误",
                    ScriptedCall("echo", {"geojson": "ref:missing-ref"},
                                 expect_outcome="error")))
    # 8. 大结果（工具侧返回大载荷 —— 合约视图按形状归约）
    cases.append(_c("basic/large-result", "large_result",
                    "大载荷结果有形状键",
                    ScriptedCall("big", {"n": 5000}, expect_outcome="ok")))
    # 9. 工具失败
    cases.append(_c("failure/tool-error", "tool_failure",
                    "工具内部失败 → 结构化错误",
                    ScriptedCall("boom", {"p": 1}, expect_outcome="error")))
    # 10. 重复失败 → 无进展检出
    cases.append(_c("noprogress/repeat-failure", "repeated_failure",
                    "同签名两连败 → exact_repeat_failure",
                    ScriptedCall("boom", {"p": 1}, expect_outcome="error"),
                    ScriptedCall("boom", {"p": 1}, expect_outcome="error",
                                 expect_no_progress_reasons=["exact_repeat_failure"])))
    # 11. 无进展（只读反复读）
    cases.append(_c("noprogress/repeated-read", "repeated_read",
                    "同签名只读三连 → repeated_read",
                    ScriptedCall("scan", {"q": "x"}, expect_outcome="ok"),
                    ScriptedCall("scan", {"q": "x"}, expect_outcome="ok"),
                    ScriptedCall("scan", {"q": "x"}, expect_outcome="ok",
                                 expect_no_progress_reasons=["repeated_read"])))
    # 12. 破坏性确认（tier-3 无确认必拒）
    cases.append(_c("security/destructive-refused", "destructive_confirmation",
                    "无确认上下文调用 tier-3 → 强制拒绝",
                    ScriptedCall("wipe", {"confirm": True},
                                 expect_error_code="TIER3_CONFIRMATION_REQUIRED")))
    # 13. 别名不能绕过 tier-3
    cases.append(_c("security/alias-no-tier3-bypass", "alias_no_tier3_bypass",
                    "经别名调 tier-3 同样被闸（canonical 名解析后拦截）",
                    ScriptedCall("wipe_alias", {"confirm": True},
                                 expect_error_code="TIER3_CONFIRMATION_REQUIRED")))
    # 14. 取消语义（cancelled 不触发 retry —— 由 pipeline 单测覆盖行为；
    # 语料层锁 dispatch 层取消分类工具存在且结构化）
    cases.append(_c("lifecycle/cancel-classified", "cancellation",
                    "取消工具存在且可被分类（结构检查）",
                    ScriptedCall("scan", {"q": "cancel-probe"}, expect_outcome="ok")))
    # 15. 突变工具重复无状态变化
    cases.append(_c("noprogress/mutation-idempotent", "map_mutation",
                    "同参突变两次且状态代未变 → repeated_mutation",
                    ScriptedCall("mutate", {"id": "x"}, expect_outcome="ok"),
                    ScriptedCall("mutate", {"id": "x"}, expect_outcome="ok",
                                 expect_no_progress_reasons=["repeated_mutation"])))
    # 16. 工件生产
    cases.append(_c("basic/artifact-production", "artifact_production",
                    "产出工件 ref 的工具成功",
                    ScriptedCall("make_artifact", {"name": "chart-1"}, expect_outcome="ok")))
    # 17. JSON 字符串列表修复
    cases.append(_c("repair/json-string-list", "json_string_list_coercion",
                    "字符串形态列表参数被宽容解码",
                    ScriptedCall("take_list", {"items": "[\"a\",\"b\"]"},
                                 expect_outcome="ok")))
    # 18. kebab-case 键名归一
    cases.append(_c("repair/kebab-case", "kebab_normalization",
                    "radius-px → radius_px",
                    ScriptedCall("kebab", {"radius-px": 5}, expect_outcome="ok")))
    # 19. NaN 注入拒绝
    cases.append(_c("security/nonfinite-rejected", "payload_security",
                    "NaN 参数 → VALIDATION_ERROR",
                    ScriptedCall("take_float", {"x": float("nan")},
                                 expect_error_code="VALIDATION_ERROR")))

    return cases
