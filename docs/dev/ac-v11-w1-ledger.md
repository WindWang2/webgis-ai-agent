# AC-V11 W1 交付台账（意图与配方的可学习化）

> 波次:W1 · ADR-0161 · 状态:已完成 · 回滚点:tag `ac-v11-w1`

## 交付清单（任务 → 文件 → 测试 → 证据）

| # | 任务 | 文件 | 测试 | 证据 |
|---|---|---|---|---|
| W1.1 | 意图证据库 | `migrations/versions/0057_cartography_intent_learning.py`（领号登记 .alloc.json）、`app/models/intent_learning.py`、`app/services/cartography/intent_learning.py`（record/query/replay + capture 缝）、`app/services/chat/plan_orchestrator.py`（主路径缝）、`app/services/gis_harness/intent.py`（adaptive 缝 + session_id 参数） | `tests/cartography/test_intent_learning.py`（13 ✅：roundtrip/replay diff 空/生产缝/断库 fail-safe） | 裁决可查询回放；plan_orchestrator/harvest 回归 17 ✅ |
| W1.2 | 反馈信号闭环 | 同上服务模块（FEEDBACK_SIGNAL_TYPES 词表 + record + effective_weight 半衰期） | 信号衰减 4+0.5 精确断言、词表闭集拒收 ✅ | 闭环端到端（记录→衰减聚合→亲和共用） |
| W1.3 | 配方 fallback 链学习 | `recipes.py`（resolve_fallback_chain +affinity 先验，同 priority 并列生效）、`planner.py`（_chain_affinity_prior fail-safe）、`memory_harvest.py`（success 记账缝） | 权重公式/重排不增删候选/链求解先验改道+落选留痕 ✅ | 无先验行为逐字节不变（回归 52 ✅） |
| W1.4 | 记忆置信度/过期 | `app/models/project.py`（只增列 confidence/expires_at）、`project_memory.py`（record_fact 写入 + get_active_facts 过滤） | 过期记忆不注入 + 置信度持久 ✅ | 既有字段零改动 |
| W1.5 | 语料 300→1000 | `tests/cartography/expand_intent_corpus.py`（幂等生成器）、`fixtures/intent_corpus_v11.jsonl`（1000 条） | `tests/cartography/test_intent_corpus_v11.py`（8 ✅：形状/族覆盖/错拼标注/overall≥0.7433 实测 0.948/fallback<0.25 实测 0.0354/en≥0.9×zh/byte 重放） | 300 条 +8pt 门禁与 204 闭环矩阵原样全绿（66 ✅） |
| W1.6 | 澄清台账 | 服务模块 clarification_metrics（命中率/误触发率聚合） | 台账三态断言 + 空库诚实 ✅ | 10 条模糊种子 100% 触发保持 |

## 验收对照（任务书 W1 验收项）

| 验收项 | 状态 |
|---|---|
| 意图证据可查询回放 | ✅ record/query/replay（同引擎回放 diff 为空 = 确定性） |
| 反馈信号闭环端到端测试 | ✅ 词表→记账→半衰期聚合→亲和权重（共用存储） |
| 1000 条语料命中率 ≥ V10 基线 +5pt | ✅ 0.948 ≥ 0.7433 |
| 既有 204 条无劣化 | ✅ test_closed_loop_corpus_v6 全绿（矩阵无洞） |
| 记忆项有过期与置信度 | ✅ 增列 + 读取过滤 + 写入参数 |

## 首轮数值（与 W0 对照）

| 指标 | W0 | W1 | 备注 |
|---|---|---|---|
| 语料规模 | 300 | **1000**（zh 600/en 400） | 任务书 W1.5 |
| 语料 overall | 0.7733+（300 条门禁） | 0.948（1000 条）/ 0.6933+5pt 门禁 | 扩容未稀释命中率 |
| fallback_rate | <0.25（300） | 0.0354（1000） | 兜底纪律保持 |
| 学习表 | 0 | 3 表 + 2 增列 | 0057 |
| 亲和对排序的影响 | — | 仅同 priority 并列 | 先验非证据红线 |

## 遗留与移交

- 澄清台账报表落盘（docs/dev 看板段）→ W8 成本/质量看板统一。
- `rephrase`/`palette_change` 信号的真实用户行为接线（chat 路由侧）→ W5/W7
  （当前服务面 + 测试闭环已具备；W1 不改用户可见行为）。
- 亲和权重消费扩展到 select_candidates 排序 → W7 自愈策略库同批评估。
