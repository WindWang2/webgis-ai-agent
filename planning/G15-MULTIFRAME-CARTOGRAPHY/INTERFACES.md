# G15 INTERFACES — 共享接线清单（对兄弟任务）

## 我拥有的面（其他任务消费须经这些契约）

- `frame_product.v1` schema（MapSpec 顶层块）—— G16 如需 frame 级标注上下文，读
  `frame_product.frames[].frame_id` + resolver 的有效文档（勿自建第二解析器）。
- `app/services/gis_harness/frame_products/`：
  - `resolver.resolve_frame_document(document, frame_id)` — 单帧有效文档（G16 标注渲染的输入面）
  - `shared_expression.compute_shared_expression(...)` — 共享分级（含资格判定）
  - `service.bind/plan/update/unbind/status` + `verification.verify_frame_product`
- PublicationIR `driver="product"` + `PageLayoutIR.frame_slots`（消费方：导出路由/批量图册可后续接入）
- 工具面 `frame_product_{plan,bind,update,status,unbind}`（capabilities 已注册）
- 前端 `frontend/lib/cartography/frame-product.ts`（投影 + 用户面 CAS 提交）与 `frame-sync.ts`（N 方同步组纯函数）

## 我消费的面（经既有契约，未修改其语义）

- MapSpec CAS：`apply_gis_mutation` / lifecycle engine（SetLayoutIntent.frames 是加法写面）
- temporal：`detect_time_binding` / `apply_time_window_to_doc` / `normalize_boundary_value`
- feature_pages：`evaluate_where` / `parse_where_param`（where AST 词表零新增）
- classify/thematic_spec：`classify_values` / `resolve_thematic_colors` / `spec_to_paint`
- publication_export：`compile_publication_page` 接缝（新增产品分支）；`assemble_pages_pdf`（body 可为 HTML 体的最小扩展）
- ToolRegistry：`@tool` + bootstrap 登记 + deref skip 名单（+2 行）

## 冲突段披露（对 #2032 UI 重设计）

- `frontend/components/sidebar/map-studio-tab.tsx`：tablist 新增第三个 tab 按钮（约 20 行）+ panel 区域三分支化；未动主题/布局/面板宽度。
