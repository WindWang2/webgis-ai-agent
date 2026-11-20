# G38 BASELINE — 数据关联与空间富集作者工作台

## 执行身份

- TASK_ID: `G38-DATA-JOIN-AUTHORING`；slug `g38-data-join-authoring`
- BASE_SHA: `1276692737a8cf58d0604fb40ae0e70fc61779a2`（= 研究快照，fetch 后无漂移；origin/master 同 SHA）
- Branch: `zcode/g38-data-join-authoring-20261114T151651Z`
- Worktree: `/home/kevin/projects/webgis/webgis-worktrees/g38-data-join-authoring-20261114T151651Z`
- 主工作区 HEAD `816705218`（旧于 base，仅读；有用户未提交 `.claude/skills/*`、`vendor/pi` 改动，绝不触碰）
- Research 时间：2026-11-14（UTC）

## 仓库事实（启动时核对）

- remote = `https://github.com/WindWang2/webgis-ai-agent.git`；default branch = `master`
- Open PR：0；Open issues：0（`gh issue list --state open` 空，exit 0）
- 最近 50 merged PR：#2076–#2116 全部合并（G29–G35 = #2094–#2100；R1 修复 = #2101–#2116；集成修复 #2084–#2086、#2033–#2036 均合并）
- vendor/pi gitlink = `b4afdddf44e059c67ccf5097e5317d913521b470`（v1.5.2，G22 对齐，不动）
- 依赖锁定：`pandas==2.3.3`、`geopandas==1.2.0`、`shapely==2.2.0`（requirements.lock）。requirements.txt:46 写 `pandas>=2.2.2,<4.0` 但注释 #1512 说明真实意图上界 <3.0；本任务不升级依赖，按 lock 版本核对接口。
  - pandas merge 的两侧 null 键互相匹配（pandas 官方文档），与 SQL 语义不同 → 本域空键政策必须在进入 pandas 前确定（WP04）。
- 相关 R1 审计（已修，语义必须保留）：#2065 联邦 aggregate_join limit 截断、#2066 V6 fetch window 截断 join/聚合、#2070 晋升 GC 删已发布 blob（#2103 修复 keep referenced blobs）。

## 复用基线（来自 planning 文档 + 代码，详细证据见 DEDUP_MATRIX / INTERFACES）

- **G23 durable ops**：`app/services/jobs/`（事实源 analysis_tasks 表，fencing=worker_id+attempt，心跳 300s/孤儿 3600s，两段式取消）；接入范式 = `DurableJobStore.create`(幂等键) → `app/services/data_operations/runner.py` `async_durable_job`（原子认领+心跳+取消轮询+fenced 终态）。JobKind 在 `app/services/jobs/lifecycle.py`（String(20) 无 CHECK，纯加性）。
- **G18 delivery**：`app/services/data_delivery/` — `source_resolver.py` SourceSnapshot（表/矢量、版本、完整性、批读）、vector_carrier（features_to_arrow / table_to_geoparquet）、publish_data_object/resolve_data_object、DurableJobStore 范式、受权下载+回读核验。
- **G24 selection**：`app/api/routes/selection_handles.py` + `app/services/selection_handles.py` — bbox/where/ids(≤200) → handle（TTL 600s，revision 前进 410），materialize 超 10k typed 422；ids first-wins + missing_ids 明示。
- **G33 thematic**、G31 topology、G32 proposals：workbench 范式（service 包 + routes + tools + frontend panel + i18n zh/en）。
- **G14 identity**：`app/services/feature_edit/identity.py` 服务端稳定身份函数。

## 预算与资源纪律

- Subagents：本任务总额 3；已用 2（research），1 保留给独立 reviewer（不参与实现）。
- 构建/测试：-j2 默认、pytest 单进程、全机 heavy-stage 锁 `$(git common-dir)/webgis-heavy-stage.lock`。
- 不等线上 CI；本地关键验证必须实际执行。
