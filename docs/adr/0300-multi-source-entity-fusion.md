# ADR-0300：多源空间实体融合工作台（可证明匹配 · 冲突审阅 · 不可变融合版本）

- 状态：Accepted（/goal 03｜多源空间数据融合与可信数据环境）
- 日期：2026-11-25
- 关联：ADR-0286（G03 数据资格判定链）、G10（项目数据刷新 · 影响面 ·
  采纳）、G17（staged 导入）、G18（ref 驱动多格式交付）、G38（数据关联
  作者工作台 —— 职责对照见下）、G44（业务字段契约）、G45（可固定坐标
  操作）、PR #1868（多源准备/语义绑定）

## Context

仓库已有数据「获取—准备—关联—交付」链，但对**同一地理实体的多来源
观测**（两个机构的 POI 名录、两年行政区划、修测前后的地块）没有生产
语义：谁和谁指同一实体、冲突字段信谁、人工裁决如何持久、融合产物
如何作为可追溯数据集版本被既有链消费。G38 关联是**以左表为骨架的
属性富集**（left/inner join，一行进一行出，不做实体消歧、不产出融合
版本）——不是本域。数据目录/刷新/join 均不重复实现。

## Decision

### D1 融合计划是唯一语义契约（`data_fusion_authoring.plan.FusionPlan`）

封闭词表 + typed 错误码 + 预算常量唯一登记于 `contracts.py`；
`normalized_digest` 覆盖全部执行语义自由度（源锚/许可/字段声明与映射/
逐字段裁决规则/类别词表映射/证据权重/阈值/几何采纳/提交政策/预算）。
预览、执行、增量刷新共用同一 digest —— 计划或源漂移即失效。
**共享标识空间显式声明**（`id_space`）：external_id 证据只在两侧声明
同一 id_space 时可比；不同源各自的内部编码（教育局码 vs OSM id）绝不
静默比较（无 id_space 时该权重必须显式置 0）。

### D2 匹配是可解释证据评分 + 歧义保护（`matching.py`）

- 三通道 blocking（ID 桶 / 空间格网（地理帧 x 键 360° 回绕 + 东西向
  动态 halo ±⌈1/cosφ⌉，|φ|>75.5° 钳制披露）/ 名称桶）并集生成候选，
  候选总量与单实体候选数有界（溢出强制审阅，绝不静默丢弃）；
- 证据五类（external_id/name/address/category/distance）逐项打分入档；
  **双缺失证据跳过并重归一（记录 renormalized），单侧缺失按 0 计**；
  category 字段任一源未声明时整体跳过重归一，已声明但词表外按计划
  声明的 unknown 中性系数计（comparison="unknown" 如实披露）——
  缺数据不当零、不当满分、不算幽灵分；
- 状态机：互最优 + 边际（ambiguous_margin）+ 阈值（review_min ≤
  auto_match_min）+ 硬阻断（id 冲突恒不自动合并）+ id 钉定（共享
  id_space 精确匹配 = 最强身份证据，优先排序、距离仍在界内可自动
  合并）→ matched / needs_review / ambiguous / unmatched / rejected；
- 距离口径：地理 CRS 用 pyproj.Geod(WGS84) 大地距离；投影 CRS 用
  平面距离（`crs_safety.classify_crs` 闸：仅 geographic 与
  projected_local_metric 可比，否则 typed 提示走 G45）；
- 名称字段冲突检测与匹配证据同一归一化口径（计划声明 remove_bracketed
  时「X（Y校区）」与「X」是同值）—— 证据与裁决绝不两套语义。

### D3 冲突不可被覆盖，只可被显式裁决（`adjudication.py` + `review.py`）

- 逐字段确定性规则（same_value / trust_priority / newest_temporal /
  keep_multi / manual）+ 未决（unresolved）—— unresolved 输出缺失并
  进审阅队列，绝不静默择一；
- 人工决定挂 **plan 级**（非 run 级），键 = 两端**实体内容指纹**
  （`fp:<fa>:<fb>`，与快照 index 无关）：来源刷新后按内容重挂靠；
  CAS revision 乐观并发（stale_review_revision 拒绝不覆盖他人）；
  `locked` 锁定的唯一推翻路径是显式删除决定；
