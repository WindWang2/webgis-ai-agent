# PLAN.md — G17-IMPORT-WORKBENCH 实现计划

垂直切片优先：先打通「多文件 GeoJSON → inspect → plan → preview → commit → ref」全链，再扩 GPKG/ZIP/CSV/XLSX/纯表/取消/恢复/UI/工具。

## 领域模型（WP02 定稿）

### 状态机
- Batch: `staged → inspected → configured → previewed → committing → completed | partial | failed | cancelled | expired`
- Member: `discovered → selected → planned → previewed → committing → completed | failed | skipped | cancelled`
- 可重试操作：save_plan（重写即新版本）、preview、commit（按成员）。

### 标识
- `batch_id = "ib-" + uuid32`
- `source_id = "src-" + <文件序号>`，附 `sha256`、`original_name`、`size`
- `member_key`（稳定、与 UI 排序无关）：
  - 顶层文件整体：`f:{source_id}`
  - GPKG/KML 图层：`c:{source_id}:{container_kind}:{layer_name}`
  - ZIP shapefile 集：`c:{source_id}:zipset:{zip_dir}:{basename}`（zip_dir 为容器内相对目录，空目录用 `.`）
  - XLSX sheet：`c:{source_id}:sheet:{sheet_name}`
  - 重检时以 `(sha256, member_key)` 对比源身份；bytes 变化 → 计划失效（PLAN_STALE）。

### 解释计划
- `plan_version`（int，每次保存递增）；`plan_hash = sha256(canonical{source_sha256, member_key, interpretation, INTERPRETER_VERSION})[:16]`
- interpretation（按成员）：
  - vector: `{source_crs_declared: str|None, keep_fields: [str]|None}`
  - table: `{mode: auto|points|wkt|table, encoding, delimiter, header_row, x_column, y_column, wkt_column, source_crs_declared, field_types: {col: text|number|boolean|date}, keep_fields}`
- preview/commit 一律校验 `plan_hash` 一致（scenario 12）。

### 提交收据（per member）
`{member_key, plan_hash, status, input_rows, output_rows, rejected_rows, geometry_type, source_crs{value, source: source_metadata|declared|rfc7946_default|unknown}, target_crs, output_ref, output_kind: feature_collection|table, duplicate: bool, error_code, started_at, finished_at, timings}`

### 幂等
- 成员提交键 `(member_key, plan_hash)`：已有 completed 收据且输出 ref 可读（store.get ≠ None）→ 复用（duplicate 语义，不重复挂层/登记）。
- 批次级：commit 请求带 `expected_plan_version`；提交中锁定（内存 asyncio lock per batch + 状态文件乐观版本）。

### Staging 存储
- `DATA_DIR/import_batches/<batch_id>/`：`owner.json`（session_id、user 指纹、created/expires）、`batch.json`（原子写状态）、`sources/<source_id>__<safe_name>`（原始字节，不可变）、`diag/<member>-rejected.jsonl`（超内联上限的拒绝行明细）、`outputs/`（成员解析中间产物 original.geojson，提交后保留供 durable 恢复）
- 过期：TTL 默认 48h；list/get 时惰性清理（只删自己目录）；DELETE 端点显式清理。
- 权限：每次 API/工具操作 `verify_session_owner(session_id)` + owner.json.session_id 匹配；不信任模型传参。

### 预算（配置常量 data_import/budgets.py）
- files/batch ≤ 20；单文件 ≤ 既有 MAX_VECTOR_SIZE(50MB)；batch 展开字节 ≤ 200MB；容器成员 ≤ 64；嵌套 zip ≤ 1 层（嵌套披露 unsupported）
- 预览默认 200 行、硬帽 2000；拒绝行内联 ≤ 100；XLSX 行扫描帽 50k；逐行循环每 1000 行 checkpoint 取消检查
- 同时解析 1 成员（串行 commit 循环）

## 工作包顺序（每轮一个可验收增量）

1. **WP01** ✅（BASELINE.md 行为矩阵 + 侦察报告）
2. **WP02** schemas + models + 状态机 + plan_hash（含单测）
3. **WP03** staging + discovery（真实 GPKG 3 层 / ZIP 双 shapefile 集 / KML / XLSX 多 sheet fixture）
4. **WP04** interpretation（CSV 分隔符/编码/引号/前导零/长整型/WKT/纯表；XLSX sheet+公式缓存）
5. **WP05** vector 成员选择 + 几何构造（layer= 读、X/Y 构点、WKT 解析、空几何/非有限/Z 披露、keep_fields）
6. **WP06** CRS 声明链（source_metadata/declared/unknown 三态；位置预览仅用已验证坐标；.prj 缺失声明通道）
7. **WP07** preview + 拒绝行（与 commit 同解释器；变更即失效）
8. **WP08** commit（逐成员幂等 → IngestPipeline / prefix="table" 纯表 + register_artifact("table")；部分成功收据；血缘 original→member→output）
9. **WP09** API routes + tools（registry.dispatch 真实验证；produced_refs/side_effect/failure_modes）
10. **WP10** 前端 client + workbench UI + dataset-manager 薄挂载 + i18n + vitest
11. **WP11** 取消/恢复/资源（CancellationToken、迟到结果不覆盖终态、重启恢复、staging 过期）
12. **WP12** 22 场景矩阵 + 性能报告 + 文档 + 旧契约回归

## 验收场景映射 → 测试文件

（TEST_MATRIX.md 维护，22 场景全部编号落位）

## 关键技术决策记录

- D1: 不给旧 /upload 增加多文件语义；新 `/api/v1/data-import/batches` 端点族。
- D2: XLSX 生产依赖 openpyxl（read_only + data_only；无缓存值披露 formula_no_cache）；运行时 ImportError → 成员状态 xlsx_unavailable 诚实披露。
- D3: 纯表走 `prefix="table"` ref + `register_artifact(artifact_type="table")`；前端可经既有 table-artifacts 端点取回。
- D4: 空间成员输出 = 解析后 FC dict → `IngestPipeline.ingest(prefix="upload")`，与旧上传同一摄入语义；不重复实现 profile/quality。
- D5: 源 ID 保留：容器成员的原有 ID 字段原样进 properties；无稳定 ID 时收据披露 `source_identity: none`（不铸 _wfid——那是 G14 领域）。
- D6: staging 状态 = 文件系统（DATA_DIR），不新建 DB 表/迁移；owner 校验复用会话归属链。
- D7: 取消 = 内存 CancellationRegistry per (batch, op) + 状态文件标记；进程重启后 committing 态成员复位为 planned（收据缺失即未完成，不显示成功）。
- D8: 前导零/长整型：解释器默认全列读为 str（pandas dtype=str），field_types 显式转换；输出 GeoJSON properties 里 text 字段保持字符串（不经 JS Number）。
