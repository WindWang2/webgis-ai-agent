# SCOPE — 04 线地理统计图符（草稿，待代码审查后细化）

## 所有权（首选路径，实际以调查校正）
- 新域模块 `diagram_symbols`（后端 app/lib/cartography/ 下 + 前端对应专属 adapter，命名查重后定）
- model_library/model_packs 局部新模型、design_candidates 局部候选、visualization_plan 增量
- representation_binding 增量合同、MapSpec additive 字段 + 前端专属 renderer adapter

## 非目标（硬边界）
- 不重建：chart_panel 浮动面板、#2274 多尺度综合、#2278 设计系列、#2273 数据融合、#2277 工作流、G21 OD flow、05 线发布预检、03 线联动视图
- 不新增：独立 MapSpec、第二套图表系统、综合引擎、发布流水线、地图事实源
- 不改 vendor/pi gitlink、宿主桥、native extension

## 共享入口接线原则
- registry/路由/locales/OpenAPI 仅必要薄接线，逐处记录原因

## 资源纪律
- 编译/重测单任务 ≤ -j4；与其他线共享 /tmp/webgis-heavy.lock（flock）错峰
- pytest 单进程为主，确认安全后最多 -n 2
