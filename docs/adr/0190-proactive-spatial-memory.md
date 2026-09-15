# ADR 0190 — Proactive Spatial Memory Engine v1（空间长效记忆主动关联唤醒与意图消歧）

- 状态：Proposed
- 日期：2026-09-15
- 分支：`agent/06-proactive-spatial-memory-associative`
- 关联：ADR-0183（GIS Spatial Reasoning Memory v1——表、契约、写入门、
  passive `[GIS_MEMORY]` 注入）、ADR-0069（项目制图事实账本）、
  ADR-0139（V9 tenancy）、ADR-0150/0161（意图语义 / 可学习意图）、
  ADR-0180（Situation World Model）、迁移 0080（`gis_spatial_memories`）

## 上下文

迁移 0080 已建立 `gis_spatial_memories` 物理表（session/project/user 三
作用域 + 租户隔离），ADR-0183 交付了完整的写侧（evidence-gated 门、
supersede 链、预算淘汰）与一条 **passive** 读取通道（chat 路由每轮拼接
`[GIS_MEMORY]` 文本块）。但该表在实际交互流程中仍未被**主动激活**：

1. 用户输入「分析我司园区周边配套」时，Agent 无法联想「我司园区」对应的
   经纬度/范围——代词与惯用简称（上次/那个/本市/开发区）不会触发记忆检索；
2. 团队惯用的投影坐标系（地方高斯投影）、行业分类分级阈值，用户每轮反复交代；
3. 数据源历史失效经验（某平台某些字段频繁缺失）无法被后续会话主动借鉴。

既有 `retrieve_memories` 是 **被动谓词过滤**：只有 subject 字面匹配 + kind
权重 + 新鲜度阶梯，没有查询语义展开（BM25）、没有地理拓扑邻近、没有
连续时间衰减，也没有「模糊指代 → 高置信 resolved_place」的消歧语义。
注入通道只有一处（chat 环境块），意图理解阶段（`intent.py`）完全无感知。

## 决策

1. **引擎分层叠加，不建第二套记忆存储**。新组件全部落在既有
   `app/services/gis_memory/` 包内，持久层复用 `store.record_memory` /
   `get_active_memories`，类型真相复用 `contract.py` 的 10-kind closed
   vocab（迁移 CheckConstraint 不动）。任务书的四类核心条目实现为
   **kind 视图族**（`CARD_FAMILIES`）：
   - `SpatialEntityMemory` ← resolved_place, boundary_ref
   - `CRSPreferenceMemory` ← crs_resolution, user_cartographic_preference
   - `FieldSemanticMemory` ← dataset_semantics, field_role
   - `StrategyHeuristicsMemory` ← successful_strategy, provider_failure,
     product_decision, analysis_artifact

2. **`associative_index.py`：进程内热索引（倒排 + 网格空间 + 半衰期）**。
   - 顶层按 `org_id` 物理分区（`dict[org_id, OrgMemoryIndex]`）——跨租户
     查询在数据结构层面不可表达，而非仅靠谓词过滤；
   - 倒排索引：CJK bigram + ASCII 词元，预计算 IDF 与文档长度，BM25
     (k1=1.5, b=0.75) 打分；
   - 空间索引：bbox 均匀网格（单元格约 1°），查询时收集邻域候选，邻近度
     = 归一化 bbox 交叠/中心距（1.0 同迹 → 0.0 远离）；
   - 时间：Half-Life Decay `2^(−age_days/half_life)`，默认半衰期 14 天，
     显式用户来源（explicit_user_*）半衰期无穷大（偏好固化）；
   - 查询 O(命中 postings)，实测每候选 ≈3µs：候选 ≤300 单次 <1ms，
     退化语料（千条全命中）≈3.5ms（进程内、零 SQL）；staleness 窗口
     （默认 30s）到期后下次访问自动从 store 重温；
   - 有界：每 org 索引条目上限 1024（LRU 逐出；store 预算下最坏一次
     同步 ≈680 行，上限留余量），与 store 预算纪律同源。

3. **`proactive_retriever.py`：主动唤醒管道（Query + 身份 → Top-K 卡片）**。
   - 输入：query_text、org_id（**必填，fail-closed**）、user/session/project、
     可选查询 bbox；输出：`AwakeResult(cards, resolved_place, signals,
     latency_ms)`；
   - `MemoryContextCard`：面向模型的有界事实卡片（family/title/摘要行/
     `resolved_place{name,level,bbox}`/scope/confidence/score/reasons/refs），
     沿用「先验非证据」声明与字符预算纪律；
   - **模糊指代消歧**：`vague_reference_signals(query)` 检出回指/惯用简称
     （上次|之前|那个|本市|我区|我司|同一|还是 …）时，把记忆中的
     resolved_place 候选提升为 `resolved_place` 判定；**唯一高置信**才
     返回（top1 score ≥ 阈值 0.62 且领先 top2 ≥ 0.08 余量），并列/低置信
     返回 None 交由澄清流程——消歧绝不静默赌博；
   - scope 优先级 session > project > user（与 retrieval 权重同序），
     fresh 解析永远优先于记忆（调用侧保证，同 ADR-0183 R6 纪律）。

