# G01-PI-NATIVE-RUNTIME — M0 Baseline（实时审计事实）

> 审计时间：2026-11-07（UTC+8）晚；审计人：主 Agent + 子代理 A（只读深读）。
> 审计基准：`origin/master` = `585812ee4e2d75612f2b124e6fe6587af3deca5c`（2026-11-07 17:54:14 +0800，
> "Merge pull request #2010"）。与任务 Prompt 快照 SHA 一致；执行时已重新 fetch 确认。

## 1. 实时仓库事实

- Worktree：`/home/kevin/projects/webgis/webgis-wt-pi-native-gis-runtime-20261107`
  branch `zcode/goal-pi-native-gis-runtime-20261107`（自 origin/master 新建，无历史包袱）。
- vendor/pi gitlink：`f9a26365568127461c47a07a37dcb42c140a3ac5` = **tag v1.5.0**（本地对象验证，
  `git describe --tags` 输出 `v1.5.0`）。上游 `earendil-works/pi`。主 worktree 的 submodule
  检出停留在旧 commit（dd6bea41e, v0.81.1-1），本 worktree 已按 gitlink 正确初始化。
- Node v24.21.0（满足 vendor engines >=22.19）；宿主 python3 无 pytest，复用主 worktree
  `.venv`（pytest 9.1.1）在新 worktree 目录下运行（app 包按 cwd 解析，已验证）。

### 1.1 在途 PR（open，共 8 个，全部为 dependabot）

#1978/#1979/#1980/#1982/#1983/#1984/#1985/#1986 —— 均为依赖版本 bump，无功能冲突。

### 1.2 已合并关键 PR（本方向前置，不得重做）

| PR | 内容 | 与本方向关系 |
|---|---|---|
| #1972 | 类型化分析产物封套 v2 | GIS 执行域基础，只读 |
| #1973 | **Pi v0.81.1+1 → v1.5.0 vendor 升级 + WebGIS Agent Host 兼容契约（ADR-0282）** | 本方向地基；vendor pin/compat/build-vendor 均出自此 |
| #1974 | 情境化人机协同交互（选择→turn 链） | 消费 Pi turn 语义，不重做 |
| #1975 | 出版级制图 vNext | 制图域，只读 |
| #1989 | pi-gis usability v1（host↔map completion 缺口） | 已修复：不重做 |
| #1993 | pi-gis usability v2（city scope / publication layout 显式化） | 已修复：不重做 |
| #1998 | 工具载荷字节预算 + scope 披露保留 | 预算层已建立 |
| #2008 | Story 浏览器旅程 | 前端旅程，只读 |
| #2009 | session-lock 重入（J7 嵌套锁死锁） | 锁层修复，复用不重写 |
| #2010 | 会话恢复后 Story 命令可用性 | 会话恢复下游 |

### 1.3 Open Issues（#1999–#2007，均 P1、needs-triage）

A4 导出约束出口（#1999）、版面标记污染（#2000）、跨城市图层范围（#2001）、组件族锁（#2002）、
指北针旋转（#2003）、框选截断（#2004）、网络距离/秒混淆（#2005）、summary 丢数值（#2006）、
OpenAPI 快照过期（#2007）。**处置决策：全部避让**——均为制图/数据域独立 bug，不在本方向
（Pi 运行时）所有权内，且无一条阻断 Pi↔GIS 集成链。若实现中被证实直接阻断，最小修复并记录。

### 1.4 远端无 PR 的 feature branch

`feat/g01..g05-*`、`feat/h01/h02-*`、大量 `fix/*-2026110x` —— 均为已合并 PR 的残留 head
分支（对照 §1.2 合并记录确认）；无在途 Pi 方向未开 PR 分支。

## 2. 存量能力（子代理 A 深读审计，证据为 文件:行号）

规模：`app/agent_pi_bridge.py` 3,246 行 + 18 个 `app/services/chat/pi_*.py` + 扩展
`app/extensions/webgis-tools/index.mjs`（416 行）+ `scripts/pi_doctor.py`（499 行）+ 52 个 Pi 相关测试文件。

### 2.1 已经是生产级的部分（只加固，不重写）

- **exactly-once settlement 结构**：单结算 seam `_settle_turn_outcome`
  （agent_pi_bridge.py:2996-3016 非流式 :2352-2364）；kernel end_turn 幂等（same-turn receipt
  replay :1601-1604）；投影 `_projections_settled` 单次消费；lease 释放 owner-checked。
- **归属守卫**：HMAC turn token（pi_turn_context.py:40-95）、路由忽略 caller 路由字段、
  live-turn 409（pi_tools.py:61-92）、session 键控 active-turn 表 + Redis 镜像 + 跨 pod。
- **stale write 四层拒绝**：tracker 迟到 step 丢弃（:852-869）、plan 证据迟到跳过、kernel
  `tool_late` 台账、surface receipt 指纹对照。
- **工具投影三层**：冻结 native 7 + registry 超集 dump（spawn 一次）+ per-turn 动态激活面
  （`[WEBGIS_ACTIVE_TOOLS:...]` 尾标记，扩展两段加载纪律）；wrap-native 双端拒绝；32KB 预算。
- **取消链**：CancellationToken → dispatch → checkpoint 协作停止 → abort 快照只点燃目标
  pending ids；stall 120s / turn 300s / 心跳 8s；晚到结果系统性拒绝（409 + 四层 stale gate）。
- **安全**：回调 HMAC + 签名 token + live-turn；子进程 env 白名单；`--no-builtin-tools`；
  tier≥3 双拒；DESTRUCTIVE 逐出 native 面；注入 marker 中和；错误 sanitize。

