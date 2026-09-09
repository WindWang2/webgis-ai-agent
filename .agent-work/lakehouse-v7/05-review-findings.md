# Lakehouse V7 — Review Findings

## R0 — Subagent-A 架构挑战（Phase B，实现前；agent_270b5b42）

输出 28 条发现；采纳与处置：

| # | 级别 | 发现 | 处置 |
|---|---|---|---|
| 1 | BLOCKER | zarr v3 不认 `_ARRAY_DIMENSIONS` attr；xarray 绑定维度靠 metadata `dimension_names` | **已采纳**：v3 `create_array(..., dimension_names=...)`；attr 仅镜像。已实证（zarr 3.3.0 + xarray 2026.7.0：dims 绑定 + coords 自动识别）。测试 `test_labeled_store_roundtrip_and_window` + xarray 验收断言 |
| 2 | CRITICAL | V6 六入口对 v2 store 的 3-D 形状假设会读错轴/崩 | **已采纳**：`_require_v1_cube` 版本闸进 `read_cube_window`/`fork_cube_revision`/`_read_window_bounded`；v2 fork typed 拒绝（v2-aware fork 为后续能力）。测试 `test_v6_entry_points_gate_labeled_store` |
| 3 | CRITICAL | labeled 投影全坐标会撞 64KiB manifest 闸 | **设计本已规避**：manifest payload 只带 `coords_summary`（kind/n/start/end/first/last）；完整坐标只存 zarr 坐标数组。写入器返回投影与 readback 投影均已断言恒等 |
| 4 | MAJOR | virtual 空 entries 撞 `compute_content_root` 非空闸；`verify_data_object` 零 blob = verified 假绿 | **已采纳**：virtual manifest 特例 —— content root 从有序 children ids 计算；verify 递归 children（`virtual_children_missing`/`virtual_child_corrupt` 状态） |
| 5 | MAJOR | kind 白名单双写（data_object/dr）；`resolve` 不查 schema_version | **已采纳**：`DATA_OBJECT_KINDS` 单点常量双处消费；orphan 扫描 kind 集合扩展 + 版本容忍 |
| 6 | MINOR | v2 不得复用 v1 attr 键位 | **已采纳**：v2 只写 `variables`/`dims`/`labeled`/`crs`/`transform`/`nodata`，绝不写 `bands`/`times` |
| 7 | CRITICAL | catalog 以 data_object_id 为 PK 撞多 owner 发布 | **已采纳**：代理自增/uuid PK + `unique(owner_type, owner_id, content_sha256)` |
| 8 | CRITICAL | `record_revision` FK 需要 artifacts 行，publish 不经 run → FK violation | **已采纳**：publish 先 find-or-create project `Artifact`（`storage_ref=data_object_id`，幂等）再 record_revision |
| 9 | MAJOR | 发布后的对象在其项目域内不可解析 | **已采纳**：`resolve_project_object`（catalog `status=active` 行授权）+ GET surface 扩展；session 域路由保持 `_reject_project_scope`（R0-23） |
| 10 | MAJOR | catalog 投影漂移无对账 | **已采纳**：GC reaping 步（manifest 不可解析 → 行 revoked）+ search 对候选项抽查 liveness（bounded） |
| 11 | MINOR | revoked child + active virtual parent 目录不一致 | 检索结果对 virtual 标注 `degraded`（children 校验可见）；文档化 |
| 12 | BLOCKER | GC plan token 只证候选集不变，不证可达性不变（计划期新发布引用候选 blob → 误删） | **已采纳**：execute 时对每个 blob 候选**重跑**保护扫描（以当前引用集为准）；token 加 publication watermark（计划时最大 manifest mtime + catalog max updated_at；execute 发现 watermark 漂移 → 整体拒绝重规划） |
| 13 | MAJOR | multipart complete 与 sidecar meta 写入间窗 → 并发 put-if-absent 全量重写 | **已采纳**：digest/ETag 进 `create_multipart_upload` 的 object Metadata（complete 前即在） |
| 14 | MINOR | multipart ETag 非 digest | scrub 的 etag_check 只做 head-ETag vs sidecar 记录值比对，绝不与 sha256 比（文档化） |
| 15 | MINOR | S3 读中删竞态分类歧义 | 接受（transient，digest 校验兜底）；文档化 |
| 16 | CRITICAL | 硬链接 × in-place RMW = 跨修订静默腐败；一个 chunk 文件覆盖多 t 时 CoW 跳过逻辑失真 | **已采纳**：fork 前置 chunks[0]==1 契约闸（typed 拒绝）；v2 不提供 in-place RMW 路径（write 恒新建 store）；未来 RMW 路径必须先 `st_nlink>1 → 复制断链`（写入工具函数 `break_hardlinks_before_write` 备用）。测试 `test_v6_fork_guard_rejects_multi_time_chunks` |
| 17 | MAJOR | GC 必须按"无活 manifest 引用（并集）"判 blob 可删，非按死 manifest 的 blob | **已采纳**：保护 blob 集 = 全部活 manifest 的 content_blobs 并集；仅删 ∉ 保护集的 blob。测试：孤儿父 + 活子共享 blob ⇒ 零删除 |
| 18 | MAJOR | protected roots 漏 in-flight workflow runs | **已采纳**：root 源加 running/pending `workflow_runs` 的 run_manifest artifact refs |
| 19 | MAJOR | registry TTL vs GC 宽限耦合 | **已采纳**：GRACE_HOURS 默认取 ≥ registry TTL（读 `_default_ttl` 同源）；文档化耦合 |
| 20 | MINOR | DR backup chunk 是天然孤儿 | 文档化（备份是派生数据，GC 可回收；建议 GC 后重备份 —— CAS 重灌廉价） |
| 21 | MINOR | bucket lifecycle 带外删除 | 部署前提文档化；scrub etag_check 是探测器 |
| 22 | NIT | 工作副本 GC 硬链接安全（只 unlink） | 已在代码注释声明 |
| 23 | MINOR | project_id 不对称 | session 域路由保持 `_reject_project_scope`；新端点显式域分支（R0-24：catalog 双域显式 fail-closed 404 分支） |
| 25 | MAJOR | PG `json` 列无 GIN opclass | **已采纳**：tags 列 `JSONB`（`.with_variant(JSONB, "postgresql")`）；SQLite 方言跳过 GIN，测试按方言断言 |
| 26 | MINOR | 0034 head 竞争 | 0034 严格 additive（建表+索引）；提交前复跑 `alembic heads` |
| 27 | MAJOR | `publish_data_object` 第二遍 `read_bytes()` 全量驻留 | **已采纳**：BlobStore 加 `put_blob_from_path`（FS 分块拷贝 / S3 multipart 流式），publish 文件源走流式路径 —— 峰值 O(chunk) |
| 28 | MAJOR | virtual 解析需全局预算（菱形 DAG 指数展开） | **已采纳**：全局 visited-id 集 + 全局展开预算（10k 节点） |

