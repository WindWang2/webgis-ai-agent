# ADR-0217: 视觉观测证据生命周期与安全修复闭环（C13）

- 状态：Proposed
- 日期：2026-09-29
- 关联：ADR-0214（F15 视觉观察/批评/修复 provider 与用户批准修复）、
  ADR-0186（视觉自愈事务）、ADR-0209（验证→批评→修复闭环收口）、
  ADR-0205（Cartographic Grammar / W9 边界）

## 背景

F15（ADR-0214）交付了 provider-neutral 观察契约、封闭 taxonomy、跨域
融合、两步用户批准修复与 recurrence 硬停，并把四个硬化项记入
Out of Scope：前端截图采集、跨会话 blob refcount/GC、blob 键会话绑定、
AUTO_SAFE/审批的机器可测分级。C13 在最新 master 上收口这些项并补齐
证据新鲜度语义。

## 决策

### D1 — 旧观测不得驱动新地图（stale 双门，默认强制）

- **第一道门（管线）**：`latest_screenshot_for(session, revision,
  fingerprint)` 严格匹配 —— revision 必须相等、指纹双方非空必须相等；
  不匹配 → None（诚实缺席 `no_screenshot`）。F15 的「滞后一拍」回退
  是跨代误修复入口，予以移除。
- **第二道门（provider，纵深）**：`_eval_rules` 校验 screenshot 与观察
  的 revision/指纹；不一致 → `screenshot_revision_mismatch` /
  `screenshot_fingerprint_mismatch`，不跑像素判据。
- **证据新鲜度**：`UnifiedFinding.observed_revision`（additive）在落
  map_product 前盖章；plan/auto 修复面只消费
  `observed_revision == 当前 mutation revision` 的 finding（旧数据缺章
  保守按过期）。终验后任何 mutation 都使视觉证据过期 —— 用户手改、
  agent 动作、修复本身一视同仁。

### D2 — 截图 blob 生命周期：引用计数 + 会话绑定 + sweep GC

- 引用索引 `vref-<sha>`（JSON，≤64 会话/blob）与字节同库存放，经既有
  `BlobStore` 协议读写 —— 可插拔后端（本地文件 hermetic；对象存储
  适配器 = 换协议实现 + 自带 sweep），不引入网络依赖。
- 并发语义（诚实双方向）：读改写 last-writer-wins —— 丢减量 → 泄漏
  （租约 sweep 可回收）；丢增量 → 过早回收，消费侧 `resolve` 得到诚实
  缺席（内容寻址无错配风险，可用性损失但绝不产生错误数据）。
- FIFO 淘汰 / `clear_session` 只释放本会话引用；最后引用消失才删字节；
  引用非空但 vref 租约整体超龄（默认与字节 TTL 同宽 7 天）→ 按陈旧
  租约回收（长会话的 live 字节因此可能被回收 → 诚实缺席，地图推进后
  下一轮观察自动补拍）。
- 解析护栏：vref 在场而请求会话无引用 → 诚实缺席（跨会话猜 ref 读取
  不可行）；vref 缺席 = 旧数据放行。
- 上传通道维持有界纪律（PNG 魔数 + ≤4 MiB + ref-only 回执）；前端另加
  三重受控门（revision 变化 + 30s 最小间隔 + 每会话 ≤8 与后端 FIFO
  同宽）防截图洪泛。

### D3 — AUTO_SAFE / NEEDS_APPROVAL 边界（机器可测）与保守默认

- 纯函数分级 `approval_class_for`：可映射 taxonomy + warning + 不触锁
  → `auto_safe`；severity=error 或触达用户锁定图层 → `needs_approval`；
  不可映射（无修复路径）→ `blocked`。
- **自动执行默认关闭**（`GIS_VISUAL_AUTO_REPAIR` 显式开启）：F15 语义是
  visual finding 恒不自动修复，默认开启属行为变更；开启后边界机器强制
  —— 只执行 auto_safe 分级、`origin="system"`（lifecycle 锁 guard 结构
  性拒绝触碰用户锁定图层 = user-pinned 决策不可被覆盖）、per-revision
  ≤1 + per-session ≤4 + healer 收敛账本三重 bounded、全有或全无。
- 高风险修复走 approval UI（plan/apply 既有两步），批准仍受 approved
  结构门槛 + CAS 约束。

### D4 — 拒绝记忆（user-wins 的延续）

- 用户拒绝提案 → 决策账本按 finding 的 `recurrence_fingerprint`（原因
  身份）持久（会话内）：plan 与自动通道都不再为同一缺陷索要同一 patch；
  缺陷证据变化（新观察、新指纹）后自然放行。approved 撤销同键的更早
  rejected（最新裁决获胜）。被拒提案再 apply → 409（结构门槛，非 UI
  礼仪）。

### D5 — VisualRepairIntent codec：typed 意图，不进 body codec

- `compile_intent / intent_to_dict / intent_from_dict /
  intent_fingerprint` 服务 plan/apply/决策账本/复验之间的持久化与传输
  面；round-trip 与指纹稳定性机器锁定。
- **有意不加入 `intent_codec.body_to_intent`**（延续 F15 决策）：body
  codec 是 HTTP mutation 直提通道的单一映射源，加入即在 approval 结构
  门槛之外开第二个 apply 入口。因果溯源由既有 mutation_id
  （`vrepair:<pid>` / `vauto:<fp>:<rev>`）+ `_mutation_dedup` 幂等 +
  决策账本共同承担。

## 后果

- 闭环收紧：截图上传（观察接受后自动）→ 上传触发复验 → 同 revision
  终验拿到新鲜像素 → findings/自动修复 → 再观察。旧证据在每一层被
  结构性挡住。
- 兼容：全部 additive（新字段缺省值、新端点、新 env 开关）；未配置
  evaluator / 未开启 AUTO_REPAIR = F15 行为逐字节保留。
- Out of Scope：对象存储适配器的具体实现与 sweep 任务化（接口已备）；
  决策账本的跨 pod 合并（与会话 map_state 同域，单写者）；视觉修复的
  undo 专面（checkpoint/rollback 已覆盖）。
