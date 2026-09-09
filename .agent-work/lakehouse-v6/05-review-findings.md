# Lakehouse V6 — 05 Review Findings / 06 PR Summary

## Review Round 1（架构/正确性/回归，独立 subagent）

| 级别 | 发现 | 处置 |
|---|---|---|
| BLOCKER B1 | POST 端点 `require_owned_session` 解析 query 参数，业务用 body session_id → 跨租户读写 | ✅ 修复：POST 直接对 body session_id 调 `verify_session_owner`；测试用保真守卫 fake（deny 抛同款 404）+ 跨租户回归锁 |
| CRITICAL C1 | `WEBGIS_OBJECT_STORE_BACKEND=s3` 死接线（无生产调用方） | ✅ 修复：`get_object_store()` 成为 data_object/durability/DR 的真实选择器；orphan 扫描对不可枚举后端 typed 拒绝 |
| CRITICAL C2 | `fork_cube_revision` 无生产调用方 + zarr 元数据原地覆写可能击穿源修订 inode | ✅ 修复：新增生产调用方 `POST /lakehouse/cubes/revise`；元数据文件一律拷贝不硬链接；绝对路径不入 store attrs |
| MAJOR M1/M-5 | project 域 manifest 无属主校验（REST 面） | ✅ 修复：project_id 在 REST 面显式 400 拒绝（项目域发布通道不存在，ADR non-goals） |
| MAJOR M2/R1 | cube 指针 save 时 payload 与 build 时发散 → 双身份 | ⚠️ 部分：预算内 build 时发布为主路径（restore 用 build manifest 的 blob）；save 时 manifest-only 指针可校验不可物化 → restore 诚实 False（既有披露）；文档写明 |
| MAJOR M3 | publish_data_object 预算"写前拒绝"声明不实；manifest 拒绝时 blob 泄漏；2GiB 全量驻留 | ✅ 修复：两遍法 —— 第一遍流式量尺+摘要（无写入），manifest 构造通过后第二遍写 blob |
| MAJOR M4/M5 | durable 失败仅 WARN 无升级；S3 异常折叠为缺席（DR 状态失真） | ✅ 修复：S3 区分 404/NoSuchKey（缺席）vs 传输错误（typed unavailable）；durable 状态经响应字段披露（文档补 GC 生命周期段） |
| MINOR | env fp 使升级后 CAS 去重失效（ops 注意）；GC cube 半写目录盲区；负切片；错误消息泄路径；sha256 三处重复；死代码 | ✅ 修复/记录：路径脱敏 + 切片校验 + sha256_of_file 统一 + 死代码删除；其余记入 Known Limitations |

## Review Round 2（性能/安全/UX/可维护性，独立 subagent）

独立得出同 B1/M-1/M-2/M-5；新增关键发现：
- **C-1**：lakehouse blob 与 promotion GC —— ✅ 在共享保护谓词处文档化生命周期语义（会话期宽限；长生命周期必须落快照指针/revision/head 引用面）+ 台账记录 `content_location` 证据；完整 dereference 接线为 follow-up。
- **M-2**：cube 窗口读无界（8GiB 级 OOM DoS）—— ✅ 至少一个有限切片 + 负号拒绝 + 服务端钳制 + 8M 单元预算。
- **M-3**：geoparquet 发布双读/三哈希 —— ✅ publish 两遍法消除全量驻留（IO ~2× 保留，文档注明）。
- MINOR：canonical_dumps 先于 1MiB 量尺（≤1MiB 契约下可接受，记录）；content_hash docstring 夸大（已软化：runtime 不分支于此字段）；tool session_id 由 harness 注入覆盖 LLM 传值（既有约定，spoofing 面仅存在于无 harness 直派发，记录）。

## PR 摘要（06）

- **Problem**：数据面缺 durable lakehouse（悬空 fabric ref、FS-only BlobStore、opt-in 内容身份、zarr stub、无 cube）。
- **Architecture**：组合层 `app/services/lakehouse/` 挂既有单一事实源（BlobStore/台账/revision/GC 闸）；零 DB migration。
- **Waves**：14（docs、identity、S3、fabric ref、vector、raster、zarr cube、cube service、dedup、workspace durability、lineage、DR/GC、security、REST+perf+docs）。
- **验证**：全量后端 12818 passed / cov 84%；V6 专项 449+；quality gates 全绿（regen 后）；perf harness nightly-only 4 passed。
- **Rebase**：origin/master 无新提交（445ad30），rebase no-op；changed-scope 复测 710 passed。
- **Known Limitations**：见 `docs/data-plane/v6-spatial-lakehouse.md`（预算降级 manifest_only/oversized、GC 生命周期语义、manifest-only 指针不可物化、时间片对齐上游职责、xarray/ETag/S3 multipart follow-up、catalog GIN deferred）。
