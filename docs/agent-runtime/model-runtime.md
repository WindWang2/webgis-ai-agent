# Model Runtime —— 描述符 / 角色 / 路由 / 健康 / Provider 适配

`app/services/chat/model_runtime/` · ADR-0102。

`model_config.resolve_llm_config` 仍是基础三元组（base_url/model/api_key）的
唯一解析点（audit4 #997）；本运行时在其上叠加能力/策略/健康维度，不建第二
配置真相。OpenAI 兼容 transport（`llm_client.py`，httpx 池化）保持不变 ——
**不做 transport 重写**；Pi 的 Node 侧模型系统不改，应用层只共享模型知识。

## 描述符（descriptors.py）

`ModelDescriptorRegistry`：来源优先级 override（运行时 upsert）> env/
file（`MODEL_DESCRIPTOR_OVERRIDES` JSON / `MODEL_DESCRIPTORS_FILE`）> 保守默认
（settings 主模型，能力未知）。**未知字段拒绝**（与工具注册同门）；未知能力
保守为 None，绝不编造厂商规格。

```json
[{"provider_id": "webgis", "model_id": "step-3.7-flash",
  "context_window": 128000, "max_output": 16384, "tool_calling": true,
  "reasoning_content": true, "json_mode": false, "fallback_group": "default"}]
```

## 角色 profile（roles.py）

策略不是 vendor：max_output / timeout / temperature / reasoning /
tool_access / context_budget_tokens / fallbacks / max_attempts / require_tools /
require_json。内置 execution、planner、title、spatial（与 model_config 语义
一致）+ subagent_worker、subagent_reviewer、structured_extraction。
`MODEL_ROLE_PROFILES` 环境变量 JSON 可覆盖（未知字段拒绝）。

## 路由（routing.py）

```python
cfg, decision = get_model_router().resolve_config(RouteRequest(
    role="execution", require_tools=True, est_context_tokens=...,
    prefer_model=...,   # operator/会话偏好
))
decision.reason_codes   # primary_configured / operator_preference /
                        # capability_skip:<model>:tools / health_skip:<m>:cooldown /
                        # primary_cooldown:<s>s / no_capable_fallback ...
decision.fallback_chain # 有界候选链
```

- 主模型 = operator 配置（或偏好）；即使冷却也保留选中但**披露**
  `primary_cooldown` —— 不静默覆盖 operator 决定；
- 能力护栏：`require_tools` 时 tool_calling=False 的候选跳过并留痕 ——
  **绝不静默降级到无工具模型**；
- 健康护栏：冷却候选跳过；限流/能力不匹配候选劣后；
- 链有界；无可用候选 → 主模型 + `no_capable_fallback`（诚实单次尝试）。

## 健康（health.py）

按 (provider, model) 键控，复用 geodata 断路器语义（ADR-0027：连败开门 +
指数冷却 30s→300s + 成功复位）但**独立实例**（故障域不同）。有界指标：
可用性/连败/近期限流/时延桶/最近成功/冷却/能力不匹配。**永不存内容。**
非自愈类失败（context_too_large / invalid_tool_schema / capability_mismatch）
不进退避计数 —— 等待不会自愈，改请求/换模型才行。

`router.observe(decision, latency_s=..., failure=FailureKind.X)` 是调用回报
唯一入口（成功/失败 → 健康表；失败 → trace fallback 事件）。

## Provider 适配（provider.py）

- `FailureKind`（§20 十类）：transport / timeout / rate_limit /
  provider_unavailable / unsupported / context_too_large /
  invalid_tool_schema / model_refusal / malformed_output / unknown；
- `classify_status_failure(status, body)` / `classify_exception(exc)`；
- `normalize_finish_reason` / `view_response`（归一化视图，replay/评测共用）；
- `sanitize_provider_error(text)` —— **provider 错误体是不可信输入**（§39）：
  控制字符剥离、伪 XML 围栏标签剥离、有界化后才可回注模型上下文。

契约测试：`tests/unit/test_provider_contract_v2.py`（§35 十二场景，
MockTransport，runtime 必须诚实 settle —— 截断流显式抛
`ProviderStreamTruncated`，绝不假成功）。

## Live 路由接线（ADR-0103）

`app/services/chat/model_routing_bridge.py` —— 把 ADR-0102 交付但未接入
production 路径的 router 接上，向后兼容：

- `resolve_routed_config(role, messages=, require_tools=, require_json=)` →
  `(LLMConfig, RouteDecision|None)`：router 的 primary 就是
  `resolve_llm_config(role)` 的既有结果（operator 配置 > runtime override）；
  router 只叠加能力护栏（context window / tool calling / json）、健康降级链
  （fallback_group + role fallbacks）与 reason codes。任何 router 异常 →
  legacy 路径（`decision=None`，无 decision 就无健康表更新，行为与历史一致）；
  `MODEL_ROUTER_ENABLED=0` 整体关闭。
- `observe_outcome(decision, latency_s=, exc=/status_code=/finish_reason=)`：
  失败经 `classify_exception` / `classify_status_failure` 分类后
  `router.observe` 入健康表（breaker / cooldown / capability_mismatch）+
  trace fallback —— 无观测不降级，观测绝不抛出。`health_snapshot()` 只读
  快照（评测/诊断）。`LatencyTimer` 是 latency_s 来源。
- `_estimate_context_tokens`：CJK-aware 粗估（与 context_budget 同一估算
  语义）；估算失败返回 0 —— router 视为未知，不猜。

### 引擎接线点

`app/services/chat/execution_engine.py`：

- `_call_llm` / `_call_llm_stream`：
  `resolve_routed_config(self._routing_role(), messages=, require_tools=bool(tools))`
  + `LatencyTimer`；流式在 `_observed_stream` 的 `finally` 里回报 —— 截断/
  中断异常按失败入健康表，正常耗尽按成功；
- `_generate_title`：title 角色同款路由 + 成功/失败回报；
- `_llm_config` / `_planner_llm_config`：require_tools / require_json 护栏，
  异常自动回退 legacy；
- 子代理：SubagentDispatcher 按角色 profile 的 `model_role` 注入子引擎构造
  （`app/services/subagent.py`）；`_routing_role()` 缺省回落 `execution`。

### V3 角色 profile（roles.py）

新增 8 个 policy-only 角色：architecture / debugger / scientific_review
（`preferred_group="strong"` 强推理池）与 corpus_worker / doc_crosscheck /
descriptor_enrichment / static_analysis（`preferred_group="cheap"` 高吞吐
廉价池）及 code_worker（default 组）。角色只声明**策略**（max_output /
timeout / temperature / require_tools / require_json / max_attempts /
preferred_group），不绑任何厂商 —— 模型本体由路由器按抽象 `fallback_group`
描述符（operator 经 `MODEL_DESCRIPTORS_FILE` 划分强弱/快慢池）+ 健康度确定
性解析；无分组数据回落 execution 主模型，行为不劣化。

测试锚点：`tests/unit/test_model_routing_bridge.py`（8 新角色存在性、
primary 不变、能力/上下文护栏、健康回报、引擎接线）、
`tests/unit/test_model_runtime_v2.py`。
