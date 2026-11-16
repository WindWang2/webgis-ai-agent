# G47 — DEDUP_MATRIX

判定：`present` 已有完整实现 / `partial` 有相邻能力但缺本域语义 / `missing` 不存在 /
`owned-by-other` 属其它任务 / `blocked` 外部阻塞。

| # | 需求 | 判定 | 生产调用链证据 | 本任务动作 |
|---|---|---|---|---|
| 1 | 面积∝数量的比例圆通道 | partial | `thematic_authoring/contract.py:113-128` SizeChannel（linear / area_sqrt）→ builder 写 paint | **不重做**；Dorling 复用"半径由面积反算"的数学，但布局与图面自建 |
| 2 | 圆形位移（Dorling）求解 | missing | 全仓无 dorling/displacement solver；`model_packs/presentation_views.py:42-56` 标 planned | 新增 `cartogram_authoring/solver.py` |
| 3 | 布局几何的独立质量核验 | missing | 无 overlap/area-error oracle | 新增 `quality.py`（与求解器分离，只读 artifact） |
| 4 | 示意坐标 vs 地理坐标隔离 | partial | MapSpec 无 diagram 坐标概念；`render_diagnostics.py:134-137` 泛 cartogram 降级 | 布局 artifact 显式 `coordinate_space="diagram"`，禁止进入 MapSpec source/几何分析 |
| 5 | 强版本冻结 | present（他域） | `bivariate_reliability_authoring/snapshot.py:139-222` | 同合同本域适配（几何资格/要素上限），不复用其私有函数 |
| 6 | durable job（取消/重试/重启） | present（基础设施） | `data_operations/runner.py:375-444`、`jobs/lifecycle.py:46-62` | 新增 `JobKind.cartogram_layout` + body（幂等键含计划/源版本/算法） |
| 7 | MapSpec 原子应用 + undo | present | `apply_gis_mutation` + `UpsertLayerIntent`，G41 `service.py:663-693` | apply 写层内 `cartogram_authoring` 证据块（原几何/字段/锁不变） |
| 8 | 持久 artifact / BlobStore | partial | `session_data.store(prefix=...)`；G41 派生 ref | 布局 JSON 存 `ref:g47layout-*`；导出字节由冻结布局按需生成 |
| 9 | SVG/PNG/PDF 出版 | partial | `publication_export.py`（weasyprint）、`print_layout_authoring/compile.py:245-258` 组件分派；无 diagram kind | 新增受控 `diagram` kind + `cartogram_authoring/render.py` 三格式 |
| 10 | 前端图面 | missing | `frame-composer.ts:122-127` 仅降级警告 | 新增 `dorling-diagram.tsx`（真缩放/平移/选择/键盘） |
| 11 | 原地图联动（按 feature identity） | partial | `selection-store.ts:57-95` 稳定身份桥接 | 圆 ↔ 原面用原 feature id 双向选择 |
| 12 | Pi 工具入口 | partial | `app/tools/*` + `@tool`；G41 三工具范式 | 新增 5 工具（inspect/plan/run/status/apply/export） |
| 13 | 泛 cartogram 支持 | owned-by-other / 不认领 | `presentation_views.py:42-56`、`render_diagnostics.py:134-137` | 保持诚实降级；只新增 `dorling_circle` 具体模式 |
| 14 | 符号/纹理资产 | owned-by-other（G40 #2124 已合并） | `app/api/routes/cartographic_assets.py` | 不消费、不复制 |
| 15 | 双变量/可靠性 | owned-by-other（G41 已合并） | `app/services/bivariate_reliability_authoring/` | 不重做 |
| 16 | 多画幅纸面系统 | owned-by-other（G34 已合并） | `print_layout_authoring/` | 仅加 `diagram` kind 的窄 adapter |
| 17 | 跨尺度简化 | owned-by-other（G27 已合并） | `multiscale/generalize.py` | 不做 |
| 18 | 组合筛选（G46） | owned-by-other | 未合并 | 不依赖 |

## 结论

本任务无"已完成"的同域实现可认领；真增量集中在 **Dorling 求解 + 独立质量门 + 示意坐标隔离 +
diagram 图面/出版 + 原图身份联动 + Pi 闭环**。第 1/6/7/9 项为复用接缝，不新造第二套。
