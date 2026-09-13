# Recon — 方向 9：GIS Spatial Reasoning Memory v1

- 执行时基线：`origin/master @ 580b33e9`（PR #1272 ads-v1 合并后）。
- 分支：`harness/spatial-reasoning-memory-v1`（worktree `webgis-srm-v1`）。
- 勘察方式：主 Agent 深读核心生产路径 + Subagent A（Explore，只读）全库盘点（12 项清单见 PR 附录）。
- 结论先行：**本方向 = 一条收敛型记忆子系统（1 张新表 + 1 个新包）+ 4 个生产读写缝**，
  不是第二套 cartography preference DB，也不是通用聊天 memory。

## 1. 生产调用链（before）

```
用户消息 ──▶ POST /api/chat (chat.py:712 Pi 分支, USE_NEW_AGENT 默认 True)
  ├─ _record_frontend_cartographic_observation(session, req.map_state)   # turn 前观察写
  ├─ _build_cartography_turn_context(session, project)                   # [CARTOGRAPHY_VERDICT]+[CARTOGRAPHY_MEMORY]
  │    └─ context_assembler._build_project_memory_block(project)         # ADR-0069 active 事实 → 有界块
  ├─ _build_environment_turn_context(req.map_state)                      # [环境感知] 有界块
  └─ turn_bridge.stream_prompt(message, cartography_context, env_block)  # agent_pi_bridge
       └─ bind_turn_prompt(...)                                          # pi_turn_context.py:210
            ├─ SessionPlan 投影 + 工具面 + ACTIVE_TOOLS + 逐出 tombstone + V6 三块
            └─ attach_turn_context(...)  # 所有块插在用户消息与 turn marker 之间，marker 最后

  工具执行：webgis_map_intent (tools.py:411)
  └─ resolve_map_request_intent(query)          # intent.py：match_scope/place 逐 turn 重解析，无记忆
       └─ semantic.match_scope(query, entity_service=resolve_local_admin)  # local_first.py:164
  └─ planner.recipes.select_candidates(intent, project_verified=…)       # ADR-0069 recipe_outcome + V11 affinity

  失败路径：tool_dispatch_service.py:562
  └─ classify_and_remediate(...) → RecoveryLedger.record_failure(session, tool, class)  # 会话内 durable，无跨会话语义记忆

  turn 结束：chat.py _persist_pi_transcript
  └─ harvest_project_memory(session, project)   # ADR-0069：drift→shared_classification/recipe_outcome（仅通过评审）

记忆栈现状（全部已收敛点）：
  carto_project_facts        (project, 4 kinds, conflict/supersede/expires_at/confidence, [CARTOGRAPHY_MEMORY] 注入)
  carto_intent_evidence      (session/project, ADR-0161)
  carto_feedback_signals     (closed vocab + half-life decay)
  carto_recipe_affinity      (engine 全局, reorder-only 契约)
  RecoveryLedger             (session durable, flock JSON, ADR-0119 D5)
  artifact-ledger            (session ref 账本, LRU≤128, lineage/replaces)
  ads 快照/pin               (dataset_key + version_token + schema_fingerprint, ADR-0175)
  ads AcquisitionFacts       (outcome∈success|degraded|failed, ADR-0178)
  context_layers/PlanStore   (session 平面, durable vs rebuildable 已分类, durable_context.py)
```

**缺口（本方向要补的真实空白）**：
1. `match_scope` 每 turn 从零解析；「再看看医院」无法复用上一轮「成都」——resolved_place 无持久化；
2. 数据集语义/字段角色学习无跨 turn 记忆（ads semantic/ 每次现算）；CRS 结论同理；
3. provider 失败只有会话内 retry 预算，跨 turn「这条 provider 路昨天就坏了」不可表达；
4. 用户明确产品决策（provenance origin=user）散落在 provenance 尾部，无查询形状；
5. 记忆读取只有 [CARTOGRAPHY_MEMORY] 一个文本块通道，无按情境检索的 top-k+理由 API。

## 2. 与最近 PR 的重叠矩阵

