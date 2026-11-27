# INTERFACES — 工程包格式合同 v1（webgis-project-package）

## 包容器布局（ZIP，根目录）

```
manifest.json                      # 本合同实例（≤1MiB）
standard/layers.gpkg               # 多图层矢量（layer 名 = dataset logical_id；无矢量时缺席）
standard/rasters/<logical>.tif     # 栅格（COG/GeoTIFF）
standard/tables/<logical>.csv|parquet
artifacts/<logical>.<ext>          # 成果载荷（geojson/tif/…）
maps/<logical>.mapspec.json        # 原生 MapSpec 文档
styles/<logical>.style.json        # MapLibre style 合法子集（可缺席）
DISCLOSURE.md                      # 人读披露（manifest.disclosure 镜像）
```

## manifest.json 顶层 schema

```jsonc
{
  "format": "webgis-project-package",
  "format_version": 1,
  "package_id": "pkg-<32hex>",
  "created_at": "<RFC3339 UTC>",
  "origin": {                       // 溯源专用；导入端禁止沿用 owner/org/授权
    "system": "webgis-ai-agent",
    "project_id": "proj_...",
    "project_name": "...",
    "app_version_hint": "..."
  },
  "project": { "name": "...", "description": "...", "metadata_json": {} },   // 无 owner/org/凭据
  "selection": { "kind": "project|map|analysis", "requested": {...} },
  "reproduction": {
    "level": "full_offline_recompute | view_only | partial",
    "counts": { "embedded": n, "external_reference": n, "redacted": n, "missing": n, "unsupported": n }
  },
  "datasets": [{
    "logical_id": "ds-<slug>",
    "name": "...",
    "classification": "embedded | external_reference | redacted | missing | unsupported",
    "source_type": "...", "crs": "EPSG:4326", "geometry_type": "Point",
    "feature_count": 0, "bbox": [w,s,e,n] | null,
    "schema_profile": {...}, "version_fingerprint": "<sha256>",
    "license": { "spdx": "..." | null, "note": "...", "distributable": true|false|null },
    "payload": { "path": "standard/layers.gpkg", "member": "ds-<slug>", "sha256": "...", "bytes": n },
    "external": { "uri": "...", "reason": "..." }        // external_reference 时
  }],
  "workflows": [{
    "logical_id": "wf-<slug>", "name": "...", "version": 1,
    "graph_fingerprint": "<sha256>", "graph_spec": {...}, "inputs_schema": {...},
    "input_bindings": { "<input>": {"kind": "dataset|artifact", "logical_id": "..."} },
    "runnable": true|false, "not_runnable_reason": "..."
  }],
  "artifacts": [{
    "logical_id": "art-<slug>", "artifact_type": "dataset|raster|map|report", "format": "...",
    "crs": "..." | null, "content_fingerprint": "<sha256>",
    "payload": { "path": "artifacts/...", "sha256": "...", "bytes": n } | null,
    "lineage": { "parents": ["..."], "producing_tool": "...", "tool_version": "...",
                 "parameters": {}, "workflow": "wf-..." | null }
  }],
  "maps": [{
    "logical_id": "map-<slug>", "mapspec_fingerprint": "<sha256>",
    "mapspec_path": "maps/....mapspec.json",
    "style_path": "styles/....style.json" | null,
    "source_bindings": [{ "source_key": "...", "dataset": "ds-..." | null, "artifact": "art-..." | null }],
    "map_product": { "fingerprint": "...", "lineage_kind": "authored|..." }
  }],
  "files": [{ "path": "...", "sha256": "...", "bytes": n, "mime": "..." }],   // 台账（不含 manifest.json 自身）
  "disclosure": {
    "not_included": [{ "kind": "...", "name": "...", "reason": "..." }],
    "external_links": [...], "unsupported": [...]
  },
  "fidelity_receipt": { "datasets": [{ "logical_id": "...", "fields_verified": true,
    "crs_match": true, "feature_count_match": true, "geometry_match": true,
    "details": {...}, "verdict": "exact|semantic_equivalent|partial" }] }
}
```

## 分类语义（D4）

| classification | 含义 | 导入行为 |
|---|---|---|
| embedded | 载荷在包内且哈希可校验 | 注册为 ProjectDataset（新 ID） |
| external_reference | 不在包内；URI+义务在 manifest | 注册元数据引用；不自动联网取数 |
| redacted | 许可/授权不允许携带 | 只登记名称与原因 |
| missing | 源已 detached/不可解析 | 登记 tombstone 原因 |
| unsupported | 本系统能力外（如云模型任务） | 登记并披露 |

## 导入侧强制边界（服务端常量，非仅 manifest 建议）

- manifest ≤ 1 MiB；条目数 ≤ 2000；总展开 ≤ 2 GiB（可配）；单条目 ≤ 512 MiB
- 单条目压缩比 > 200 拒绝；路径拒绝绝对路径/`..`/反斜杠穿越/非 NFC/重复；仅常规文件（无符号链接/设备）
- 扩展名白名单：.json .geojson .gpkg .tif .tiff .csv .parquet .md .fgb .png；拒绝嵌套压缩包（.zip/.tar/...）
- 每文件 sha256 对账 manifest.files；缺失/不符 → 拒绝该包或降级为显式部分导入（用户确认）

## 服务面签名（REST 前缀 /api/v1）

```
POST /projects/{pid}/exchange/inspect     # 零写入：闭包+分类+预估体积+分级
POST /projects/{pid}/exchange/plan        # 零写入：完整 manifest 预览（含逻辑 ID）
POST /projects/{pid}/exchange/export      # 提交 durable job（幂等键）
GET  /project-exchange/exports/{pkg_id}   # 状态 + receipt
POST /project-exchange/imports/inspect    # upload_id → manifest 摘要/冲突/许可
POST /project-exchange/imports/plan       # 目标/映射预览
POST /project-exchange/imports            # 提交导入 job（默认新项目 staging）
GET  /project-exchange/imports/{job_id}   # 状态 + 恢复报告/映射
```

## Pi 工具面（deferred 超集注册）

```
project_exchange_inspect(project_id, selection?) -> 闭包摘要+分级
project_exchange_export_plan(project_id, selection?) -> manifest 预览（有界摘要）
project_exchange_export(project_id, selection?, name?) -> job ref（state_mutation）
project_exchange_import_inspect(upload_id) -> manifest 摘要+冲突
project_exchange_import_plan(upload_id, target_name?) -> 计划
project_exchange_import(upload_id, target_name?) -> job ref（state_mutation）
```
