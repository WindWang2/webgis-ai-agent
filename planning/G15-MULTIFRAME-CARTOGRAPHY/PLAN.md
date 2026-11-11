# G15 PLAN

## 架构决策（基于已核实的基线）

**核心模型：一份权威 MapSpec + `frame_product` v1.9 additive 顶层块 + 复用 `layout.frames` 为画幅身份登记表。**

- 画幅身份 = `layout.frames[].id`（既有 MapSpecFrame）。产品块 `frame_product.frames[]` 通过 `frame_id` 引用画幅身份，并承载产品语义（数据绑定、同步组、图例策略）——画幅与页面分开建模（D2 不变式），旧 atlas 语义零变化。
- 写入面：新 `SetFrameProductIntent`（config/patch/remove 三互斥用法，镜像 SetTemporalViewIntent），经 `apply_gis_mutation` CAS；handler 写 `mapspec["frame_product"]`。
- 有效文档解析（WP04）：`app/lib/cartography/frame_resolver.py` 纯函数 `resolve_frame_document(document, frame_id)` = `_apply_frame_overrides` 语义 + 时间窗（复用 temporal time_filter）+ frame 数据绑定 where 过滤。帧间不共享可变结构。
- 共享表达（WP05）：`build_graduated_spec` 加 `shared_breaks` 可选参数（additive）；共享组以跨画幅真实数据并集分类一次，记录 domain_status complete/partial/unknown 与资格理由。计数 vs 比率拒绝共享（单位/分母/字段含义三查）。
- 出版（WP10）：PublicationIR `PageLayoutIR` 加 `frame_slots: List[FrameSlotIR]`（additive，None = 既有语义）；`frame_product` 在场且请求产品页时产出单页多槽 IR；导出循环复用 `compile_publication_page` 的单帧编译器在独立 clip/坐标空间渲染每槽，SVG id/clipPath 按槽命名空间化。
- 前端（WP07/WP08）：`frontend/lib/cartography/frame-product/` 契约投影 + `components/workbench/frame-product-panel.tsx`（挂 map-studio-tab，最小接线）；多画幅查看器活跃 MapLibre ≤2 + 快照占位；相机同步组复用 comparison-sync 纯函数族。
- Pi 工具（WP09）：`app/tools/frame_product_tools.py`（inspect/plan/apply/update/status），注册进 `_TOOL_MODULES`；上下文投影当前激活画幅/同步组/共享分级。
- 终验（WP11）：在既有 ACK/evidence 面上加有界 frame 证据（frame_id+revision+状态），不建第二完成状态机。
- WP12：真实基准 2/4/6 幅 + `small_multiple_map` native 化（几何/工件/导出组合达标后）+ ADR-0288。

## 工作包顺序与纵向路径策略

先打通纵向：WP02 契约 → WP04 resolver → WP05 共享分级 → （schema roundtrip + known-answer tests）→ WP03 计划器 → WP10 出版 → 导出真实 PNG/SVG/PDF 检查 → WP07/08 前端 → WP09 工具 → WP11 终验 → WP12 基准/文档。

每轮：实现 → 定向测试 → 账本更新 → commit。

## 资源纪律

- pytest 从 worktree cwd（venv 共享主工作区）；`--no-cov -q` 提速；重型阶段用共享 `webgis-heavy-stage.lock`（flock）。
- 前端 vitest workers 1-2。
