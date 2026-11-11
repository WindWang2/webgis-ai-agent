# 数据成果交付（Data Delivery，G18）

用户从现有会话数据引用、fabric-parquet 磁盘工件、项目产物修订或标准表格产物出发，
选择导出范围、字段、格式与目标坐标系，查看保真能力与损失声明，执行可取消的交付任务，
最终获得**真实可下载文件 + 校验和 + 内容清单**。Pi 通过标准工具完成同样工作。

- 域模块：`app/services/data_delivery/`（contracts / source_resolver / scope / loss /
  writers / store / service）
- 契约：`app/schemas/data_delivery_schema.py`（DeliverySpec v1 / Preview / Receipt）
- 路由：`/api/v1/data-delivery/*`（capabilities / preview / jobs / download）
- 工具：`delivery_capabilities` / `preview_data_delivery` / `submit_data_delivery` /
  `get_data_delivery`
- 前端：`frontend/components/data-delivery/delivery-dialog.tsx`（产物中心行入口 +
  数据源面板物化后入口）

## 用户流程

1. **预览**（无副作用）：源身份（行数/CRS/字段/几何/完整性证据）、逐格式真实能力、
   损失清单（需确认项）、阻断项（缺 CRS/格式不可用/选择截断/行数超限）与估算。
2. **提交**：服务端复检 + `acknowledged_losses` 必须覆盖全部 required codes；
   幂等键防重复制造输出；durable job（任务中心可见，`DELETE /api/v1/jobs/{id}` 取消）。
3. **回执**：实际行数/字节/成员 sha256、真实发生的变换与降级、源身份与范围证据、
   writer 版本、DataObject durable 状态。
4. **下载**：受控路由按 job 归属授权（creator/会话/项目/owner token），安全文件名 +
   精确 MIME + `X-Content-Sha256`；未完成/失败一律 404。

## 源类型（`DeliverySourceRef.kind`）

| kind | 说明 | 身份锚 |
|---|---|---|
| `session_ref` | 会话 FC ref（materialize 产物） | 内容指纹 + content_revision |
| `fabric_parquet` | `ref:fabric-parquet/<id>` 磁盘工件 | 文件 sha256 |
| `project_artifact` | 项目产物修订（缺省 head / 显式 revision_no） | content_sha256（durable） |
| `table_rows` | rows 型表格 ref | 内容指纹 |

执行前复核身份锚，漂移 → `SOURCE_REVISION_CHANGED`（不混读两版本）。

## 范围与选择完整性

- `full`：当前快照全量（标签如实披露 truncated/sample，不称源全集）。
- `ids`：稳定 ID（feature.id → `_wfid` 自动探测；缺失 ID 逐项披露；≤500）。
- `filter`：typed 谓词（feature_pages AST）+ bbox，同快照精确重放。
- `selection_matched_count > len(ids)` 或 `ids_truncated` 且无谓词 → 阻断完整选择；
  仅 `subset_ack` 后按「所列子集」交付并在回执标注。

## 格式与硬帽

核心：GeoJSON（RFC 7946，流式手写，顶层 id 保留）、GeoPackage（pyogrio 批量
append，混合几何单层 Unknown 或按类型分层）、GeoParquet（pyarrow + geo metadata；
fabric-parquet 无变换走**字节级安全复制 fast path**）、CSV（字段顺序稳定 +
schema sidecar + spreadsheet-safe 模式）。受限：Shapefile ZIP（字段名 ≤10 字节
显式映射 + .cpg/.prj 组成文件 + 混合几何分组）、FlatGeobuf —— 能力以运行时
驱动探测为准（`GET /data-delivery/capabilities` 与工具/前端同源）。

硬帽（`app/services/data_delivery/contracts.py`）：行 200k / 输出 512MiB /
临时盘 1GiB / 字段 256 / ID 500 / 批 2000 行 / DataObject 预算 256MiB；
writer 全局一次一个。缺 CRS：GeoJSON 一律拒绝（不默认 4326）；GPKG/GeoParquet/
FGB 可 `allow_unknown_source_crs` 确认后如实标注未知坐标系。

## 发布与恢复

staging（`.staging/<id>/`，不可下载）→ 校验（成员 sha256 + 独立读取路径反读行数/
CRS/ZIP 组成）→ 同卷原子更名终态目录 → DataObject（CAS，预算内）→ session 台账
`ref:delivery/<id>`（+ 可选项目 `Artifact(type=data_delivery)` 与父源 lineage）→
`receipt.json`/`manifest.json` 落包。取消/崩溃清理 staging，无「半成品 + 正确
扩展名」终态；完成交付只依赖磁盘 + DB + DataObject，进程重启后仍可下载。
