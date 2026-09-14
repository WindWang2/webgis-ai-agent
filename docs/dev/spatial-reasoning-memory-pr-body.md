## 方向 9：GIS Spatial Reasoning Memory v1 — 可复用的 GIS 世界事实记忆（ADR-0183）

**执行时基线 SHA：`origin/master @ 580b33e9`**（PR #1272 ads-v1 合并后）。
执行期间 master 未前移（`git rev-list HEAD..origin/master` = 0），无 rebase/merge 需要。

---

### 1. Phase 0 复核结论

- `git fetch --all --prune` 后实测：执行时 open PR 为 **#1270/#1273/#1274/#1275/#1276/#1277/#1278**（并行方向线）+ 最近 merged #1262-#1272。逐 PR 比对文件面后确认：本分支与其零功能重叠（详见 §2）。
- ADR 编号：master 占用至 0179；open PR 已占 0180（×3）/0181/0182 → 本分支取 **0183**（手动分配 + `ownership.json.adr_watermark: 137→183`，allocator 复核无撞号文件）。
- 迁移：初领 0072 落在 adaptive-data-supply **预留段 0070-0079** 内，违反 `docs/dev/migration-protocol.md` §2「分支内不得越段用号」→ 改领本分支自有段 **0080-0089**（`.alloc.json` surgical 登记，`migration_watermark: 48→80`；`alloc_migration.py --check` 通过：59 迁移、单 head `0080_gis_spatial_memories`）。
- `docs/research/pi-host-seams.md` 的 "SessionPlan Missing" 已过时（ADR-0076 已落地）——以代码为准。
- 全库记忆/事实栈对账（recon §5）：ADR-0069 项目制图账本、V11 学习基座（intent evidence/feedback signals/recipe affinity/self-heal）、RecoveryLedger、ads pin/snapshot、artifact 账本全部确认；10 类记忆 kind 逐一给出复用/扩展/不建裁决。

### 2. 与 open/recent PR 的防重复说明

| 能力 | 处置 |
|---|---|
| ADR-0069 `carto_project_facts`（偏好/recipe_outcome/shared_classification，`[CARTOGRAPHY_MEMORY]`） | **只读消费 + 写口收敛**：项目制图偏好经 policy 路由 `record_fact(kind="preference")`（显式用户来源 `supersede=True`，ADR-0069 自身语义）；**绝不建第二套 preference/recipe 成效表**（review F1 修复：successful_strategy 的 recipe 维度生产者已删除） |
| V11 `carto_intent_evidence/feedback_signals/recipe_affinity` | 不重做；recipe 排序仍走 affinity（reorder-only 契约未触碰，回归护栏 `test_record_fact_conflict_semantics_untouched`） |
| ads-v1 dataset catalog/pin/snapshot | dataset 语义记忆**引用** dataset_key+version_token，不建平行 catalog |
| RecoveryLedger（会话内 durable 失败预算） | 保持 authority 不变；新表只存跨 turn 的 provider_failure 语义记忆（TTL 7d） |
| artifact_registry 账本 | analysis_artifact 记忆 ref-only 指向账本，不复制 |
| #1275 Situation（未合并） | **narrow interface**：`queries.MemoryProjectionInput` + `build_memory_projection`（+ `lookup_dataset_memory`）为其预留事实源缝，不复制 SituationCompiler |
| #1277 kernel / #1276 capability graph / #1278 skills / #1274 tool surface | 零文件冲突面（chat.py 触碰点为 `_build_cartography_turn_context` 尾部拼接与 turn 端收割，均为 additive） |
| #1270 CI hygiene | 零交集 |

### 3. 架构 before/after

**Before**：`match_scope` 每 turn 从零解析（「再看看医院」丢失上一轮「成都」）；数据集语义/字段角色/CRS 无跨 turn 记忆；provider 失败只有会话内 retry 预算；用户显式产品决策散落 provenance；记忆读取只有 `[CARTOGRAPHY_MEMORY]` 一个无检索文本块。

**After**：`app/services/gis_memory/`（contract/sanitizer/policy/store/retrieval/projection/pending/harvest/queries/eval）+ 单表 `gis_spatial_memories`（迁移 0080）：

