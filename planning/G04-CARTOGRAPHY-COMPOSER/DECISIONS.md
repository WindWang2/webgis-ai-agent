# G04-CARTOGRAPHY-COMPOSER DECISIONS

## D1(#1999)打印档导出任务携带真实求解结果

- `export_thematic_map`/`export_batch_maps` 增加可选 `session_id` 形参(dispatch 自动注入);`paper_size ∈ {a4,a3}` 时按 `f"{ps}_{ori}"` 组装 page_profile,经 `evaluate_constraint_layout(components, page_profile=...)` 在**任务创建时**对会话 MapSpec 组件真实求解,结果进任务 params(`constraint_layout` 有界报告 + `page_profile`)。屏幕档保持 `not_evaluated` + 既有 NOTE(不虚构)。
- 前端 `ExportRequest` 增加可选 `constraint_layout` 报告;`runExport` 在 `buildExportChrome` 前经 `applyConstraintLayoutToSpec`(纯函数,新模块)应用到**导出时副本**:placement_zones → position/anchor;suppressed_ids(非 key/非锁/非浮动)→ enabled=false。持久 MapSpec 零触碰(渲染期事实,不落第二事实源)。
- 前端 `vectorPdfRequestFromSettings` 从 `paperSize`/`orientation` 推导 `page_profile`(a4_landscape 等)传入 `POST /api/v1/export/vector-pdf`(路由已支持,缺调用方)。`api/publication.ts` 增加 `page_profile` 字段。
- 回执:注册诊断码 `constraint_layout_evaluated`(三方门:词表 + 发射器 `frontend/lib/map-kit/exporter.ts`),画布链上传 diagnostics 携带 → 服务端 sidecar 持久化。
- 不切换 format=pdf 到矢量命令(保持 WYSIWYG 画布链与回退语义;矢量链另行受益于 page_profile 推导)。

## D2(#2000)求解结果不落第二事实源

- `cartographer.py` 停止把 `constraint_layout`/`constraint_layout_report` 写进持久 MapSpec `layout`(报告走 warnings/ref 载荷);组件 position/anchor 保留(一等事实)。
- `mapspec_svg_chrome_layout` 退役全局标记门:`_constraint_chrome_xy` 以**组件自身锚点 != 历史锚点**为门(锚点是一等事实);`_constraint_layout_active` 删除。默认导出(历史锚点)字节不变;求解克隆(非历史锚点)行为不变;用戶手动挪动的组件现在导出遵循其锚点(user-wins 修正)。
- 既有锁定旧行为的测试(`test_pi_gis_usability_v2.py` 中带 marker 的 spec)改为去 marker、锚点语义断言(输出不变)。

## D3(#2002)族锁

- `prepare_publication_document` 改用 `app/contracts/workbench_locks.is_entity_locked`(含 `__` 双向族语义,与 lifecycle/repair 同一谓词);`user_lock_kept` 如实列出族命中。

## D4(#2003)矢量链指北针随 bearing 旋转

- `svg_marginalia.render_north_arrow` 增加 `bearing: float = 0.0`,组级 `transform="rotate(-B, cx, cy)"`(N 字随组旋转,与 live `rotate(-ctx.bearing)` 及 canvas `bearing=-mapBearing` 同语义);缺省 0 → 输出字节不变。
- `mapspec_to_svg` 读 `view.bearing` 传入两个调用点。

## D5(CM-01)判选输入单一投影器(W2)

- 新模块 `app/lib/cartography/selection_context.py`:`build_task_cartography_context(...)`/`build_design_task_context(...)` 单一权威投影;planner.py 与 tool_map_product.py 双侧接线;一致性 fixture 锁两侧。

## D6 兼容与回滚

- 全部为加性变更或行为修正;新码均 closed-vocab;无 schema 破坏。回滚 = revert 对应 commit。
- 资源红线:pytest 不加 -n;vitest 按文件分批。