4. **`memory_consolidator.py`：会话 Settle 整合（晋升/降级/固化）**。
   - 位点：chat 路由 turn 结束 `harvest_spatial_memory` **之后**异步执行
     （`consolidate_session`，fail-safe，绝不阻断 turn）——「记忆滞后证据
     一个身位」纪律的延伸；
   - **晋升**：session 记忆在热索引中被检索命中 ≥2 次（`hit_count` 落在
     value，由 retriever 回写）且证据强度 ≥ review_passed/tool_result，或
     显式用户来源，则晋升到 user 作用域（习惯类：CRS 偏好、provider 避坑、
     成功策略）或 project 作用域（实体类：resolved_place、dataset 语义）；
     晋升写 value 标记 `consolidated_from: {session_id, at}` 保审计；
   - **降级/淘汰**：TTL 过期且 `hit_count==0` 的临时记忆 → `sweep_expired`
     已失效（复用）；**显式偏好（evidence.source ∈ {explicit_user_decision,
     explicit_user_correction}）永不过期、永不淘汰**（pin），由
     `consolidator.protect_explicit_preferences` 显式清 `expires_at`；
   - 晋升经过既有写入门（policy.evaluate）——晋升不是旁路，弱证据照样拒。

5. **挂载点一（`gis_situation/turn_context.py`）**：
   `build_situation_turn_context` 在 situation 投影后追加
   `[GIS_MEMORY_PROACTIVE]` 切片（有界 900 字符、卡片渲染、xml_fence 转义、
   sensitive 剔除）。身份经 `read_memory_identity(session_id)`（turn 末
   harvest 烙印）fail-closed 读取：无烙印 = 无切片（首轮自然退化）。
   任何异常只丢切片不阻断 turn（fail-open，与 situation 块同纪律）。

6. **挂载点二（`gis_harness/intent.py`）**：
   - 新增**确定性纯函数** `apply_proactive_memory_hints(intent, cards)`：
     scope 未解析（或 query 命中模糊指代）且存在唯一高置信 resolved_place
     卡片时回填 `intent.scope`，审计通道：`matched_rules +=
     ["memory_proactive_scope:<subject>"]`、`intent_evidence[
     "proactive_memory"]`、`assumptions` 披露；fresh scope 永不覆盖；
     置信折扣 ×0.9（同 ADR-0183 R6 口径）；
   - `resolve_intent_adaptive` 增加可选 `org_id`/`user_id`/`use_memory`
     参数：内部 best-effort 探查 proactive retriever（fail-open）后应用
     上述纯函数；`resolve_map_request_intent` 保持纯函数（零 I/O）不变，
     既有生产调用点（plan_orchestrator/tools）行为零变化。

7. **租户与安全（不变量继承 + 加固）**：org_id 恒由调用方烙印；索引与
   检索全链路 org 等值；卡片渲染复用 `_xml_fence` 转义（存储型注入防线）；
   sensitive 记忆不进索引（写侧剔除，索引侧重申）；单测断言「租户 A 的
   私有空间记忆跨租户检索 100% 阻断」（org 混写、query 越权、bbox 越权
   三通道）。

8. **可观测与降级**：`AwakeResult.trace`（signals、候选数、耗时）记入
   `intent_evidence`；consolidator 输出 `{promoted, pinned, swept}` 计数；
   引擎任何故障 = 退回 ADR-0183 的 passive 通道（绝不比现状更差）。

## 后果

- 正向：「免交代习惯、模糊代词秒懂」的主动体验落地；表 0080 从「只写
  不读透」变为每轮意图/情境前置主动唤醒；亚毫秒热路径不增加 turn 延迟。
- 负向/风险：索引进程态与 DB 最终一致（staleness 窗口内可见性延迟 ≤30s，
  可接受——记忆本就滞后证据）；晋升可能放大错误记忆（缓解：晋升必须
  过写入门 + 显式来源优先 + supersede 链可回滚）。
- 不做：Embedding 语义向量（无向量基建，BM25+bigram 已覆盖中文名面；
  接口留 `semantic_score` 扩展点）；R-Tree C 扩展（纯 Python 网格足够
  AOI 规模）；跨 org 记忆共享（与 tenancy 红线冲突，永不做）。
