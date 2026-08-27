# ADR-0071: 制图会话运行时从 Pi bridge 抽出为共享模块

日期: 2026-08-27
状态: accepted

## 背景

2026-08-27 六路审计（Reviewer A / AH-P1-1）确认：约 890 行**与 Pi 传输无关**
的制图会话运行时——session 级 harness 注册表、`evaluate_cartographic_session`
（desired vs observed 收敛判定）、harness 上下文持久化/水化、runtime repair
推进、评估缓存与删除墓碑——全部寄居在 `app/agent_pi_bridge.py`。

后果：**默认运行路径（legacy ChatEngine）经 `tool_pipeline` 动态 import
"Pi bridge" 模块**完成制图闭环。任何针对 bridge 的"Piu 专属"重构都可能炸掉
legacy；模块职责名不副实；分层上 services → "Pi 桥" 是最显眼的迁移半途痕迹。

## 决策

整体迁出（1:1，无行为变更）到 `app/services/cartography_runtime.py`：

- `agent_pi_bridge.py` 保留 re-export（存量 importer 与测试兼容），自身聚焦
  Pi 传输职责（RPC 子进程、事件映射、turn 生命周期）；
- 三处 app importer（`routes/chat.py` 观察端点、`routes/metrics.py` 遥测、
  `chat/tool_pipeline.py` legacy 证据钩子）改引新模块；
- 测试中对注册表内部状态（`_harnesses` 等）的直接访问随迁指向新模块。

## 后果

- legacy 与 Pi 两条执行路径现在从**同一模块**获得制图闭环语义——它是
  desired-state 权威评估器，不是 Pi 的一部分。这是"Pi 成为唯一 Harness
  Runtime"收敛路线的第一步：共享能力先行下沉，传输层可替换。
- 后续翻转 `USE_NEW_AGENT` 默认值时，制图闭环不再是依赖风险点。
- 不变的边界：本模块不做 MapSpec 写入（写仍归 MapSpecLifecycleEngine），
  只做评估、证据与上下文水化。