- **写**：evidence-gated fail-closed（8 证据源 × kind 允许矩阵、置信分档 0.5/0.6、TTL 自动解析、失效规则推导）；消毒先于策略门（凭证键剥除/值形态硬拒绝/路径遮蔽/控制字符剥离）；矛盾 = supersede 链（用户纠正必胜、弱证据不落库、active 行 partial unique index + IntegrityError 重试兜底并发双写）。
- **读**：六级必过滤（租户/作用域/active/未过期/敏感/数据集版本兼容）→ subject 匹配 + kind 权重 + 置信 + 新鲜度 + 作用域优先级 → 有界 top-k（≤8，硬顶 16）逐条理由。
- **注入**：`[GIS_MEMORY]` 有界块（≤1100B）在 `_build_cartography_turn_context` 尾部拼接（Pi 单通道纪律，verdict → cartography memory → gis memory；turn marker 仍最后）；不可信串一律 `_xml_fence`；块头声明「先验而非证据」。
- **生产缝**：`webgis_map_intent` scope 未解析时记忆兜底（fresh > scope_hint > memory，`hint_applied` 披露 + 0.9× 折扣）；dispatch 失败缝 provider_failure 候选（pending 缓冲，热路径零 SQL，全局 session-LRU 512）；turn 端 `harvest_spatial_memory`（org/user 烙印 + 租户桥 `_gis_memory_org` + pending 排空 + MapSpec 剖面 → 数据集语义/字段角色/CRS + artifact ref + 用户决策 + 版本对账 + GC sweep）。
- **GC**：TTL sweep（harvest 位点）+ 每 (org,scope,scope_id) 预算 LRU（session 80/project 400/user 200）+ dataset 版本失效 + scope_gone/manual API。

### 4. Milestone 交付摘要（7 commits）

| commit | 内容 |
|---|---|
| c40e4691 | M0 recon/decisions/ledger（before 调用链 + 7 open PR 重叠矩阵 + kind→存储对账） |
| 0b7050f5 | M1 契约 + 写策略 + 存储 + 迁移（27 tests） |
| 4d3cd47c | M2+M3 检索/投影/收割/生产接线（18 tests） |
| de227ab4 | M4 安全/租户加固（8 tests；投影层 sensitive 纵深拦截） |
| 68d7b812 | M5 评估语料 32 场景 + ADR-0183 + 契约 schema 导出/漂移测试（11 tests） |
| f0d93b3f | 迁移改段 0072→0080（协议 §2 越段纠正） |
| 9721f73e | 独立 review 修复 F1-F15（见 §7） |

### 5. 数据 / 性能 / 正确性指标

- **评估（R9，32 场景 × 7 类任务书轨迹，离线确定性回放）**：useful_reuse=36、wrong_reuse=0、stale_reuse=0（一票否决门）、retrieval_precision=36/36=1.0、context_bytes_saved=1670B、tool_calls_saved=36、加权分 `3×useful−4×wrong−4×stale`=108。
- **正确性红线全部有测试锁定**：跨租户三层隔离、session 等值边界（伪造/前缀读不到）、访问撤销立即不可见、sensitive 双防线（检索剔除 + 渲染拦截）、credential 双防线（键剥除 + 值硬拒绝）、同 key 双 active 被部分唯一索引阻断、ADR-0069 conflict 语义回归护栏。
- **资源**：检索每次 3 个作用域查询（有界 LIMIT）；投影 ≤1100B；value ≤2048B；每作用域行数预算；memory_stats 聚合移出 turn 热路径。

### 6. 本地测试原文摘要（未等待线上 CI）

```
tests/test_gis_memory_{store,retrieval,wiring,security,eval_corpus,schema_drift,review_fixes}.py
  → 73 passed
tests/cartography/（ADR-0069/V11 回归）           → 1426 passed, 3 skipped
tests/test_chat_api.py + test_cartography_turn_injection.py
  + test_error_sanitization.py + unit/test_audit4_harness_semantics.py
  + test_pi_status_fail_closed.py + test_tool_meta_contract.py    → 49 passed, 1 skipped
迁移：alembic upgrade head → downgrade -1 → upgrade head（scratch sqlite）三段验证；
alembic heads = 0080_gis_spatial_memories（单头）；scripts/alloc_migration.py --check OK
ruff：本分支全部新增/触碰文件 0 findings
```

