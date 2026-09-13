# ADR-0176: ads-v1 时间与语义维度解析（取数意图；slot 契约与制图意图切分）

- 状态: Accepted
- 日期: 2026-09-13
- 线: adaptive-data-supply/v1-master · DS6（自适应数据供给与接入 · 并行线 P1）
- 关联: ADR-0172（检索层消费解析结果）、ADR-0170（D1.granularity 词表）、V11 intent_semantic（制图意图，本波零触碰）、`temporal/profiler.py`（字段角色复用缝）

## 1. 背景（缺口 A8）

「近五年/去年三季度/2015年以来」无法成为可执行过滤条件：生产代码零时间
表达式解析（仅闭环语料字符串命中）。地理粒度词（省/市/县/乡镇/街道/网格/
流域）同样无法映射到 D1.granularity。

## 2. 决策一：确定性时间解析器（`semantic/time_parser.py`）

- 纯正则 + 日期算术，**reference "now" 可注入**（测试钉住；生产默认 UTC
  今天）→ 同表达式同结果（DS3 重放纪律延续）；
- 词表：相对窗口（近/最近/过去/前 + N 年/月/周/天/季度；last N …；近半年
  = 6 个月窗口）、锚定区间（YYYY以来/及以后/since、截至/之前/until）、
  绝对（YYYY / YYYY-MM / YYYY年M月 / 完整日期 / 区间至到~/between）、
  季度（第N季度/Q1-4/上/本季度/去年三季度/上下半年）、年份对比
  （A年与B年对比 → 区间）；
- 中文数字全角覆盖（十二/二十/三十二…）；月份窗口对齐到月首（文档化语义）；
- **解析失败返回 None**——绝不静默放大区间；confidence 显式（显式模式
  0.9，裸年份 0.7），检索层以 <0.5 视为"无时间过滤"。

## 3. 决策二：粒度解析（`semantic/granularity.py`）

词表 = D1.granularity（country/province/city/county/township/grid/basin/
point，双语别名）；`省市` 复合词判定为 city（先于单「省」匹配）；
未知 → None（绝不猜级别）；`normalize_granularity` 幂等。

## 4. 决策三：字段角色推断（`semantic/field_roles.py`）

声明级（字段名/类型）启发 + **复用缝**：`temporal.profiler`（只加不改）
作为时间列证据的可注入判定器，失效即回退名字启发。角色集
{time, geo, measure, id}，单主角色（time > geo > id > measure）。

## 5. 决策四：slot 契约（`semantic/slots.py`，冻结）

- **与 V11 的切分（§8.1.4）**：intent_semantic 管制图意图，本包管取数意图；
  两侧**互不 import**，只通过 `to_intent_slots()` 的冻结词表对接：
  `dataset_candidates / time_range / granularity / measures / geometry_scope /
  clarification`——仅允许加字段式演进（契约测试锁形状）；
- 检索层打通（端到端）：解析出的 time_range/granularity 直接构造
  `RetrievalFilters` 进入 DS2 检索（有集成测试）。

## 6. 决策五：评测集（≥150，双语）

`tests/data/ads6_time_eval.json`：**152 条**（88 时间 + 64 粒度，中英双语），
由 `scripts/ads_gen_time_eval.py` 生成——生成器以钉住的 reference now
**逐条自检标签**（解析结果 = 标签才落盘），评测测试用同一 now 复验，
阈值 accuracy ≥ 0.90。首轮实测 **accuracy = 1.0000**。

## 7. 后果

- DS8 校准点：`_RENAME_SIMILARITY` 无关本波；confidence 阈值 0.5 与检索层共享；
- 时间/粒度解析进入检索过滤 → 计划 bbox/时间下推（DS3 步骤 time_filter
  消费 TimeRange）——「近五年 PM2.5」全链路可执行。