### 2.2 真正的缺口（子代理 A 结论，按优先级）

| # | 级别 | 缺口 | 证据 |
|---|---|---|---|
| G1 | P0 | SSE 事件幂等缺失：Pi 重复回传 `tool_execution_end` → cache 单次消费后第二份走 cache-miss 仍发 `step_result`，无 toolCallId 去重，无测试 | pi_event_mapper.py:247-271；agent_pi_bridge.py:369-381 |
| G2 | P0 | 纯文字任务不产生 map completed **无负面测试**（行为正确但回归无锁） | pi_event_mapper.py:305-314；全 Pi 测试 grep 无一负面断言 |
| G3 | P0 | turn 状态机碎片化：6 布尔 + 2 字符串 × 两条路径（prompt/stream_prompt）拼凑；`tool_wait`/`settling` 不可表达不可观测；两条路径各复制一份初始化（两处 UnboundLocalError 史） | agent_pi_bridge.py:2044-2061, 2558-2589 |
| G4 | P1 | exactly-once settlement 无直接测试（seam 幂等设计完备但"第二次结算为 no-op"无用例）；`_settle_turn_outcome` else→`complete_task` 开放分支无未知词表防御 | agent_pi_bridge.py:1670；test_turn_settlement_seam.py 七用例均测诚实终态 |
| G5 | P1 | registry 热更新对已运行 Pi 子进程不可见（dump 仅 spawn 一次，变化只影响下一 spawn） | pi_rpc_client.py:394-397 |
| G6 | P1 | 运行时 native/fallback/unavailable 三态披露不统一：/health 只报 runtime 名不报降级原因；per-request 静默降级客户端不可见（除 task_start） | health.py:93-122；chat.py:710-755 |
| G7 | P2 | 无冷/热启动延迟基准（test_pi_perf 全 mock）；TTFT/事件吞吐只进日志无聚合 | pi_surface_metrics.py:28-35 |
| G8 | P2 | turn 取消 → 排队 durable job 撤销链路薄（JobOrigin 关联有，撤销无实现无测试） | agent_pi_bridge.py:781-802 |
| G9 | 空白 | fork 语义（无）；跨 turn sequence（SSE id 仅 turn 内）；PI_SURFACE_SCOPE_ENFORCE 生产推进无记录 | — |

### 2.3 存量测试基线（本 worktree，主 .venv，`USE_NEW_AGENT=false` conftest 钉）

- batch1（abi/dynamic/parity/spec/metrics/receipt/budget）：87 passed
- batch2（bridge_compat/concurrency/leak/low_review/pool×2/cancellation/dispatch×2/event_mapper）：113 passed
- batch3（extension×3/usability×2/h01×4/harness/input_gate）：120 passed
- batch4（native_surface/post_dispatch/result_abi/rpc_client/stream_parity/surface_abi_route/tools_route_auth/turn_context/turn_lock/vendor_identity/liveness）：99 passed
- batch5（bridge_lock/multipod/session_plan_host/integration）：40 passed
- test_pi_status_fail_closed.py：6 passed（含覆盖率 79s）
- **合计 465 passed / 0 failed**（ vendor dist 未构建时；需 vendor 的 4 个文件显式 skip）

## 3. 冲突矩阵与避让决策

| 计划改动 | 碰撞面 | 决策 |
|---|---|---|
| TurnState 显式状态机（G3） | agent_pi_bridge.py 两条 prompt 路径 | 自有所有权；增量收敛不改公共 API；seam 保持 |
| SSE 幂等去重（G1） | pi_event_mapper.py + bridge cache | 自有；mapper 保持纯函数，去重放 bridge 层 |
| 纯文字负面测试（G2） | 新测试文件 | 无碰撞 |
| settlement 直测（G4） | tests/unit/test_turn_settlement_seam.py 增补 | 无碰撞 |
| 三态披露（G6） | health.py / chat.py 切换点 / pi_compat.py | 小范围契约适配，不改 ChatEngine 主路径 |
| 热更新可见性（G5） | pi_rpc_client spawn dump + registry refresh 钩子 | 自有；只加"registry 世代变化→下次 spawn 刷新 dump" |
| 性能度量（G7） | pi_surface_metrics.py + benchmarks | 自有 |
| durable job 取消（G8） | ToolDispatchService 边界 | 仅小范围契约适配（gis_harness 所有权约束） |
| fork（G9） | — | **降范围**：仅验证既有 new_session/拒绝语义 + 文档化边界，不实现 fork（避免平行会话持久化/第二事实源） |
| issues #1999–#2007 | 制图/数据域 | 全部避让 |

## 4. Pi v1.5.0 上游契约锚点（防猜测）

- 扩展两段加载：`load()` 只注册、`session_start` 才 `setActiveTools`（index.mjs:260-291, 371-379；
  pi_doctor 检查 3 钉死该纪律）。
- 事件词表以 pi_doctor rpc 域 live 验证为准（:249-426）：`agent_settled` 是唯一终判。
- 本地 vendor 已按 gitlink 初始化至 v1.5.0，`scripts/build-vendor-pi.sh` 构建进行中（M1 记录结果）。
- 升级决策（PI-10）：保持 pin v1.5.0，只产出兼容矩阵文档，不擅自升级。

## 5. M0 结论

方向合法：无在途 PR/分支冲突；前置 PR 提供了完整地基；缺口清单（G1–G9）全部落在
"已有但碎片化/薄"区间，符合"深化运行时而非重做"的任务定位。进入 M1 characterization。
