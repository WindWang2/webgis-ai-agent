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
