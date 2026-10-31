# PR: feat(sdo): 多源空间数据自动准备与语义绑定工作台 —— inspect→propose→validate→prepare→bind 端到端

## 来源（issue / 文档债 / 任务书）

- 目标来源：ZCode /goal 01 任务书（多源空间数据自动准备与语义绑定工作台）；planning/semantic-data-onboarding/ 为本目标 planning 套件。
- Baseline：`origin/master @ 4b3629c90ee977e287f7b1fb4fd827b0fad7428c`（2026-10-29 fetch 实测；任务书拟定锚点同 SHA，无漂移；开工时本机对 github TLS 全断，网络 23:34 恢复后复核 master 未前移，无需 rebase）。
- Dedup：二次去重完成（网络恢复后）——open PR：(none)；86 个 open issues（#1842–#1866）全部为 2026-10 大审查系的既有发现，与本 diff 无重叠文件；跨租户身份伪造类（#1841/42/43/47）本任务未引入同类面（新端点 session 归属一律 DB 校验 + 工具走 verified runtime context）。与 #1759（G01 数据资格）边界：descriptor/multi-role/preflight/provenance 为权威复用，本 PR 只补字段级提案确认回路 + 准备计划/收据 + 工具 + 前端。

## 根因与证据（Problem Evidence）

1. 字段角色提案面缺失：`app/services/explorer/parse_stage.py:64` auto_field_mapping 仅 4 角色、confidence 只算 name+address（:97）；`app/lib/gis/semantic_profile.py:215` derive_semantic_profile 产判定无候选/被拒概念；三套角色推断互不互通 —— 用户上传 CSV 后没有任何生产路径给出「每字段最可能是什么角色、证据、竞争候选」。
2. 准备计划/收据缺失：`app/services/data_ingest/pipeline.py:285-303` materialize 仅元数据声明；`data_quality/repair_plan.py` 只覆盖质量修复，不含字段映射/坐标轴对调/CRS 准备 —— 「检查→修正→可审计产物」中间层为零。
3. 工具准备状态机缺失：全 tools 目录无 prepared/requires_input/degraded/blocked 判别态（最近骨架是 RoleBinding 状态 + resolve_field_semantics 的 needs_clarification）—— Agent 无法诚实回答「数据准备好了吗」。
4. 语义绑定 UI 缺失：#1759 的 roles/qualification/preflight 后端生产级，前端 dataset-descriptor-modal.tsx:37-67 只渲染 5 个物理字段；UploadResponse 的 crs_source/quality/warnings 前端基本未展示。

## 改动清单（Architecture & Files）

- 新增 `app/services/data_preparation/`：vocabulary.py（13 细粒度提案角色 closed 词表 + SemanticFieldRole 单向诚实投影 + 双语同义词，负例纪律：裸「代码」不入 id）、field_proposal.py（候选/数值置信度/原因码/被拒/needs_clarification，四档名称证据含词尾中心语与单字母分词，确定性纯函数零 IO）、spatial_receipt.py（QualityReport 逐条透传 + AXIS_ORDER_SUSPECT/PK 三态/DENOMINATOR_ABSENT/时区态四类新证据）、plan.py（6 步 closed union + 前置校验（AxisSwap 须收据证据、Reproject 拒 assumed CRS）+ plan_hash 幂等键 + JSONL ring 持久化）、executor.py（纯变换 + 幂等双锚（descriptor 指纹 + 内容哈希）+ 每 (session,plan_hash) 锁 + 原件不可变 + provenance/descriptor 铸造/artifact 登记）、service.py（inspect_source/bind_field_roles 单一业务真相）。
- 新增 `app/api/routes/data_preparation.py`（8 端点，上传同款鉴权：optional auth + authorize_session_write + 404 不泄露）+ `app/schemas/data_preparation_schema.py`。
- 新增 `app/tools/data_preparation_tools.py`：prepare_dataset 五态判别式（blocked/requires_input/ready/prepared/degraded），经 ToolDispatchService 真链可达；source_ref 走 #1004 args_model ref_cursor 声明式游标。
- 前端：`frontend/components/sidebar/data-preparation/prepare-flow.tsx`（data-sources 第 5 子页签一屏流：上传选择→检查→映射复核（低置信必选）→准备执行（质量 delta）→入图 ref 水合）+ `lib/api/data-preparation.ts` + i18n dataPreparation 双语 catalog。
- 修改（最小接触面）：`app/main.py`（+2 行注册）、`app/tools/__init__.py`（+2 行）、`app/tools/categories.py`（+1 行）、`scripts/generate_endpoint_scope_matrix.py`（DOMAIN_RULES +1 域）、`tests/test_api_auth_required.py`（BODY_OWNERSHIP_CHECKS +1 helper，_verify_session_owner 同款先例）、`components/sidebar/data-sources-tab.tsx`（+子页签）。
- 生成物再生（项目原生成器）：openapi.json / api_contract_fields.json / endpoint-scope-matrix.csv。

## 验证命令与输出摘要（Test Evidence）

