# 决策账本

| 时间/阶段 | 决策 | 依据与兼容策略 |
|---|---|---|
| M0 | 使用原生 Goal + 持久文件 Oracle 循环 | 当前技能清单无 `goal-loop`，不虚构技能；停止以 Oracle/PR 为准 |
| M0 | 仅从实时 origin/master 创建指定 worktree/branch | 原工作树有他人改动，全部保持不动 |
| M0 | 不重做 Pi v1.5.0、锁、完结门、MapSpec/渲染/Planner | #1973/#1989/#2009 与 G01–G05 边界 |
| M0 | 新能力优先落在 Replay/Lab 的 additive 只读投影与测试/CLI | 避免第二 trace store、第二判决器、第二执行器 |
| M0 | 评测模式显式分为 offline_replay、live_dispatch_stub_llm、browser_renderer、live_services_opt_in | 防止 mock/fixture 被描述为生产链验证 |
| M0 | 公共测试 seam 采用用户指定的既有 ToolDispatchService、Replay/Lab 合约、RecoveryPolicy 与 ratchet | 全自动契约已预授权这些 seam；不测试私有实现细节 |
| M0 | 新指标 record-only，旧 baseline/阈值默认不变 | 防止以平均分和门槛调整掩盖致命回归 |

| M3 | 仅对 Issue #2004 做截断披露与下游 stale 防线 | 超限选择直接阻断科学输入证据；不改前端选择集或分析执行器；所有权校验保持上游 |
| M3 | 跨城、A4、旋转指北针、秒/米与 summary 缺口保留为显式未验证边界 | 避免把本线变成独立 issue 抢修；普通 guard/export 通过不宣称这些生产问题已修复 |
| M3 | 黄金 CLI 在导入 app 前初始化 tests 离线环境 | 裸进程首次因 Redis LockDegradedError 30/30 失败；改后 30/30 通过，失败与修复均记录 |
| M3 | 复用 72 个既有场景并只写引用 manifest | 避免第二执行器和静态 fixture 复制；focused tests 单独标执行状态 |

| M6 | resume 默认 flush_interval=16，interval=1 保留旧每场景写语义 | 实测 1.71×；中断最多重算 15 条同 seed 确定性场景，JSON 形状/跳过语义不变 |
| M6 | UTF-8 计量修正不宣称提速；deepcopy/sha256 不优化 | 微基准 canonical_json 更慢但计量正确；隔离数据显示其他候选非瓶颈，避免为指标硬优化 |
| M7 | 新 journey 指标只 record-only，active baseline 不动 | 防止未成熟维度变成新质量门；跨会话/科学错误仍硬失败 |
