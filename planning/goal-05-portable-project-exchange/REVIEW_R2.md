# REVIEW_R2 — 复审（2026-11-28）

## R1 关闭证据（逐项）

| R1 发现 | 处置 | 关闭证据 |
|---|---|---|
| P0 manifest 路径任意文件读取 | `_safe_staging_path` 包容校验 + `_validate_manifest_paths` 前置闸（解包后、任何 DB 写前全量校验存在性）+ 载荷/产物 sha256 强制对账 | test_r1_fixes.py::TestP0PathContainment 6 例（绝对/穿越/NUL/缺失文件/载荷伪造摘要）全绿 |
| P1-2 回滚死代码 | mapping 引用传入 `_register_all` 边注册边填充 + `_compensate_project` 提取复用 + 提交后失败路径补偿 | test_r1_fixes.py::TestP1_2RollbackNotDead（mapping 增量可见 + 补偿后零残留） |
| P1-3 路由 user 键错 | `user.get("id")` → `user.get("user_id")`；测试 override 改为真实形态（仅 user_id/role/org_id） | test_routes.py 6 例绿（真实键集下 200/401/422 语义正确） |
| P1-4 回执 500 | ImportStatusResponse 增加 `job_id` 字段 | test_r1_fixes.py::TestP1_4ReceiptSchema |
| P1-5 中间态哈希 | gpkg 成员 sha256 置空 + 数据集循环后终态整文件哈希统一回填；导入侧强制对账该终态值 | test_roundtrip.py 往返全绿（含独立库读回 + 台账哈希对账） |
| P2-6 提交后失败残留 | receipt 写盘失败 → `_compensate_project` 补偿删除 | 代码路径 + P1-2 补偿助手复用 |
| P2-7 未声明条目 | 解包后立即删除台账外条目 + 回执 `undeclared_entries_removed` 披露 | importer runner 路径 + schema/前端类型 |
| P2-8 事件循环重 IO | 阶段 A upload/产物解析全部 `asyncio.to_thread` | exporter `_resolve_all_payloads` |
| P2-9 IO 放大 | gpkg 终态单次哈希（随 P1-5）；其余记录为已知取舍（inventory/zip 各一趟为台账与压缩必需） | REVIEW_R1 记录 |
| P2-10 栅格 idx 错位 | 导入侧 `_find_map_raster_package_path` 只对 raster_ref 非空绑定计数（与导出一致） | 代码双侧口径一致 |
| P2-11 CRS 捏造 | 导入/导出双侧空 CRS 保留空（不写 4326 默认）；gdal 层无 CRS 即无标注 | exporter/importer 双侧 |
| P2-12 org 越权面 | `resolve_package_upload` 收紧 owner-only | test_r1_fixes.py::TestP2_12OwnerOnly |
| P2-13 裸 `<a>` 下载 | 前端改 `downloadWithAuth`（fetch→blob 带 Bearer） | eslint+tsc 绿；组件接线 |
| P3-14/15/16/17/20/21 | receipt 参数删除；manifest 按 plan.arcname 读取；session_ref 项目可导出；.tiff 计划口径；前端类型拆分；session 主体签名下载 URL | 各处代码 + 全量回归 |
| P3-18 孤儿清扫 | 提交时 TTL（>24h）清扫 `.staging`/`imp-*` | exporter `_sweep_stale_staging` |
| P3-19 幂等竞态 | **已知取舍**：与 data_delivery 同款（进程内 `_ACTIVE_RUNNERS` + DB 幂等键），跨进程双跑窗口与既有域一致，不在本线扩大改动 | 记录在案 |

## R2 复验记录

- 远端复核：origin/master 无漂移（f34cb69dd）；无本线主题新 PR/issue
- 冲突矩阵（changed-files 交集）：#2290 零交集；#2291/#2292 共享 main.py、tools/__init__.py、messages.ts（+#2291 api-docs.md）——全部为一行式 additive 注册，任何合并顺序可安全解冲突
- 工作树干净；`git diff --check` 零空白问题
- 关键 journey 复跑（R1 修复后新鲜执行）：roundtrip/inter op/security/r1_fixes/routes/pi_tools **33 passed**；前端 client 5 passed；全量 90 passed, 1 skipped
- ruff（本任务全部文件）0 问题；eslint 0 警告；tsc 无新增错误

## 结论

R1 全部 P0/P1/P2 已修复并锁定回归；P3 除一项记录为已知取舍外全部修复。无已知未解决 P0/P1/P2。可交付 PR。
