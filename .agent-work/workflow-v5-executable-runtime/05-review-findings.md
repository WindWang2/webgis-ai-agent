# Workflow V5 — Review Findings

## 架构挑战（Subagent-A，agent_5af42753，Phase B）

全部处置已纳入 01-architecture.md（R1 修订版）与实现：
- B1（BLOCKER）：V4 recompute 真实形态闭包失效 → Wave 1 修复 + 回归（ccf85276）；
- C1/C2/C3：quiescence 门、oracle 三方矛盾、整行 CAS 卡死 → 存储形态改每节点行；
- M1-M8 + MINOR：见 01-architecture.md 修订标注 [R1-*]。

## Round 1（Subagent-A，agent_da3881f5，commit 5a239b66 diff）

### CRITICAL（审查者只读执行复现，全部修复 + 回归，95360662）

| ID | 问题 | 修复 |
|---|---|---|
| C1 | 增量重算不闭包：下游 STALE 用重算前旧上游产物复用解除（STALE 重入队仅查「上游非 STALE」+ ready_set READY 无条件派发） | STALE 重入队须上游**结算**（SUCCEEDED/SKIPPED）；ready_set READY 同规；回归：两级链参数变化后下游 ref 必新、attempts 必增 |
| C2 | 输入 >2000 行静默截断后照常成功（科学结果错误不自知；50k 上界为死代码） | load_ref_features 返回 (features, truncated)；超界 → typed 失败 INPUT_TRUNCATED（FAILED+披露）；上界统一 ADAPTER_INLINE_ROW_CAP |
| C3 | run 完成边界 pending changes 永不 drain（门控 `== RUNNING` 写反，唯一生效场景被排除） | 门控改 `!= RUNNING`（完成边界）；回归：defer 后 run → pending 清空 + style 决策落库 |

### MAJOR（同批修复）

- M1 绑定阻断 `expected_from=PENDING` 使 READY 节点 CAS 卡死空转 → 去掉限定（转移表裁决）；
- M2 复用探测用当前会话 → 跨会话误删活缓存：改用 `artifact_session_id` 探测；`find` 按 anonymous session_scope 同域过滤；
- M3 残留 cancel_requested 使重驱立即 cancelled 永久终态：重驱 CAS 复位（含 terminal_at）；
- M4 级别聚合掩盖空身份端口：eligibility `input_identity_absent` 必 miss + 记录级最低水位降级 shape。

### MINOR/NIT（合理项全收口）

params_fp 与指纹同源；边界绑定仅 `data:*`；父取消入口传播（全异步传播披露为 follow-up）；在飞取消落 CANCELLED；派发即续租（m8）；decision/pending/supersede CAS（m5）；端口按边 to_port 对齐（m6 fan-in 错配）；store 热路径 to_thread 化（m4）；complete 快路径 claim 校验；shape 级拒绝不自毁；hooks 注释诚实化。

未采纳（记录理由）：
- m3 全异步取消传播（子实例在飞中断）：与父 run 同步驱动的架构下，入口检查 + 波次边界取消已覆盖诚实语义；真异步取消传播随集群派发 follow-up。
- 「重驱不复位 started_at」：started_at 语义 = 首次执行；重驱不重置（保留原点证据）。

## Round 2（Subagent-B，agent_986bc8ce，commit 1a2e4801 diff）

### BLOCKER（修复，94aad380）

- B-1 包注册全局 `(package_id, version)` 唯一 × 按 owner 解析 = attach
  一次性失效 + 跨租户注册投毒 + 409 指纹前缀 oracle → 唯一约束改
  `(owner_scope, package_id, version)`（db_model + migration 0034 原地
  同步，未合入无下游）、register 存在性检查同域、409 detail 脱敏、
  跨 owner 注册回归测试。

### MAJOR（同批修复）

- M-1 async 路径同步 DB 直呼（hooks owner 解析/ChangeApplier/chat 完成
  链/attach/STALE requeue/cancel sweep/subworkflow expand）→ 全部
  to_thread（CAS 退避 sleep 不再冻结事件循环）；
- M-2 chat 完成 TOCTOU：完成 CAS 后复查上游 STALE → 补标（不洗白）；
- M-3 defer CAS 被租约续期 revision 写手打成必败 → 重读重试 ≤3；
- M-4 load_ref_features 全载荷 deepcopy → descriptor feature_count 预检
  超界短路；
- M-5 泄漏 RUNNING 子实例永久占用 SUBWORKFLOW_CAP → 上限只计租约未过期者
  （行级清扫披露为 follow-up）。

### MINOR（收口）

get_instance OperationalError 上抛 StoreUnavailable（driver 退避重试，
不再与 not_found 混同返回 200）；profile 形状界（32KB/深度 8 → 422，
编译预算 ValueError → 422）；reuse 命中 output_fingerprint 探测用产物
所属会话；registry resolve SQL 侧排序截断。

### 未采纳 / follow-up（记录理由）

- m-5 inspector 组件未挂载宿主面板：挂载点均在并发 Epic 共享的 sidebar
  文件（project-tab/tasks-tab），冲突面 > 收益；组件+vitest 就绪，宿主
  接线列为 PR follow-up。
- m-2 终态实例的二次 drain 点：终态后 pending 仅在显式 rerun 时生效，
  语义可接受（rerun 即 drain），已由 C3 回归覆盖。
- m-3 rate limit 独立预算：编译实测 ~3ms，全局 240/min 足够；列观测点。
- m-1 每 owner LRU 剪枝写放大/决策环整写：量级有界（≤128/≤16），观测点。
