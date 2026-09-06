# Agent Tool Platform + Model Runtime + Trace/Replay Foundation V2

实现分支：`feat/agent-tool-model-runtime-v2` · ADR：[0101](../adr/0101-agent-tool-model-runtime-v2.md) · [0102](../adr/0102-model-provider-runtime-foundation.md)

本平台把既有骨架（ToolRegistry 执行真相 / ToolDispatchService 派发闸 /
Pi 宿主 / ToolCatalog 分层选择）升级为可观测、可测试、可重放的工具与模型运行时。
**它不替换 Pi，不建第二注册中心，不复制 GIS 语义真相。**

```text
Pi / Agent Host
    │
    ├── Model Runtime            app/services/chat/model_runtime/
    │     descriptors / roles / routing / health / provider
    │
    ├── Agent Context Runtime    app/services/chat/context_budget.py
    │     context_projections.py
    │
    └── Tool Platform
          ToolRegistry truth     app/tools/registry.py
          ToolDescriptor V2      app/tools/descriptor.py
          参数归一化              app/tools/argument_normalization.py
          结果契约视图            app/lib/runtime/result_contract.py
          ToolSurface 投影        app/services/tool_surface_v2.py
          检索 / 压缩             app/services/chat/tool_retrieval.py
                                 app/services/chat/schema_compression.py
          Trace / Replay          app/lib/runtime/trace.py
                                 app/evaluation/replay.py
```

## 文档索引

| 主题 | 文档 |
|---|---|
| 工具描述符 / 生命周期 / 指纹 | [tool-descriptor.md](tool-descriptor.md) |
| 工具面投影 / 检索 / 压缩 | [tool-surface.md](tool-surface.md) |
| 模型运行时 / 角色 / 路由 / 健康 | [model-runtime.md](model-runtime.md) |
| 上下文预算 / 投影 | [context-runtime.md](context-runtime.md) |
| Trace / Replay / 语料 | [trace-replay.md](trace-replay.md) |
| 执行策略审计 / 安全红线 | [security-policy.md](security-policy.md) |

## 不变式（评审基线）

1. Pi 是默认 agent 宿主（`USE_NEW_AGENT`）；本平台只提供 adapter/projection。
2. `ToolRegistry` 是唯一执行真相；描述符是派生只读投影。
3. 无第二 GIS capability/algorithm/recipe 注册表；工具只引用 capability id。
4. tier-3 确认闸在 `registry._dispatch_impl`（ContextVar），别名/Pi/子代理/
   重放均不可绕过。
5. 指纹确定性：canonical JSON + SHA-256；描述 change 与 schema change 可区分。
6. 大结果 = ref + 有界摘要；模型永不见原始巨载荷。
7. LLM provider 健康与 geodata 健康是两个故障域，绝不混用实例。
8. 路由确定性 —— 理由码全量留痕，绝不静默能力降级。
