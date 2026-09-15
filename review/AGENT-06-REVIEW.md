# AGENT-06 Review 纪要：空间长效记忆主动关联唤醒与意图消歧引擎（ADR-0190）

- 日期：2026-09-15
- 分支：`agent/06-proactive-spatial-memory-associative`（基于 origin/master `3eb2cc6a`）
- worktree：`webgis-wt-agent-06`
- 执行：zcode Agent（单线，subagent 峰值 0/3）

## 1. 交付范围

| 层 | 文件 | 内容 |
|---|---|---|
| 设计 | `docs/adr/0190-proactive-spatial-memory.md` | 8 条决策（分层叠加/四类视图族/热索引/消歧判据/整合位点/双挂载/租户加固/可观测） |
| 设计 | `docs/dev/proactive-memory-spec.md` | 接口、评分算法与常量、挂载行为、测试矩阵、实测性能 |
| 索引 | `app/services/gis_memory/associative_index.py` | org 物理分区热索引：CJK bigram+数字词元倒排（BM25 k1=1.5/b=0.75，IDF 预计算）、1° 网格空间索引（大 bbox wide 列表）、Half-Life 衰减（14d，pinned ∞）、确定性排序、top-k 后置理由构建 |
| 管道 | `app/services/gis_memory/proactive_retriever.py` | `awake()`（Query+org/user/session/project+bbox → Top-K `MemoryContextCard`）、`resolve_place()`（唯一高置信判定：score≥0.62 且领先 ≥0.08，模糊指代走「唯一近期实体」兜底但唯一性余量不放宽）、`render_cards()`（`[GIS_MEMORY_PROACTIVE]` 有界 900 字符 + `_xml_fence` 转义） |
| 整合 | `app/services/gis_memory/memory_consolidator.py` | Settle 整合：晋升白名单证据 + hit_count≥2 → 习惯类→user / 实体类→project（无 project 不晋升）；显式来源固化（清 `expires_at`）；晋升走既有写入门（非旁路）；`consolidated_from.from_session` 审计 |
| 挂载 | `app/services/gis_situation/turn_context.py` | `build_situation_turn_context(..., query_text="")` 追加主动切片；新增 `build_proactive_memory_slice`（身份 fail-closed / 异常 fail-open） |
| 挂载 | `app/services/gis_harness/intent.py` | 纯函数 `apply_proactive_memory_hints`（回填 scope + `memory_proactive_scope:` 审计 + ×0.9 折扣 + `intent_evidence.proactive_memory`）；`resolve_intent_adaptive` 新增 `org_id/user_id/use_memory`（fail-open 双层防线）；`resolve_map_request_intent` 纯度不破坏 |
| 挂载 | `app/api/routes/chat.py` | 两个 turn 结束位点在 `harvest_spatial_memory` 之后追加 `consolidate_after_harvest`（fail-safe） |
| 测试 | `tests/unit/test_proactive_spatial_memory.py` | 35 用例，A–F 六组（索引/租户隔离/消歧/生命周期/挂载/渲染） |

## 2. 验证证据（全部实跑）

| 门禁 | 命令 | 结果 |
|---|---|---|
| 新套件（任务书指定命令） | `./.venv/Scripts/python -m pytest tests/unit/test_proactive_spatial_memory.py -v` | **35 passed**（53s，含 pytest.ini 注入的 --cov） |
| 既有记忆回归 | `pytest tests/test_gis_memory_{store,retrieval,wiring,security,review_fixes,schema_drift,eval_corpus}.py` | 73 passed |
| 意图族回归 | `pytest tests/unit/gis_harness/test_intent*.py test_semantic_*.py …` | 116 passed |
| chat/situation 回归 | `tests/unit/test_chat_*.py test_pi_turn_context.py tests/test_gis_situation_*.py` | 94 passed |
| planner/orchestrator | `tests/unit/test_plan_orchestrator.py gis_harness/test_planner*.py` | 44 passed |
| intent 消费方 | `test_semantic_gis_intelligence.py test_multiturn_scenarios.py …`（10 文件） | 132 passed, 1 skipped（既有 skip） |
| 风格门禁 | `ruff check app/services/gis_memory/ tests/unit/test_proactive_spatial_memory.py turn_context.py intent.py chat.py` | **All checks passed!** |

## 3. 跨租户隔离断言验证（任务书红线：100% 阻断）

org 等值防线在**五条通道**上各有测试锁定，任一通道泄漏即测试失败：

