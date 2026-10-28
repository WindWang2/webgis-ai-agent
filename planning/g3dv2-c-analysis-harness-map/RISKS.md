# RISKS — g3dv2-c-analysis-harness-map

| # | 风险 | 等级 | 缓解 | 残余 |
|---|---|---|---|---|
| R1 | viewshed 几何化大栅格内存/耗时 | 中 | 内核已有 _guard_cells 像元包络；多边形化 checkpoint 协作取消；顶点预算阶梯淘汰 | 噪声掩膜最坏情形仍可能秒级——meta 披露 threshold |
| R2 | 地理 CRS 米制换算漂移 | 中 | 像元原子记账（CRS 无关）+ 米制面积=像元数×米制像元；测试钉 cos-lat 政策 | 与 #1805 classify_crs 既有误判正交（不抢修，见 PR_OVERLAP） |
| R3 | profile_chart 大载荷进 ref store | 低 | ≤512 点抽稀 + quality_flags 披露；100KB 端点口径内 | 超长线路面板保真度下降（统计仍全采样） |
| R4 | staleness 选择语义收窄回归 | 低 | 既有 scene3d 125 测试全绿 + 收窄仅放行非观察者目标；显式 feature id 仍检查 | 依赖 goal 词表——新增观察者型分析须登记 _OBSERVER_GOALS |
| R5 | 模板选择抢占既有产品 | 低→已消除 | route_profile 模型收窄后 golden corpus stress 零改写（2060 绿） | 无 |
| R6 | 与 A 方向 #1835 合并冲突 | 低 | 本分支不触碰其文件面；openapi 快照 additive 合并可行 | openapi.json 双方再生（PR 说明） |
| R7 | 与 B 方向 scene-runtime 合并冲突 | 低 | 不改 scene3d 前端/observation DTO；i18n 零改动 | render_work_corpus 不触碰 |
| R8 | _parsed 修复改变登记行为 | 低 | 仅恢复被掩盖的登记路径（best_effort 内 UnboundLocalError→正常跳过逻辑）；协调测试 32 绿 | 无 |
| R9 | F27 依赖合成 DEM（非真实数据） | 中（诚实边界） | simulated 披露 + A 方向供给合并后可无缝换真实源（raster_path 参数面不变） | 真源验证留待数据面可用后 |