NIT 24 并入 9/23。**Verdict: conditional proceed → 条款已全部折入实现。**

## 设计修订（01-architecture.md 同步）

- §1.1 存储形态：v3 `dimension_names` 为主、`_ARRAY_DIMENSIONS` attr 镜像；
- §1.1 attrs：v2 键位独立（`variables`/`dims`），不复用 `bands`/`times`；
- §5 Virtual：children-id content root、verify 递归、全局解析预算；
- §6 Catalog：代理 PK + `unique(owner_type, owner_id, content_sha256)` + JSONB tags + 漂移对账；
- §8 Publishing：find-or-create Artifact → record_revision；project-scoped resolve；
- §9 GC：blob 保护集 = 活 manifest 引用并集；execute 重验；watermark；in-flight runs 根；GRACE≥registry TTL；
- §4 S3：multipart Metadata 携带 digest；`put_blob_from_path` 流式发布。

## R1 — Round 1 代码审查（Subagent-A 复用；实现后 diff）

5 CRITICAL + 3 gate MAJOR + 12 MINOR/NIT。全部 CRITICAL/gate-MAJOR 已修
（commit 82eae9f0）：

| # | 级别 | 发现 | 处置 |
|---|---|---|---|
| 1 | CRITICAL | GC 在 S3 后端崩（float(datetime)） | 修：iter_objects epoch 归一化 + 消费侧防御 + S3-fake GC plan 测试 |
| 2 | CRITICAL | GC 端点仅 session 门禁（跨租户破坏 + 孤儿 id 泄漏） | 修：admin 角色门禁 + 测试 |
| 3 | CRITICAL | coords_from_transform_list 丢 c/f 平移（地理全错） | 修：c/f 参与 + 绝对坐标/跨原点拒绝回归 |
| 4 | CRITICAL | virtual owner 检查死代码 | 修：对照根 owner_scope 字典 + owner_mismatch 回归 |
| 5 | CRITICAL | publish 幻影 revision location | 修：键=位置=digest 自洽 + BlobStore 往返回归 |
| 6 | MAJOR | xarray 验收未测 | 修：open_zarr(v2) dims/coords/值断言 |
| 7 | MAJOR | REST verify 未分派深度校验 | 修：kind==virtual → deep |
| 8 | MAJOR | 项目域解析无 REST | 修：GET projects/{id}/objects/{id} |
| 9 | MAJOR | tags 过滤破坏分页 | 修：原始页满即续页 + tags_filtered 披露 |
| 10 | MAJOR | select-then-insert 竞态 | 修：IntegrityError→重查（savepoint）|
| 11 | MAJOR | manifest_only 被 scrub 报 corrupt | 修：payload durable 标记 + 独立状态 |
| 12 | MAJOR | RS 多波段静默读 band1 | 修：显式 band_index 强制 |
| 13 | MINOR | GC 删除残余竞态 | 记录为 bounded-residual（ADR §9） |
| 14 | MINOR | fixpoint 迭代中变异集合 | 修：快照迭代 |
| 15 | MINOR | ref 行不对账 | follow-up（记录 PR body） |
| 16 | MINOR | put_blob_from_path 键守卫缺失 | 修：64-hex 键 digest==key 强制 |
| 17 | MINOR | worker 线程 asyncio.run | 修：ref 解析移 async 阶段 |
| 18 | MINOR | 非连续标签静默包络扩展 | 修：typed 拒绝 |
| 19 | MINOR | docstring 过时 + coords 锚维度不足 | 修：v3 协议表述 + 全维并集 |
| 20 | NIT | STAC href 死分支 | follow-up |
| 21 | NIT | etag_checked 语义 | 修：执行即 True |
| 22 | NIT | 预算超限报 corrupt | 修：独立 budget_exceeded 状态 |
| 23 | NIT | eval-corpus 混入 | 修：revert（属并行 epic） |
