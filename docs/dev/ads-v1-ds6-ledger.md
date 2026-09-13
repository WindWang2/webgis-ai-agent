# ads-v1 · DS6 交付台账（任务 → 文件 → 测试 → 证据）

> 波次：DS6 · 时间与语义维度解析 · ADR-0176 · 里程碑 M4（与 DS7 同车）
> 状态：**完成** · 2026-09-13

| 任务 | 文件 | 测试 | 证据 |
|---|---|---|---|
| 相对时间解析器 | `app/services/data_fabric/semantic/time_parser.py`（TimeRange，now 可注入，确定性） | `tests/unit/test_data_fabric_semantic.py`（11 测）+ `tests/data/test_ads6_time_eval.py`（2 闸） | 相对/锚定/绝对/季度/对比全词表；解析失败返回 None；月份窗口月首对齐（文档化） |
| 地理粒度解析 | `semantic/granularity.py`（D1 词表双语别名；`省市` 复合词 → city） | `test_granularity_vocabulary_is_canonical` / `test_parse_granularity_bilingual` / `test_normalize_granularity_idempotent` | 未知 → None 不猜级别 |
| 度量语义识别 | `semantic/field_roles.py`（time/geo/measure/id，复用 temporal.profiler 缝） | `test_infer_field_roles_precedence` / `test_infer_field_roles_empty_is_honest` | 单主角色优先级 time > geo > id > measure；profiler 失效回退名字启发 |
| slot 契约（与 V11 切分） | `semantic/slots.py`（冻结词表 + `to_intent_slots`） | `test_slot_vocabulary_frozen_shape` | 两侧互不 import；仅加字段式演进；文档见 ADR-0176 §5 与模块 docstring |
| 检索层打通（端到端） | 解析 → `RetrievalFilters`（temporal/granularity）→ DS2 检索 | `test_parsed_query_feeds_retrieval_filters` | 「近五年按省汇总的PM2.5」→ year 范围 + province 粒度进入实时检索 |
| 评测集 ≥150（双语） | `tests/data/ads6_time_eval.json`（152 条）+ `scripts/ads_gen_time_eval.py`（生成时逐条自检标签） | 闸测试（n≥150 + accuracy≥0.90） | **accuracy = 1.0000**（阈值 0.90，provisional） |

## 波次验收对照（§6 DS6 行）

- [x] 150 条解析 ≥ 0.90（152 条 / 1.0000）
- [x] 双语全绿（中英样本同集评测）
- [x] slot 契约有文档与测试（ADR-0176 §5 + slots.py docstring + 契约测试）
- [x] 粒度解析与检索层打通（端到端集成测试）

## 备注

- 时间解析语义决定（文档化于 ADR-0176 §2）：近N月/季度窗口月首对齐；近半年 = 6 个月窗口；去年以来 = 去年年初至今；中文数字覆盖至「三十二」级。
- 无迁移；无 V11 模块改动（切分纪律）。
