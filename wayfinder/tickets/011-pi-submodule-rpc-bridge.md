# Ticket: Pi submodule + Python RPC bridge

## 目标

将 Pi (earendil-works/pi) 作为 git submodule 引入，通过 RPC 模式与 Python 后端通信，替代之前自研的 Agent 系统。

## 架构

```
Python FastAPI (app/)
    ↓  JSON-RPC stdio
Pi submodule (vendor/pi/)
    ↓  AgentLoop + tools
LLM API
```

## 关键决策

1. **Pi 作为 submodule**: `vendor/pi/` 指向 earendil-works/pi
2. **RPC 模式通信**: Pi 的 `--mode rpc` 通过 stdin/stdout JSON-RPC
3. **Python 桥接层**: `app/agent_pi_bridge.py` 封装 subprocess 通信
4. **Feature flag**: `USE_NEW_AGENT=true` 启用 Pi 桥接
5. **GIS 工具保留**: 现有 `app/tools/` 通过 Pi 的 customTools 机制注入

## 实现步骤

1. ✅ 删除旧 `app/agent/` 包
2. ✅ 添加 Pi submodule
3. ✅ 构建 Pi (npm run build)
4. ✅ 创建 `app/agent_pi_bridge.py`
5. 🔲 接入 FastAPI routes
6. 🔲 添加测试

## 验收标准

1. `vendor/pi/` 存在且可构建
2. `app/agent_pi_bridge.py` 可导入
3. Pi subprocess 可启动并通过 RPC 通信
4. FastAPI route 可通过 feature flag 切换到 Pi