- `pytest tests/data/test_field_proposal_v1.py tests/data/test_spatial_receipt_v1.py tests/data/test_preparation_plan_v1.py tests/data/test_preparation_api_v1.py tests/data/test_preparation_e2e_v1.py tests/unit/test_data_preparation_tools_v1.py -q --no-cov` → **120 passed**（清偿回归后）
- `pytest tests/unit/dataset_semantics tests/data/test_ingest_pipeline.py -q --no-cov` → **68 passed**（相邻面零回归）
- `pytest tests/test_api_auth_required.py -q --no-cov` → **22 passed**；`pytest tests/quality/test_api_compatibility.py tests/unit/api_contract/test_field_contract.py -q --no-cov` → **43 passed**（快照字节闸绿）
- 工具面回归（catalog/descriptor/surface/dispatch_service/execution_policy 等 11 文件）→ **157 passed**
- 清偿后合并面（14 文件含上述全部）→ **227 passed**
- `cd frontend && pnpm typecheck` → 0 错；`pnpm exec eslint <新增文件> --max-warnings 0` → 0；`pnpm test components/sidebar/` → **270 passed (45 files)**；全量 `pnpm test` → **4666 passed**（首次单失败为与 pytest 并行的资源竞争 flake，错峰复跑绿）
- `ruff check app/ tests/ scripts/` 干净；`python scripts/check_import_boundaries.py` OK；`python scripts/generate_endpoint_scope_matrix.py --check` ok
- 关键 Oracle 双跑：E2E 幂等/确定性断言在 M4/M8/回归三轮中重复执行一致（同 hash 同 receipt、同输入同提案）
- 未跑项披露：10 万行全量冒烟未单独执行（executor 全量 deepcopy 与 ref store 既有量级一致、画像 50k 扫描预算内；大表分块优化列后续方向）；Playwright/真实地图 smoke 未跑（本 PR 前端经 vitest 组件级验证 + tsc/eslint）
- master 既有红灯（非本分支引入）：tests/data/test_data_profile_raster_band_cap_1716.py 三例在干净锚点 origin/master@4b3629c9 同口径复跑同样失败（/tmp/master-check 只读验证）

## Review 意见与清偿（Independent Review）

- Reviewer：独立 subagent（未参与实现，全新上下文读全 diff + 实证复现）；R1 结论 **FIX-FIRST**；R2 复审结论 **APPROVE**（R1 全部 12 项 CLEARED、4 原始复现不可复现；R2 新发现 6 项全 P3：R2-1/3/5 当场修复，R2-2/4/6 记入 RISKS 残差披露；见 INDEPENDENT_REVIEW.md）
- 计数：R1 P0×0 / P1×3 / P2×4 / P3×5 → 全部清偿于 commit ada8e26f3
- 逐条清偿（要点）：
  - P1#1 幂等碰撞（同 hash 跨数据集返回错误收据）→ find_receipt 加 input_ref 校验 + 收据内容锚 input_content_hash（双锚）+ 回归 test_idempotency_no_cross_dataset_collision / test_content_anchor_stale
  - P1#2 auto_execute 谎报 prepared → 澄清项存在一律 requires_input + 回归 test_auto_execute_respects_clarifications
  - P1#3 前端闸与后端策略冲突 + 测试假 severity 掩盖 → hardBlocked 镜像 _AUTO_RESOLVABLE + mock 改真实 blocked 形态
  - P2#5 状态回退被作废 → preview 对 executed/verified/rolled_back 409 + API 断言；P2#6 并发双收据 → 每计划锁 + gather 回归；P2#7 → crs_source 透传；P2#4 并入双锚
  - P3×5（单字母分词/元数据步证据/显式选无哨兵/断言拆分/文档如实化）全部清偿
- 未清偿项：无 P0/P1/P2；P3 全清。残余披露：executor 全量 deepcopy（大表优化后续方向，LOOP_STATE 风险修正）。

## 风险与回滚（Compatibility & Rollback）

- 兼容性：纯 additive —— 不改既有 explorer/ingest/quality/dataset_semantics 任何行为；descriptor 写回走既有 store 版本化（≤8 版本）+ with_fingerprints 重铸；无 DB 迁移；OpenAPI/契约快照经官方生成器再生（additive 字段）。
- 回滚方式：revert 单 commit（ada8e26f3 为清偿、前 6 个为里程碑，整体 revert 即移除全部面；无 env flag 依赖、无数据残留风险 —— 计划/收据 JSONL 在会话目录随会话清理）。
- 残余风险：prepare 工具 auto 路径的自动过滤闸 20%（可自动解决的 blocked 建议码集合当前仅 filter_rows，扩展需评审）；前端「显式选无」哨兵 __none__ 不入计划（UI 语义已测试锁定）。

## Out of Scope

- 既有审查 issues（#1841–#1847 跨租户/仿真/质量规则等）的清偿：归属独立清偿面，本 PR 不触碰其文件。
- GeoParquet 上传、本地目录数据源、拖拽上传 UI、地图足迹预览、prepare 与 MapSpec Planner 的自动衔接（compile_workflow_semantics 消费面已就绪，联动编排属后续方向）。
- 10 万行级分块/流式执行优化（现与 ref store 既有量级一致）。
