# G15 DEDUP_MATRIX — 以最新 master (816705218) + 本 PR diff 复核

| 能力 | 状态 | 证据（file:line 或测试） |
|---|---|---|
| MapSpecFrame / layout.frames 读写 | present（基线） | `app/lib/cartography/mapspec_schema.py:788-801`；本 PR 补首个**意图写面** `SetLayoutIntent.frames`（此前唯一写者 = simulation 整文档） |
| 请求级多帧导出（ExportRequest.frames → png grid/pdf pages） | present（基线，不动） | `tests/test_export_frames_v5.py` 全绿 |
| atlas 逐帧分页 + batch | present（基线，不动） | `publication_ir.py` frames/category/feature/time 驱动；116 publication/atlas 测试通过 |
| TemporalViewContract（真实时间桶/两期共享分级） | present（基线，消费不复制） | resolver 复用 `apply_time_window_to_doc`/`detect_time_binding`/`normalize_boundary_value` |
| 双视图 swipe/side-by-side + 相机同步 | present（基线） | `comparison-view.tsx`；本 PR 复用 `resolveSyncPair` 纯函数做 N 方推广（`frame-sync.ts`），不改 ComparisonView |
| 产品内多画幅（身份/绑定/编辑/保存/联动/终验） | **本 PR 新增** | `frame_product.v1` 契约 + frame_products 服务族 + 工具面 + 面板 |
| 共享表达资格（count vs rate 等） | **本 PR 新增**（TemporalClassification 只覆盖两期对照） | `shared_expression.compute`；`tests/.../test_shared_expression.py` |
| 同页多画幅出版 | **本 PR 新增** | `PageLayoutIR.frame_slots` + driver=product + `_compile_product_page` |
| 逐画幅终验 | **本 PR 新增** | `verification.py` + goal_satisfaction 证据消费 |
| G11 简报/故事章节（#2021） | owned-by-other | 未触及 |
| G10 数据刷新（#2022） | owned-by-other | 只记录 content_revision 作 stale 信号，不做刷新 |
| UI 全局重设计（#2032） | owned-by-other | 只新增 frames 子页签 + 独立子组件 |
| G13 能力生态（#2073 open） | owned-by-other | 能力包注册走既有 `capabilities/` 域模块模式（与 temporal 同门），不改其管线 |
| G12 Pi 集成 | owned-by-other | 未触碰 bridge/extension；工具走既有 ToolRegistry/bootstrap |
| G14 稳定要素身份 | owned-by-other | 使用现有图层/要素通道；不建第二 ID 系统 |
| G09 参数化批量计算 | 排除 | 只消费已有分析结果字段/时间桶，无参数矩阵/批处理调度 |
| G16 标注覆写 | 边界 | 未改标注求解器；resolver 向渲染器只传范围/画幅上下文 |

## 重复文件清单（与兄弟 PR 的共享面，均已最小 additive）

- `app/lib/cartography/mapspec_schema.py`：仅 v2.0 加法（G16 若加 MapSpecLayerLabel 字段互不冲突）
- `app/services/mapspec/{intents,mutation_handlers,mutation_registry}.py`：新增分支/登记，不改既有分支
- `app/tools/registry.py`：deref skip 名单 +2 行（frame_product_update）
- `frontend/components/sidebar/map-studio-tab.tsx`：三段子页签（与 #2032 的冲突面 = 该文件的 tablist 区域，已限制在新增 tab 按钮 + panel 分支）
- `frontend/lib/map-kit/spec-frames.ts`：追加导出函数，旧函数逐字不动
