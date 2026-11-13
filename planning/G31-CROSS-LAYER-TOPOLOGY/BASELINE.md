# G31-CROSS-LAYER-TOPOLOGY — BASELINE

- 执行日期：2026-11-12（UTC）
- 目标仓库：WindWang2/webgis-ai-agent
- base SHA：`ca7b3ec273f2b43336852951e2292a416b2238e6`（origin/master，与研究快照一致）
- worktree：`/home/kevin/projects/webgis/webgis-worktrees/g31-cross-layer-topology-20261112T144915Z`
- branch：`zcode/g31-cross-layer-topology-20261112T144915Z`
- open PR（执行时）：0 个；最近合并 #2087–#2093（G22/G23/G24/G25/G26/G27/G28 已入 master）

## 环境

- Python venv：复用主工作区 `/home/kevin/projects/webgis/webgis-ai-agent/.venv`（shapely 2.1.2、pyproj 3.7.2 可用；worktree 内不另建 venv）
- pytest 从 worktree 根运行：`.venv/bin/python -m pytest`（`tests/data/test_quality_rules.py` 24 passed 已复核）
- 迁移链最高编号 0104 → 本任务新迁移用 0105
- Celery：USE_REDIS=false 时 eager 原地执行（测试路径与生产同形）
- 重阶段跨 worktree 锁：`$(git rev-parse --path-format=absolute --git-common-dir)/webgis-heavy-stage.lock`

## 已核实的关键基线（file:line 以 base SHA 为准）

| 能力 | 位置 | 事实 |
|---|---|---|
| DQ 规则 DSL | `app/services/data_quality/rules.py:27-118` | 16 类规则封闭词表、RuleSpec、ruleset digest=规范化投影 sha256、≤64 条/集 |
| DQ 报告实体 | `app/models/data_quality.py:46-154` | QualityReport（target_ref/dataset_identity/ruleset_digest/status/overall_status）+ QualityRuleResult 行；JSON 有界投影纪律 |
| DQ durable 路径 | `app/services/data_quality/engine.py:271+`（run_quality_evaluate）、`jobs.py:20-48`、`app/api/routes/data_quality.py:221-259` | ref 驱动 durable job（submit_durable_job → celery task → 幂等复用 completed 报告）；worker 侧 _worker_session_owned 权限复核 |
| 修复执行 | `app/services/data_quality/repair_execution.py:101-197` | execute_repair：修复→新 ref（绝不覆写源）+ 有界 evidence + lineage；persist_repair_lineage 项目侧行 |
| G14 编辑 | `app/services/feature_edit/service.py:33-155`、`contracts.py` | build_and_store_plan（预览+TTL plan 暂存）、agent_commit（policy 闸、CONFIRMATION_REQUIRED、CAS STALE_REVISION）；编辑词表封闭（set_fields/rename_field/repair_geometry/move_by/snap_to_ref/create_features/delete_features/replace_geometry/split_feature/merge_features）；`_wfid` 稳定身份；MAX_GEOMETRY_TARGETS=10 等 |
| G14 局部拓扑 | `app/services/feature_edit/topology.py`、`executor.py:293+` | 提交期复核三种声明式规则（no_overlap/no_self_intersection/no_duplicate_points），米制经 Geod 换算 |
| G24 选择句柄 | `app/services/selection_handles.py:1-150` | 不透明 handle 绑定 (session, ref, content_revision)；scope 封闭词表 bbox/where/ids；bbox=粗超集并披露；TTL 600s；消费时重走鉴权；>TOTAL_EXACT_LIMIT 时 count 未知 |
| G18 源解析 | `app/services/data_delivery/source_resolver.py:408-640` | resolve_source 支持 session_ref / project_artifact（durable 修订+read_content 摘要校验+is_complete）/ fabric_parquet / table_rows；带 actor 鉴权（verify_session_access/_verify_project_access）——G31 源绑定的直接复用点 |
| BlobStore | `app/services/durable_blob_store.py:128+`、`get_filesystem_blob_store()` | 内容寻址 JSON blob（put_json→key+sha256）；witness 几何证据的存储路径 |
| durable jobs | `app/services/jobs/submit.py:43-213` | submit_durable_job（幂等键、取消 token、进度回写、失败落终态）；JobKind/JobStatus |
| 工具注册 | `app/tools/registry.py`、`app/tools/feature_edit_tools.py` | `@tool(registry, tier=…, name=…, param_descriptions=…)` 模式；API/工具共用 service 面 |
| 质量侧栏 | `frontend/components/sidebar/project/quality-panel.tsx:72-110,171-173` | catalog preview 100 → inline audit；sampleOnly 披露、样例不完整禁修复；useProjectDatasets/useProjectQuality hooks |
| 前端面板挂载 | `frontend/components/sidebar/project/project-assets-section.tsx:18,110` | QualityPanel 挂载点（新面板平行挂载处） |
| i18n | `frontend/lib/i18n/messages.ts` | 静态 import 双语 catalog；新 namespace 需在 zh-CN/en-US 建文件并登记（键一致性有测试钉住） |
| 前端测试 | `frontend/test/project/quality-panel.test.tsx` 等 | vitest + jsdom，mock-api 模式 |

## 本任务不做（边界）

- 不改 Pi vendor/RPC、不重写 G17/G18/G19/G23、不改 G24 拾取语义
- 不重写 feature_edit/executor.py、identity、journal（只做只读消费 + 桥接调用）
- 不开发 G32 的持久变更提案；不等待任何并行 PR
- 不把 R1 审计问题清偿当目标（只在触及路径上守门）
- 不新增第二套 MapSpec store / 任务状态机 / 内容存储（witness 走 BlobStore，运行/发现走自有伴随实体 + durable jobs）