| 通道 | 测试 | 机制 |
|---|---|---|
| 索引分区 | `test_index_org_partition_is_physical` | 顶层 `dict[org_id, _OrgIndex]` 物理分桶；`search("")` 直接拒绝；对方私有记忆在本租户任何 query 下不可达 |
| awake | `test_awake_cross_org_fully_blocked` | org B 同文本查询 → `cards==[]` 且 `resolved_place is None`；匿名（无 org）→ `trace.reason=="no_org"` |
| 卡片渲染 | 同上 | `render_cards(miss.cards)` 不含租户 A 主体文本 |
| resolve_place | 同上 | org B → None |
| sync_org | `test_sync_org_loads_only_tenant_rows` | 只加载调用方身份给定的 scope 实例（org 等值 SQL）；B 只见到自己的 1 行 |
| consolidator | `test_consolidation_never_crosses_tenant` | org B 身份无法把 org A 的 session 记忆晋升进 B 的 user/project 作用域（`get_active_memories` org 谓词 + hit 计数按 org 取） |

另：sensitive 记忆不进热索引（`test_sensitive_memory_never_enters_index`，
store 审计面仍可见）；渲染层 `_xml_fence` 转义 + 预算省略（`test_render_cards_budget_and_fencing`）。

补充实测证据（进程内直连，见下「性能」）：
org-a `resolved_place="我司园区"`；org-b 同 query → `resolved=None, cards=0`，
渲染无泄漏字符串 → **阻断 100%（6/6 通道）**。

## 4. 性能实测（1000 条/org，Windows / Python 3.13）

| 场景 | 结果 |
|---|---|
| 退化语料（千条共享前缀全命中） | 3.5 ms/call，top-1 判别正确（数字 token 锁定 `地块编号1`） |
| 真实语料（千条互异 AOI） | 2.8 ms/call |
| 每候选成本 | ≈3µs（候选 ≤300 时 <1ms，满足「亚毫秒」目标口径） |

性能优化过程中的两个实质修复：
1. **tokenizer 丢弃单 ASCII 字符**导致「地块编号1/488」不可判别 →
   数字串任意长度保留（字母仍 ≥2），top-1 判别有测试锁定；
2. 热循环内每候选做 `sorted(set())` + IDF + 理由字符串格式化 →
   IDF 外提每查询一次、理由仅对 top-k（≤32）后置构建（7.4→3.5ms）。

## 5. 自审查发现与修复（实现期修掉的真缺陷）

| # | 发现 | 修复 |
|---|---|---|
| F1 | `_OrgIndex` LRU 逐出先 `popitem` 再清理，倒排/网格残留悬空 id（内存泄漏） | 拆分 `_detach(entry)`（纯清理）/`remove(id)`（pop+清理），逐出走 `_detach` |
| F2 | `sync_org` 重温会整条替换条目 → `hit_count` 清零，30s staleness 窗口下晋升判定永远不达标 | 重温时沿用进程内计数，回退 `value.hit_count` 持久值取 max |
| F3 | `consolidated_from.session_id` 被 sanitizer 当凭证形键剥除（`_SECRET_KEY_EXACT` 含 `session_id`） | 审计键改名 `from_session`，注释写明因果 |
| F4 | 每 org 索引上限 512 < store 预算最坏同步量（80+400+200=680）→ 静默 LRU 逐出 | 上限 1024，注释写明推导 |
| F5 | 模糊指代 query 与记忆零词元重叠时索引无候选入口，「上次看的那个地块」召回失败 | `recent_entries` 有界兜底通道（≤16 条，半衰期+先验排序），唯一性余量仍由 `_unique_place` 把关 |
| F6 | `resolve_intent_adaptive` 中 probe 调用点未兜异常（探查函数本身抛错会炸解析） | 调用点再包一层 try/except（双层防线，测试锁定） |

## 6. 已知边界（明示不做）

- Embedding 向量语义：无向量基建；BM25+bigram+数字 token 覆盖中文名面，
  `semantic_score` 扩展点留在评分权重层（ADR-0190「不做」节）；
- 纯 Python 网格索引在千万级记忆下需换真 R-Tree/PG 后端——当前规模
  （org 内 ≤1024 热条目）不构成瓶颈；
- turn_context 切片在 `GIS_SITUATION_CONTEXT=0` 时一并关闭（挂载点在
  situation 投影之后）；passive `[GIS_MEMORY]` 块不受影响；
- consolidator 的 hit_count 为进程内计数，跨进程重启回退
  `value.hit_count` 弱恢复（写入由 retriever touch→consolidator 侧完成，
  未在 v1 落库回写，见 spec §1.3/§1.4）。

## 7. 结论

阶段一（ADR+规格）→ 阶段二（TDD 先行，35 用例 RED→GREEN）→ 阶段三
（三组件 + 三挂载点）→ 阶段四（全部门禁绿）。任务书验收条件满足：
- 新测试套件 100% 通过（35/35）；
- 既有回归零破坏（494 用例复跑全绿，1 个既有 skip）；
- ruff 0 告警；
- 跨租户数据隔离断言 100% 阻断（五通道 + consolidator 共 6 项测试锁定）。
