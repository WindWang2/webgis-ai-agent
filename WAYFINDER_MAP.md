# Wayfinder Map: Agent System via Pi Submodule

## Destination

将 webgis-ai-agent 的 agent 系统从隐式散落在 `ChatEngine` 中的逻辑，改为直接引入 **Pi (earendil-works/pi)** 作为 git submodule，通过 RPC 模式调用，保留 GIS 专属桥接层。

完成标志：Pi submodule 可构建、Python RPC bridge 可通信、通过 feature flag 切换、全部测试通过。

## 架构

```
Python FastAPI (app/)
    ↓  JSON-RPC stdio
Pi submodule (vendor/pi/)
    ↓  AgentLoop + tools
LLM API
```

## Decisions so far

- [新方向] 放弃自研 Agent，直接引入 Pi (earendil-works/pi) 作为 git submodule ✅
- [架构] Pi 通过 `--mode rpc` 提供 JSON-RPC stdio 接口，Python 通过 subprocess 通信 ✅
- [桥接] `app/agent_pi_bridge.py` 封装 Pi RPC 通信 ✅
- [Feature flag] `USE_NEW_AGENT=true` 启用 Pi，否则走 legacy ChatEngine ✅
- [构建] Pi monorepo 已构建成功（coding-agent, agent-core, ai, server） ✅

## Open Tickets

- [ ] 完善 Pi RPC bridge（streaming prompt、abort、state 查询）
- [ ] 将 GIS 工具注入 Pi 的 customTools 机制
- [ ] 端到端测试：用真实 LLM 验证 Pi bridge
- [ ] 性能基准：对比 Pi vs ChatEngine

- 前端 UI 重写 (仅 backend agent 系统)
- 新的 LLM provider 接入 (保持现有 OpenAI-compatible 接口)
- GIS 算法库重写 (仅 agent 调度层)
- 容器化/沙箱执行 (Pi 的 Gondolin/Docker 模式，当前不需要)
