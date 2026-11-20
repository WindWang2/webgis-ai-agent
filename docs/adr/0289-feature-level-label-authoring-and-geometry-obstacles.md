# ADR-0289: 要素级标注 authoring 与带身份几何障碍（G16）

- 状态：Accepted（G16-AUTHORABLE-LABELS，schema v2.1 additive）
- 日期：2026-11-11
- 范围：MapSpec `layer.label.authoring`、`layout.labels.obstaclePolicy/forbiddenZones`、
  确定性求解器（双孪生）、live MapLibre 投影、UI/Pi 双入口、导出链

## 背景

自动标注（#1265/#1875/#1975）解决"选字段、抽稀、避让、导出"，但用户对
单个标签的明确意图（"保留这三所学校、把这两个分开、隐藏重复名、别压
主干道"）没有持久表达：无 per-feature 覆写模型、线/面墨迹不参与避让、
UI 与 Pi 各说各话。障碍侧只有匿名 circle AABB（保留前 200），长线整线
bbox 会禁满画布，同源图元与标签的关系靠猜。

## 决策

1. **覆写模型**（`MapSpecLayerLabel.authoring`，≤256/层）：身份 = 层嵌套
   （layer.id+source 隐式）+ `featureId`（FEATURE_ID_KEYS 链 / `_wfid`
   同源，`str()` 归一 —— `1` ≡ `"1"`）。字段：hidden/pinned/offset/text/
   callout/priority + 溯源（origin/sourceRevision/frameId/note）。写路径
   盖章溯源，patch 不可覆写。**单一语义权威** = `app/lib/cartography/
   label_authoring.py`（纯函数）+ TS 消费孪生；reset 作用域互斥
   （visibility/position/text/all），解除隐藏绝不顺带清文字或位置。
2. **canonical 单位 = em**（相对标签自身字号）：live `text-offset` 原生
   em；导出 `em × font_px`（font_px 已含 dpi/72）—— 偏移与字号同乘
   dpi_scale，DPI/缩放不改变相对位置语义，绝不持久化绝对屏幕像素。
3. **原子写路径** `PatchLayerLabelAuthoringIntent`（registry 模式）：
   UI 手势与 Pi 工具同一意图/CAS/撤销栈/user-wins；任一 op 拒绝整笔失败
   （per-op findings）；inline 源逐目标身份核验，裸数据集（无 id 键）拒绝
   持久覆写（feature_identity_missing）；ref 源放行待消费侧权威复核。
4. **求解器融合**（`label_collision.py` ↔ `label-solver.ts` 孪生）：
   hidden → suppressed(hidden_by_user)（不占预算）；pinned 先于全部自动
   项放置（user-wins：恒放置，冲突置旗 + stats 分型，绝不自动移动）；
   user_priority 恒先于抽稀名次；无 authoring 输入逐字节 legacy。
5. **geometry_v1 障碍策略**（显式声明才生效；缺省 circles_legacy 逐字节
   不变）：线逐段 AABB（实际 stroke 宽 + 视口预筛 + 段采样，长线绝不
   整线 bbox）；面**边界**墨迹（外环；填充不是障碍 —— 区内标签可放，
   禁入面走 forbiddenZones 显式声明）；circle 补身份（恒不豁免自身 ——
   点符号避让是设计）；自身线/面墨迹豁免（OWN_ANCHOR_EXEMPT_KINDS，
   同 layer+feature 才豁免，跨层绝不）；分组公平交错预算（zones →
   components → 图层轮转 → circles，截断如实披露 —— 后层关键墨迹不被
   首层静默挤掉）。
6. **live 投影**（runtime）：auto 子层 text-field/filter（隐藏+pinned
   排除）/text-offset 按**解析键分组** match（顶层 id + 属性键 ≤3 组）；
   `${id}-label-pinned` 子层（text-allow-overlap 恒显示、主层 filter 同
   步、置于 auto 之下先放置）；`${id}-label-callout` 引线层（合成源 +
   moveend 刷新 em 屏幕语义）；orphan 不投影 + 事件披露。live MapLibre
   与离线确定性求解存在实现差异 —— 统一的是身份/文字/优先级/锁/隐藏/
   单位语义，不承诺任意视口逐像素相等；用户固定项在两端保持一致位置
   语义（em canonical）。
7. **失效**：orphan（目标要素不存在 → 不应用、绝不漂移）/ stale（revision
   变化但身份未断 → 照常应用 + 待复核）/ unverified（数据不可复核）；
   消费侧（live/export/inspect）是权威判定点。

## 后果

- 旧 spec 零新增键 → 行为逐字节不变（golden corpus + 双孪生 fixture 锁定）；
- 诊断词表 +5（authoring_applied/orphan/stale、pinned_conflict、
  obstacles_budget），EMITTER_REGISTRY 登记（死码门）；
- "为什么没显示"只给有来源的理由（authoring_policy/data/identity），
  placement 族显式 not_evaluated —— 离线预测不冒充 live 观察；
- 2.0 号位留给 G15（恒等上船），本线 2.1；合并顺序任一均收敛。

## 备选与拒绝

- **spec 级覆写总表**（layout.labels.authoring）：跨层混写风险、双读点；
  拒绝，层嵌套使身份在构造上唯一。
- **持久化绝对像素偏移**：缩放/DPI 漂移；拒绝，em canonical。
- **artifact 引用承载超量覆写**：当前无消费者，悬空引用即谎言；改为
  有界拒绝（authoring_limit_exceeded）+ 分批工具化。
- **求解器内隐式 exemption radius**：与既有「调用方排除自身锚点」约定
  冲突且不可测；改为带身份显式豁免（新策略下生效）。
