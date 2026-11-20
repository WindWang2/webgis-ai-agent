# G18 PLAN — ref/revision 驱动的多格式数据成果交付

## 架构（新增命名空间，最小共享接线）

```
app/schemas/data_delivery_schema.py        # DeliverySpec v1 / Preview / Receipt / Status 模型
app/services/data_delivery/
  contracts.py      # 格式/损失码/错误码/硬帽常量 + 能力探测（真实驱动探测）
  source_resolver.py# 4 类源锚 → SourceSnapshot（身份指纹+有界批次读取器+CRS/schema/完整性）
  scope.py          # full / ids / typed filter+bbox 范围协商与完整性合同
  loss.py           # 逐格式损失分析（字段名截断/嵌套 JSON 化/混合几何/Z/时区/公式前缀…）
  writers.py        # GeoJSON(流式手写)/GPKG/GeoParquet/Shapefile ZIP/FlatGeobuf 写入器
  csv_delivery.py   # CSV + schema sidecar + spreadsheet-safe 模式
  service.py        # preview / submit / execute / status / download 编排 + receipt
  store.py          # delivery 目录布局、原子发布、manifest/receipt 落盘、DataObject 发布
app/api/routes/data_delivery.py            # /api/v1/data-delivery/*（capabilities/preview/jobs/download）
app/tools/data_delivery_tools.py           # 4 个标准工具（capabilities/preview/submit/get）
frontend/lib/api/data-delivery.ts
frontend/components/data-delivery/*        # 主体对话框 + 状态/结果组件
frontend/components/sidebar/project/artifact-center.tsx   # 薄入口（additive）
frontend/components/sidebar/data-sources/dataset-inspector.tsx # 薄入口（additive）
frontend/messages/{zh-CN,en-US}/dataDelivery.json
```

## 执行模型（复用既有 jobs，不建新队列）

- 任务行 = `AnalysisTask`（task_type=`data_delivery`，job_kind=`analysis`，idempotency_key=`data_delivery:<owner>:<spec 摘要>`），经 `DurableJobStore` 生命周期迁移。
- 进程内执行体：`asyncio.create_task` + 全局 `asyncio.Semaphore(1)`（一次一个 writer）；取消走既有两段式（DB `cancel_requested_at` + `cancellation_registry` token；DELETE /api/v1/jobs/{id} 直接可用）。
- 进程重启 → running 行心跳超时转 stale（既有语义）；completed 交付由磁盘 + DB + DataObject 恢复下载，不需重算。
- 阶段：resolve(身份指纹复核) → scope → transform(CRS) → write(staging) → verify(反读 oracle 摘要) → publish(原子 rename + DataObject + register_artifact + receipt) → terminal。每批 ≤2000 行，批间检查取消/预算。

## 源锚（WP03）

1. `session_ref`：`session_data_manager.get(session_id, ref)`（FC payload）；descriptor 供完整性（truncated/total）。
2. `fabric_parquet`：`ref:fabric-parquet/<id>` → `fabric_parquet_path` + artifact registry 记录 + pyarrow 分批读。
3. `project_artifact`：DB `Artifact` + `ArtifactRevision`（缺省 head，可选 revision_no/pinned）→ `read_content`/blob 字节；FC 或 rows 表格两种 payload 形态。
4. `table_rows`：rows 型 payload（列由行扫有界推断）。
身份 = content fingerprint（FC: dataset_fingerprint；文件: sha256；revision: content_sha256）。执行前复核，不匹配 → `source_revision_changed`。

## 范围（WP04）

- `full`：当前快照全量（标签诚实：行数 + truncated/sample 披露；sample 源只能「当前快照」标注）。
- `ids`：稳定 ID 列表（feature.id 或 `_wfid`；缺 ID → 拒绝该 scope）+ 缺失 ID 逐项披露。
- `filter`：typed predicate（feature_pages.evaluate_where 语义子集）+ bbox；冻结于提交规格，同快照重放 → 完整选择（场景 25/27）。
- ids 截断（matched_count>len(ids) / ids_truncated）且无 predicate → 阻断「完整选择」，仅可显式确认「所列子集」（receipt 记录 subset_of_selection）。

## 格式与损失（WP05/06）

- GeoJSON：RFC 7946 流式手写（WGS84；缺 CRS 或需变换 → pyproj 真实重投影；精度削减为显式有损选项，默认关闭）；保留顶层 `id` + `_wfid`。
- GeoPackage：pyogrio 批量 append；混合几何单层 Unknown 或按类型分组多图层（策略显式）；嵌套属性 JSON 串化披露。
- GeoParquet：vector_carrier 通道（geo metadata + zstd）；同格式无变换 → **fast path 安全复制/指针复用**（无 FC 中间体，源字节身份可追踪）。
- CSV：属性表（可选 WKT/X/Y 列）；schema sidecar；spreadsheet-safe 模式（`=`/`+`/`-`/`@`/TAB/CR 前缀变换并记录）。
- Shapefile ZIP（受限）：字段名 >10 字符需显式映射（自动生成无冲突映射须预览确认）；编码 .cpg(UTF-8)、.prj；混合几何分组多文件；bool/date/int64 降级披露。
- FlatGeobuf（受限）：驱动可用性运行时探测；不可用 → 预览声明 unavailable（不装样子）。
- 能力矩阵来自真实驱动探测（pyogrio.list_drivers + 运行依赖），UI 与工具同源。

## 发布与下载（WP08）

- staging：`DATA_DIR/data-delivery/.staging/<delivery_id>/` → 校验后原子 rename `DATA_DIR/data-delivery/<delivery_id>/`（成员：主文件(s) + manifest.json + receipt.json）。
- DataObject 发布（预算 ≤256MiB，kind=`data_delivery`，owner_scope=session/project）→ durable 身份 + CAS 去重（幂等重交不重算文件）；超预算 honest `oversized`。
- register_artifact（session 台账）+ project_id 存在时写项目 `Artifact`(type=data_delivery) + `ArtifactLineage`（父=源项目产物，如有）。
- 下载：`GET /data-delivery/jobs/{id}/download?member=`——DB 行授权（creator/org/session owner_token/project 成员），安全文件名、精确 MIME、Content-Disposition、sha256/长度头；未发布/失败 → 404，不泄漏存在性。

## API（WP09）

- GET `/data-delivery/capabilities`（可选认证；纯静态探测）
- POST `/data-delivery/preview`（DeliverySpec → Preview：源身份/schema/行数/完整性/逐格式可用性/损失/阻断项/需确认项）
- POST `/data-delivery/jobs`（spec + acknowledged_losses + 可选 project_id；服务端复检阻断项；202 + job_id）
- GET `/data-delivery/jobs?project_id=` / GET `/data-delivery/jobs/{id}`（状态+receipt）
- GET `/data-delivery/jobs/{id}/download?member=`
- 取消复用 DELETE `/api/v1/jobs/{id}`；`/export/geojson` 与地图导出契约不动。

## 工具（WP09）

`delivery_capabilities` / `preview_data_delivery` / `submit_data_delivery` / `get_data_delivery`（tier=2, domains=["dataset"], produced_refs=["artifact"], side_effect=state_mutation 仅 submit）。经 ToolRegistry 注册；submit 内部强制 preview 校验，不旁路损失确认。

## 验收场景映射（27 条 → tests/delivery/）

见 TEST_MATRIX.md。

## 明确不做

地图 PNG/PDF/SVG 导出、PublicationIR/atlas/StoryMap、G17 导入、G19 计算列、G20/G21 渲染、#2022 外部刷新、第二 agent loop/registry/store、全局 UI 重设计。
