# REVIEW_R1 — 独立逐行审查（2026-11-28，独立上下文子代理，非作者）

审查范围: origin/master...HEAD 全 diff + 邻接权威代码（flow_product service/mutation、session_data、mutation registry/schema）。
审查者自跑验证: tests/unit/moving_object 78 passed；共享面契约 127 passed；parity 74 passed；前端 16 passed；**反经线 bbox 预筛 600 次差分模糊测试 0 错配**。

## 结论

科学核心（测地、区间语义、事件决策表、反经线处理、预筛无假阴性）判定 **sound 且诚实披露**；无科学级 blocker。Ship-blocker 为 2×P1。

## 发现与处置

| # | 级别 | 位置 | 问题 | 处置 |
|---|---|---|---|---|
| R1-1 | P1 | service.py `_build_product_payload` | typed 拒绝漏出为裸异常 → REST 500（RegionGeometryError/SegmentCapExceeded/MovingObjectError caps/enum ValidationError） | 已修: 整体包装翻译为 TrajectoryServiceError（TRAJECTORY_REGION_INVALID / TRAJECTORY_SCALE_EXCEEDED 等）+ config 枚举 Literal 化 |
| R1-2 | P1 | service.py family_id | family 派生自窗口化内容 → 换窗重应用堆叠图层族（违背"原子替换同族"承诺） | 已修: family=稳定绑定身份（dataset ref + 实体/时间字段 + regions ref）；J2 测试改为断言 A 族图层被替换 |
| R1-3 | P2 | dwell.py | 驻留时长桥接无界观测间隙（2 修复点 30 天 = "驻留 30 天"） | 已修: DwellConfig.max_gap_s 时间断点（超限拆分游程）；区域级 dwell 同步；参数与披露随结果 |
| R1-4 | P2 | contracts/events n_entities | n_entities 语义与实现矛盾（cross-only 实体计入），分母不闭合 | 已修: n_entities=有 inside 修复的实体数；n_entities+zero_hit==n_analyzed 校验进测试 |
| R1-5 | P2 | ingest SortPolicy.KEEP_INPUT | 策略死代码：下游全部规范化重排序，披露语为假 | 已修: 移除 keep_input，摄取恒规范排序（乱序计数仍披露），契约与文档一致 |
| R1-6 | P2 | service.py 指纹 | 全量 feats json 序列化在事件循环上跑（500k 行秒级卡顿） | 已修: 移入 asyncio.to_thread |
| R1-7 | P3 | service.query kind=event | total 报样本大小（200）而非事件表 | 已修: envelope 增 total_is_sample=true 披露 |
| R1-8 | P3 | service 警告 vs builder | "零路径段不画"为假（实际发出不可见要素） | 已修: builder 真正过滤零路径段（计数保留） |
| R1-9 | P3 | service.TrajectoryProductStale | 死代码 | 已修: 删除（stale 拒绝在 mutation 层） |
| R1-10 | P3 | presentation.compute_speed_domain | "distinct"语义用 count 实现；[v,v+1e-9] 伪造梯度 | 已修: distinct 值判定 |
| R1-11 | P3 | events region_summaries unknown_intervals | 全局计数重复盖每个区域行 | 已修: 按 (entity,region) 追踪 |
| R1-12 | P3 | events RegionSet._tree | 时间耦合 + 变异调用方输入 | 已修: 正式字段，load_regions 构建TREE；interior 仅 open 规则计算 |
| R1-13 | P3 | event_features | 先截断后过滤 cross（5000 cross 可挤掉有位事件标记） | 已修: 先过滤后截断 |
| R1-14 | P3 | 杂项 | interval_motion 未用参数、__init__ 文档 aggregate 不存在、面板死 JSX、frontend trajectoryConfigFromApplied 读不存在的 config 键、图例"灰="无对应 paint 分支、time_start/time_end 不可解析静默失效、oracle approach 腿方向、cap >/>= 不一致 | 已修: 全部逐项清理；window 边界不可解析改为 typed 拒绝；oracle approach 腿改为自西向东进站 |

## 审查判定 CLEAN 的面（摘要）

ingest 时间解析/DST/排序/去重、测地常数、dt=0 无假零、断点规则、事件区间语义、closed/open、洞/多面、反经线三帧+预筛、quality 分离、缓存/STALE/跨会话拒绝、mutation 原子性（先验证后计划）、权限（4 路由 require_owned_session + session_id server 注入）、跨端契约（schema/generated/registry codec/前端 API/i18n 63 键 parity）、性能无 O(n²)、测试无同义反复。

## reviewer 附注

- 工作区 corpus json 重序列化：为 sed 版本期望更新的规范化产物，已随提交入库（非测试运行时改写）。
- golden 刷新漂移指纹与 2.3 bump 一致性已由 reviewer 复核确认。
