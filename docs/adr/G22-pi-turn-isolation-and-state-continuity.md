# ADR（G22）：Pi turn 交接门与长会话 GIS 状态连续性

> 状态：Proposed（G22 方向草案；合并前按 master 最新 ADR 编号规则定号——
> 0288 已有三篇撞号、0289 已用，预计 0290+，提交 PR 时重核对）。

## 语境

G12（ADR-0288）交付 Pi 原生发现/嵌套调用/clear_queue/配置代际后，长会话
连续性仍存在四类已核实缺口：

1. **同会话迟到取消误伤后继 turn**（G12 review P2-1 残留）：`clear_queue`/
   `abort` 是 worker 级全局 RPC（vendor 无 turn-scoped 命令），而同会话
   后继 turn 不改变 `_active_turn_sid` —— 仅比对 session id 的守卫挡不住
   「T1 的迟到取消命中 T2」。
2. **权威 GIS 状态恢复目录缺口**：`GisStateRebuildProvider` 只投影少量
   图层/可见性/refs；用户锁定、G16 标注覆写、G15 画幅、撤销栈、任务清单、
   数据 ref 有效性均缺席（其 docstring 自己承认「等权威源落地」——
   G14/G15/G16 合入后权威源已存在）。
3. **重启残留**：崩溃进程的 Redis `webgis:pi:active_turn:*` key（值=裸
   turn_id、无归属）最长滞留 15min，新实例的 live-turn 检查会误信。
4. **观测真空**：J7 llm_calls 计数因 json.dumps 空格 vs node 紧凑输出恒 0
   （G12 N4），断言 vacuous。

## 决策

### D1 — turn 交接门（宿主串行化，不发明 upstream API）

`PiRpcClient.request()` 拆分**写相位**（`issue`：登记 pending + stdin 写出）
与**响应相位**（`collect`）。`PiBridge.abort` 与 turn 登记/注销共用每 bridge
一把 `asyncio.Lock`（交接门）：

- abort 在门内完成「**目标 entry 对象同一性**校验 + 控制写出」原子段——
  写出完成才放门；翻转（后继 turn 已登记）→ 整体跳过写出。
- `register_active_pi_turn` 的本地登记变更同门（有界等待，超时降级+告警）
  ——vendor 单 stdin 流上传递序「旧控制 RPC 字节 < 后继 turn prompt 字节」
  由此保证（写出与登记互斥 + 各自 await 完成点传递）。
- 写后快照作用域效果（token 点燃/F03 来源记录/失败快照 pending ids）在
  翻转时不再整体早退（旧早退丢失 F03 诚实终态）；无目标 turn 的 legacy
  清场（W07：清残留 steer 队列）保留，不记录空 turn id 来源。
- 兼容面：`request()` 语义不变；mock 客户端需补 `issue/collect` 契约
  （6 个测试文件迁移）。

**残余窗口（如实记录，review 修复轮 2 收敛）**：

- vendor rpc 模式对 stdin 行是**并发分发**（prompt 分支 fire-and-forget、
  abort 分支 `await session.abort()`）——交接门只保证**字节序**；teardown
  另以 `_drain_control_rpcs` 在 unregister 前有界（5s）等待在飞控制 RPC
  的**响应**（响应落定 = vendor 侧处理完成），把「后继 run 在旧 abort
  效果落地前启动」的 vendor 内竞态窗口压到响应超时残余。滞留 future 在
  超时后移出追踪集（双层 finally 不叠加等待）。
- wedged 控制写持门 >5s：后续 abort **整体跳过**（fail-safe——发不出就
  绝不发，宁可不取消也不冒险命中后继 turn）。
- Redis 实例前缀默认 host 维度：同宿主**多进程**部署（uvicorn
  --workers、dev+test 并跑）必须为每进程显式设置互不相同的
  `WEBGIS_INSTANCE_ID`，否则后启进程的启动清扫会误删同宿主其他进程的
  live key（多 pod 安全 ≠ 同宿主多进程安全）；实例段冒号一律清洗为
  `_`（值格式以首个冒号分段）。

### D2 — 权威 GIS 状态恢复目录（GisStateRebuildProvider 扩展）

全部 additive：用户约束**全层提取**（workbench 锁、隐藏层、G16
`label.authoring.overrides`、G15 `frame_product.active_frame`、agent undo
栈），不受通用目录扫描界限制；`SharedTurnFacts` 增 `durable_jobs`（非终态
AnalysisTask 有界投影）与 `invalid_source_refs`（mapspec source → session
ref 存在性核对）两事实，缺席=None 不投影。预算纪律：域 cap 1600 不变，
用户约束优先、通用目录逐行让位并披露截断与 id 寻址；selection 无持久
权威源——不发明。summary（压缩/vendor 侧）从不进入 facts（A17 结构性
保证：权威读取覆盖一切旧值/伪造授权声明）。

### D3 — Redis active_turn 实例前缀 + 启动清扫

值格式 `instance:turn_id`（host 维度 / `WEBGIS_INSTANCE_ID` 覆盖；旧裸
格式兼容读取 + owner-checked 双格式注销）；lifespan 启动
`sweep_own_keys()` 只清本实例前缀 key（多 pod 不误杀）。崩溃残留窗口从
≤15min（TTL 兜底）压到启动即清。

### D4 — vendor pin v1.5.1 → v1.5.2（pin-only）

按兼容矩阵 §3 清单全量实跑（doctor 六域/契约 13/旅程 J1–J8）后升级；
消费面（C1–C16）零漂移（v1.5.2 仅 interactive UX/导入清理/版本串）。协议
floor 保持 v1.5.1。回退 = gitlink + `--reinstall` + 复跑（A23 演练）。

### D5 — 观测修复（N4）与反 vacuous 纪律

J7 计数 separators 对齐 node 紧凑输出；新增
`llm_calls_observed_before >= 1` 反 vacuous 锁——任何「零调用」类断言必须
先证明计数器看得见调用。

## 后果

- 迟到取消（A07/A09）与迟到回调（A20）在宿主侧闭环；vendor 侧无需
  turn-scoped API（上游演进后可替换 D1 的门为原生命令）。
- 恢复目录把「压缩/重启后仍守住用户约束」从提示词期望变成投影事实
  （A13–A18）。
- 测试观测面升级：写出时刻身份快照（ProbeRpcClient）成为取消类断言的
  标准口径。
