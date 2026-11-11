# G18 PR 说明（PR body 源文件）

## 问题

产物中心此前只能复制引用/校验和，没有数据下载端点；分析数据无法成为可交付给
其它 GIS 的真实成果。普通数据导出只有客户端整包上传的 `/export/geojson`
（50 MiB 帽、无范围/字段/CRS/保真协商、无回执）。GPKG/GeoParquet/CSV/
Shapefile/FlatGeobuf 用户交付路径完全缺失。

## 用户获得的行为

- 从**现有会话数据引用、fabric-parquet 磁盘工件、项目产物修订或标准表格产物**
  出发（源锚确定 + 版本绑定），选择**范围（当前快照全量 / 稳定 ID 集 / typed
  谓词重放）、字段、格式与目标坐标系**，预览**保真能力与损失声明**，执行
  **可取消的交付任务**，获得**真实可下载文件 + 校验和 + 数据版本 + 内容清单**。
- Pi 通过标准工具完成同样工作（查看格式限制 → 预览 → 提交 → 查询收执），
  经真实 ToolRegistry 分发。
- 成果中心与数据源面板新增薄入口；完成后刷新/项目重开均可恢复下载。

## 实现边界（显式不做）

地图 PNG/PDF/SVG 导出、PublicationIR/atlas/StoryMap、G17 导入、G19 计算列、
G20/G21 渲染、#2022 外部刷新、第二套 agent loop/registry/store、全局 UI 重设计。
不等待 G12–G17、G19–G21 合入；全部路径基于当前 master 已有源类型。

## 主要变更

- 新域 `app/services/data_delivery/`：contracts（真实驱动探测/硬帽/词汇表）、
  source_resolver（4 类源锚 → 授权快照 + 身份锚复核）、scope（完整性闸）、
  loss（逐格式损失）、writers（6 格式真实写入器）、store（原子发布/durable）、
  service（durable job + 串行 writer + 两段式取消 + 反读校验）。
- `app/schemas/data_delivery_schema.py`、`app/api/routes/data_delivery.py`、
  `app/tools/data_delivery_tools.py`；main.py 一行挂载 + tools 一条注册。
- 前端：`lib/api/data-delivery.ts`、`components/data-delivery/`（对话框 +
  4 例组件测试）、artifact-center / dataset-inspector 薄入口、i18n namespace。
- 修复基线既有失败：`materialize` 的 `_materialize_geoparquet_result` 补
  `path/bytes/content_sha256/durable` 键（test_geoparquet_fast_lane_v5:308
  KeyError，干净 master 即失败）。
- `data_object` kind 白名单 additive 扩展 `data_delivery`。

## 已执行命令与结果（本机，2026-11-11）

| 命令 | 结果 |
|---|---|
| `python -m pytest tests/delivery -q -p no:cacheprovider --no-cov` | **57 passed**（writers oracle 10 / service flow 23 / recovery faults 7 / routes+tools 8 / review fixes 9） |
| `python -m pytest tests/unit/test_geoparquet_fast_lane_v5.py` | **15 passed**（含基线失败修复） |
| `python -m pytest tests/test_export_geojson.py tests/test_map_export_download_auth.py` | **10 passed** |
| contract/snapshot：api_compatibility / field_contract / response_model_coverage / api_docs_drift / endpoint_scope_matrix | **28 passed** |
| `cd frontend && npx tsc --noEmit` | clean（G18 文件零错误） |
| `npx vitest run components/data-delivery test/i18n` | **36 passed** |
| `git diff --check` | clean |

性能实测（dev fixture，本机）：3 要素交付 GeoJSON/GPKG/GeoParquet/CSV 均
<1s；100k 行 parquet 源 ids 范围有界样本读取 0.011s/0.1MB（修复前 6.42s/
92.7MB）；fast path 字节级一致（无 FC 中间体）。资源纪律：批 2000 行、writer
全局串行、行帽 200k、输出 512MiB、临时盘 1GiB、DataObject 预算 256MiB。

## 独立评审与修复

- Round 1（独立 reviewer，未参与实现）：conditional pass，3×P1（ids 范围
  parquet 整表读取、回执损失漏报、dispatch_spec 指向不存在的 Celery task）
  + P2（MAX_OUTPUT_BYTES 未强制）→ 全部修复 + 7 例回归。
- Round 2（第二位独立 reviewer）：**PASS**；残留 P2 NEW-1（字段名损失码
  重复/误标）+ P3 NEW-2（copy_mode 未声明）/NEW-4（精度选项对二进制格式
  静默无效）→ 全部修复 + 回归。记录见 planning/g18-dataset-delivery/REVIEW.md。

## 与其他 PR 关系

- 无功能重叠（无在途 data delivery PR）。与 #2022（G10 数据刷新）的
  `tests/quality/snapshots/openapi.json` 存在再生成冲突面：master 上该快照
  本就陈旧（早前合并未刷新），本 PR 的刷新把既有陈旧项一并重排 —— G18 自身
  增量可证明 additive（+5 路径 / 0 净删除），建议合并时以本 PR 版本为准或
  双方各再生成一次。前端仅新增组件 + 薄入口，不动 #2032 的布局/主题文件。

## 迁移/回退

纯 additive：删除本 PR 全部新增文件 + 还原 6 处接线（main.py、tools/__init__、
materialization 4 键、data_object kind、snapshot/docs 重生成）即回到现状。
无 schema 版本迁移、无 DB 迁移（复用 analysis_tasks）。已发布交付文件位于
DATA_DIR/data-delivery/，回退不影响既有会话/图层/产物。

## 未测限制

- 真实 Playwright 浏览器旅程未跑（前端以组件测试 + tsc + i18n 契约覆盖）；
  Celery/Redis 生产多 worker 部署下，进程内 runner 的跨进程取消靠 DB
  `cancel_requested_at` 探针兑现（既有两段式合同），未在真实多 worker 环境
  实测。
- FlatGeobuf 多批 append 仅在 GDAL 3.x（本机 pyogrio 0.12.1）验证。
- 环境阻塞（既有，非本任务）：ADR 编号 0286/0287 撞号契约测试、
  gis_tool_conformance fcntl（Windows）—— 干净 master 同样失败，已核实。