| 能力 | master@580b33e9 | merged 近 PR | open PR | 本任务处置 |
|---|---|---|---|---|
| 项目制图偏好/共享分类/recipe 成效 | ADR-0069 `project_memory.py` | #1269/#1271 扩展（confidence/expires_at/affinity） | — | **只读消费 + 收敛写口**：偏好类写回 ADR-0069 `record_fact`；绝不新建第二套表 |
| recipe 亲和/self-heal 学习 | ADR-0161 `intent_learning.py`/`selfheal_policy.py` | #1271 | — | 不重做；successful_strategy 记忆只做**补充语义层**（analysis strategy），排序仍走 affinity（reorder-only 契约不碰） |
| 数据集身份/版本/pin | ads-v1 `versioning_gate.py` + 0070 | #1272 | — | dataset_semantics 记忆**引用** dataset_key+version_token，不建平行 catalog |
| 会话失败重试 | RecoveryLedger (ADR-0119 D5) | — | — | 复用为 provider_failure 的会话内 authority；新增跨会话 provider_failure 记忆（带 TTL），harvest 时从 dispatch 失败事件提升 |
| artifact 引用/lineage | `artifact_registry.py` (LRU≤128) | — | — | analysis_artifact 记忆只存 ref（zero-payload），不复制账本 |
| Pi turn 注入通道 | `attach_turn_context` 7 类块 | — | #1275（situation 块/env_block 重构）、#1277（kernel/projection） | **最小侵入**：在 `_build_cartography_turn_context` 拼接处追加有界 `[GIS_MEMORY]` 块（单一注入通道）；不新增 attach 参数签名之外的通道；#1275 未合并 → 用 narrow interface + fixture |
| place/boundary 解析 | `intent_semantic.match_scope` + `local_first.resolve_local_admin` | #1263 (ADR-0150) | #1278（skills/situation 读 intent） | 复用在 `webgis_map_intent`：解析失败时以记忆做 scope 补全（走既有 `hint_applied` 披露），fresh 命中永远优先；不进 intent.py 纯函数内部 IO |
| 失败分类 | `failure_taxonomy.classify_and_remediate` | — | #1273（同文件有改动风险） | 只在 dispatch 调用点后**加一行**旁路记录（fail-safe），不改 taxonomy 契约 |
| CI/hygiene | — | — | #1270（mapspec/conftest） | 零交集；若 conftest 冲突在 rebase 时最小适配 |

## 3. 旧审计 finding 复核

- `docs/research/pi-host-seams.md` "SessionPlan Missing" 已过时（`session_plan.py` ADR-0076 已落地）——以代码为准。
- carto_* 表无 org_id（ADR-0139 tenancy 缺口）仍成立 → 新表必须带 org_id + `scoped_query`。
- V11 review 落下的注入纪律（同轮不三重注入、verdict≠memory≠plan）继续有效 → 新块必须与
  `[CARTOGRAPHY_MEMORY]` 明确分工：cartography 记 project 内**作图先验**；GIS memory 记
  **跨会话/会话 GIS 事实**（place/dataset/strategy/failure/product）。

## 4. 复用 / 扩展 / 不做 清单

**复用（只读）**：carto_project_facts(preference/recipe_outcome)、carto_recipe_affinity、
recovery_ledger、artifact-ledger、ads pin/snapshot、projects.org_id tenancy、
`scoped_query`、`effective_org_id`、session ownership。

**扩展（写口收敛）**：偏好类记忆 → ADR-0069 `record_fact(kind=preference)`；
其余 9 类 → 新表 `gis_spatial_memories`（migration 0080，模型/契约详见 decisions D2/D3）。

**不做**：ChatGPT 式平台 memory；raw prompt/CoT 持久化；credential 持久化；
第二套 recipe/cartography feedback 表；第二套 dataset catalog；cache hit 冒充语义记忆；
SituationCompiler 复制（#1275 未合并，只留 narrow interface）。

## 5. 记忆 kind → 现有存储对账（Subagent A 结论 + 主 Agent 核验）

| kind | 处置 | 说明 |
|---|---|---|
| resolved_place | **新表**（session/project scope） | subject=规范化地名；value={name,level,admin_id,bbox,source}；fresh 解析优先 |
| boundary_ref | **新表存 ref** | 只存 admin 身份 + session ref/本地资产 ref，绝不存几何 payload |
| dataset_semantics | **新表** | subject=dataset_key；绑定 version_token；版本漂移自动失效（R3/R7） |
| field_role | **新表**（dataset_semantics 子结构，合并存储，独立 kind） | 词汇表复用 `data_fabric/semantic/field_roles.py` |
| crs_resolution | **新表，短 TTL + rebuildable 标记** | session scope 为主；确定性可重建，记忆只是加速 |
| analysis_artifact | **新表存 ref** | ref-only 指向 artifact-ledger/artifact_registry |
| successful_strategy | **新表**（分析策略语义层） | recipe 排序仍走 affinity；本表记「任务+范围→可用策略」先验 |
| provider_failure | **新表，必带 expires_at** | dispatch 失败旁路缓冲 → harvest 提升；session 内 authority 仍是 RecoveryLedger |
| product_decision | **新表** | harvest 自 provenance origin=user 的显式决策 + plan/product 终态 |
| user_cartographic_preference | **ADR-0069 已有，不建** | 读取侧收敛进检索/投影；写侧优先路由 record_fact |