**master 预存失败对照**：本轮全部 scoped 回归零失败，无「本任务回归 vs master 预存」归因需求；按资源纪律（任务书 §8）未跑全量套件，若 CI 长尾出现失败将以 `origin/master` 干净复跑对照归因。

### 7. 独立 review findings 与修复（Subagent B，四轴复核，结论 FIX-FIRST）

**已修复（P0×1 + P1×6 + P2×8，全部重跑受影响测试）**：

| # | 轴/级别 | 修复 |
|---|---|---|
| F1 | 架构 P0 | successful_strategy 生产者删除——recipe 成效唯一存储 = ADR-0069（同位点已写 recipe_outcome，防双块双源） |
| F2 | Spec P1 | 数据集记忆 value 恒带 dataset_key+version_token；harvest 位点版本对账（invalidate_for_dataset 生产触发）；检索 stale 过滤回归测试锁定生产形状 |
| F3 | 安全 P1 | `[GIS_MEMORY]` 全部不可信串 `_xml_fence` 转义（存储型注入通道封死）；subject 剥控制字符（防日志伪造） |
| F4 | Spec P1 | analysis_artifact 生产者（artifact 账本 ref-only）；偏好 eval 改走生产路由端到端（不再绕过 D4 路由） |
| F5 | 可靠 P1 | pending 缓冲全局 session-LRU（512）+ `set_session_clearing` 丢弃钩子 |
| F6 | 可靠 P1 | active 行 partial unique index（sqlite/postgres 双谓词）+ IntegrityError 回滚重试 |
| F7-F15 | P2 | 消毒前置门、stale 指标激活、单行渲染隔离、stats 移出热路径、naive-UTC 列默认、全键 secret 匹配、boundary_ref/product_decision/preference 渲染、drain 移至 gather 后、测试断言收紧 |

**有意不接线（诚实披露，均有两级防线兜底）**：
- `lookup_dataset_memory`：无仓内消费方——它是为方向 2（#1275）预留的 narrow interface（与 `build_memory_projection` 同缝），合并后挂接；
- 项目删除 → `invalidate_for_scope`：未挂 project_service 钩子（并行线触碰面）；`retire_memory`/`invalidate_for_scope` 作为 API/审计入口提供。TTL + 预算淘汰保证无界增长不可能发生。

### 8. 兼容性 / 风险 / 回滚

- **兼容**：未改任何既有函数行为——chat.py 为 additive 拼接/收割（fail-open：记忆缺席 = 空串 = 现状退化）；`webgis_map_intent` 兜底仅在 scope 未解析且无 scope_hint 时触发，`hint_applied` 显式披露；ADR-0069/V11 契约零改动（1426 项回归护栏）。迁移 additive、可 downgrade、create_all-coexistence 可重入。
- **风险**：① chat.py 与 #1275/#1277 的合并冲突面（同文件不同函数，语义正交）；② 每turn 一次同步 DB 收割（to_thread，与 ADR-0069 harvest 同位点同成本）；③ Postgres 部分唯一索引需迁移到位后才受保护（sqlite create_all 同步生效）。
- **回滚**：`GIS_MEMORY` 注入/收割/工具兜底三缝各自独立、fail-open；代码回滚 = revert 本分支；数据回滚 = `alembic downgrade -1`（drop 表，无共享数据）。

### 9. 后续接口点

- 方向 2（#1275）合并：`MemoryProjectionInput` 作为 SituationCompiler 事实源挂入；`lookup_dataset_memory` 接入数据供给计划期。
- `get_local_admin_boundary` 工具成功路径 → 精确 boundary_ref（几何级 ref）。
- data-lifecycle GC 循环接管 `sweep_expired` 周期化；project_service 删除位挂 `invalidate_for_scope`；用户偏好的 UI 撤销入口（`retire_memory`）。

---

**未等待线上 CI，未自动合并。** PR 创建即停，review/merge 交由维护者。