- 几何采纳封闭政策（primary_source / newest_temporal / manual），
  绝不自动 dissolve；未采纳侧几何与双侧原值全量进 `_fusion`
  provenance。

### D4 两段式运行：匹配 run 与受权 commit（`executor.py` + `service.py`）

- run = 源冻结复核（G18 resolve 同一授权路径）→ 匹配 → 匹配包冻结
  （含双侧完整实体指纹键集）；幂等键 = session+plan+digest+双源指纹，
  收敛规则与 G38 同款（收据短路 / 活跃收敛 / 世代键）；
- commit 是显式受权步骤：require_resolved 门（未决审阅项 typed
  阻断）/ 源指纹复核（漂移 → stale_source，决定自动继承到新 run）/
  组装 → staging → 原子发布 → 文件字节哈希入收据 → DataObject CAS →
  session 台账 `ref:data-fusion/<run_id>`；
- 融合要素输出标记 origin ∈ merged / single_a / single_b /
  withheld_review：未决审阅对两侧暂按单源输出并披露（质量报告计数）
  —— 融合版本中同一实体可能出现两行直到审阅完成，这是诚实边界，
  不假装已合并。

### D5 增量维护以内容指纹为锚（`incremental.py`）

来源刷新 → 键集合 diff（added/removed，基底包冻结完整键集）→ 影响
面占比；超安全阈（35%）typed `refresh_not_safe` 要求显式全量重算。
决定三态预测：inherited（两端仍在）/ stale（一端变，保留待复审）/
orphan（悬空不应用）。锁定决定绝不无声推翻。

### D6 质量是数据特征与验证事实（`executor.build_quality_report`）

全部真实计数（覆盖/匹配分布/硬阻断/候选溢出/未决/未决字段/来源
信任声明与理由/许可署名），每项给「适用/需审阅/不适用」可解释依据；
无单一神秘评分；样本与总体区分（资格卡时间解析率标明「前 50 行样本」）。

## 职责对照（为什么不另建/不重复）

| 域 | 职责 | 与本域边界 |
|---|---|---|
| G38 关联 | 左表骨架属性富集（join） | 不消歧、不产融合版本；本域产出可作其输入 |
| G10 刷新 | 项目数据版本感知/影响/采纳 | 本域消费其语义；融合自身的增量匹配在本域 |
| G18 交付 | 多格式成果交付 | 融合成果经既有交付链导出（附许可/署名/冲突披露） |
| G45 坐标操作 | 可审阅/可固定重投影 | CRS 不可比时本域 typed 拒绝并提示 G45，不自带变换 |
| G44 字段契约 | 字段业务契约/录入校验 | 融合输出字段可被其约束；本域不重复校验器 |

## 规模与验证证据（本地实测，2026-11-25）

- 14.4k×14.4k 受控网格（±5m 抖动）：真配对 **100% 召回**（blocking
  不漏关键候选）、wall time **4.55s**、tracemalloc 峰值 **260MB**
  （=248MiB，域预算 256MiB 内；halo 动态扩展后候选 +54% 仍在界）；
  计时与内存分开测（tracemalloc 每分配追踪放大 wall time ~35x，
  不可混测）。
- brute-force oracle 单测：引擎候选集必须覆盖全部 score≥review_min
  或 id 钉定对（blocking 无关键候选丢失由 oracle 证明，非自证）；
  反子午线（±180° 21.9m 近邻）与 65°N 东西向跨格对有独立回归钉。
- 验收旅程（tests/unit/data_fusion_authoring/test_service_flows.py，
  真实 DB）：成都小学别名/括号校区/冲突地址/独有实体 → 预览 → 审阅
  （CAS/锁定）→ require_resolved 门 → commit → 唯一性可验证融合版本
  （provenance 完整）；拒绝保持分开；allow_unresolved withheld 语义；
  源漂移 stale_source；刷新影响面 + 锁定继承 + refresh_not_safe；
  跨会话 not_owned。

## 后续集成点（不在本 PR 范围）

- 融合成果作为 G10 可刷新项目数据资产的晋升接缝；
- 面实体「仅关联」模式与 G38 空间关联的语义对齐文档；
- N 源（>2）链式融合的计划组合 UI（当前以「融合成果作为下一计划源」
  组合，pairwise 诚实语义）。
