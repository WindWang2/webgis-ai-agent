# G18 INTERFACES — 共享文件接线清单

## 只新增的文件（无碰撞）
本任务主体全部在新命名空间（见 PLAN.md 文件树）。

## 最小 additive 接线（逐处列出）

| 文件 | 改动 | 兼容约定 |
|---|---|---|
| app/main.py | `include_router(data_delivery_routes.router, prefix="/api/v1", tags=["Data Delivery / 数据交付"])` | 一行挂载，紧跟 data_fabric 之后 |
| app/tools/__init__.py | `_TOOL_MODULES += ("app.tools.data_delivery_tools", "register_data_delivery_tools")` | 一条注册项 |
| app/services/data_fabric/materialization_service.py | `_materialize_geoparquet_result` 返回体补 `path/bytes/content_sha256`（修复既有测试 KeyError 基线失败） | 纯 additive 键；老消费者按缺省忽略 |
| frontend/components/sidebar/project/artifact-center.tsx | 行操作区加「交付数据」按钮（新组件 `<DataDeliveryDialog/>`，lazy 局部挂载） | 不改布局/主题；#2022 冲突面（project-assets-section.tsx）不触碰 |
| frontend/components/sidebar/data-sources/dataset-inspector.tsx | 物化操作区旁加「导出交付」按钮 | additive |
| frontend/lib/i18n/messages.ts | 登记新 namespace `dataDelivery` | 一条登记 |
| frontend/messages/{zh-CN,en-US}/dataDelivery.json | 新文件 | — |
| tests/quality/snapshots/openapi.json | 新路由进快照（按仓库既有流程再生成或补登） | 若快照测试要求，按需更新 |

## 消费的既有稳定接口（不改签名）

session_data_manager.get/get_ref_descriptor；fabric_parquet_path/is_fabric_parquet_ref；register_artifact；vector_carrier.{features_to_arrow, table_to_geoparquet, geoparquet geo helpers, arrow_available}；feature_pages.{evaluate_where, parse_bbox_param, parse_fields_param}；DurableJobStore.{create/create_sync/get/get_by_idempotency_key/mark_running/transition/request_cancel}；cancellation registry；publish_data_object/resolve_data_object；read_content（promotion store）；dataset fingerprint service；ToolRegistry @tool；ProjectService.get_project_with_auth。

## 与兄弟任务的分区

- 不改 Pi vendor/RPC/bridge/extension（G12）；不改 availability/eligibility/ToolDispatchService（G13）；不改 MapSpec/frame/label（G15/G16）；不改上传协议（G17）；不改 raster 样式/OD（G20/G21）；不触碰 #2022 的 refresh 域文件与 #2032 的全局 UI 文件。
- MapSpec schema 零改动（本域不涉及 schema 版本/ADR 编号占用）。
