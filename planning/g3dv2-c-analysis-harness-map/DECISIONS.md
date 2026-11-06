# DECISIONS — g3dv2-c-analysis-harness-map

| # | 决策 | 理由 | 替代方案（未采纳原因） |
|---|---|---|---|
| D1 | viewshed 几何独立模块（viewshed_geometry.py），内核零改动 | 算法计算与几何可视化产物分离；stats 零漂移可钉 | 改内核返回几何（污染数值契约，#1829 口径回归风险） |
| D2 | 像元原子记账（CRS 单位 shoelace = n×\|det\| 整数关系） | 地理 CRS 度² 冒充 m² 是 1e10 倍错误；整数记账解析可证 | 米制 shoelace（只对投影 CRS 成立） |
| D3 | cut/fill 用像元中心中点求积 | ≤1 次表面精确（解析 oracle 可证）；与 surface_area 的"像元中心是唯一观测"纪律一致 | 角点双线性插值（发明数据，Jenness 口径相悖） |
| D4 | 基准面只做 constant/mean | 任务书"指定基准面"；tilted plane 拟合增加假设面无独立 oracle 需求 | 最小二乘斜面拟合（无明确用户语义前不开） |
| D5 | profile 走 dispatch 认可键（profile_chart）+ prefix=profile | 沿 #517/uncertainty 先例；工具无 session 访问，缝负责铸造 | 工具内铸造（需要 session 上下文注入，破坏薄包装） |
| D6 | 组件挂接在 _author_display_result 层挂载后 upsert | 层与面板同一事务窗口；失败 additive 不推翻层 | 独立工具（多一次 agent 调用，非闭环） |
| D7 | run_3d_analysis 产物提升（geojson/profile_chart hoist） | 编排=薄包装；产物走与直调完全相同的外层缝通道 | 编排层自铸 ref（第二执行真相，违 D3 原则） |
| D8 | staleness 选择检查收窄到观察者语义目标（viewshed/LOS 或显式 feature id） | 线路/区域分析不是选择点的函数（F27-14 旅程显形）；显式声明优先 | 全目标检查（把非观察者分析误判旧） |
| D9 | 绑定登记时戳当前选择上下文 | 分析响应当前选择做出——同选择不判旧、换选才失效 | 不戳记（响应式分析立即自判旧，语义错误） |
| D10 | route_profile_joint 主语义模型收窄 graduated_line | terrain×pdf 键选位属既有报告模板族（golden corpus stress 两案例改写证明抢占）；矢量导出仍合法 | 保留 terrain 模型（选择回归） |
| D11 | MAPSPEC_SOURCE 验收步（真实 UpsertSourceIntent） | DEM 供给注册面归 A 方向（#1835 未合并）；scripted 走同一 mapspec 引擎事务，不假装生产工具 | 造 webgis_source_upsert 工具（侵入 A 方向边界） |
| D12 | 建筑净空 fail-closed 不开放 | 无可靠 3D building geometry 源；能力探测 typed unavailable | 楼层推断/白模假设高度（编造数据） |
| D13 | skyline 保持 honest pending decline | 算法未实现 | 占位实现（伪装可做） |
| D14 | 吸收 #1830 快照/水位欠账（openapi KeyframeScene、migration 98→99） | 本分支再生显形的一行 additive 记账；preflight full pass 门需要 | 留红（本 PR 质量门被无关债卡死） |

ADR-0276 已固化（docs/adr/0276-3d-analysis-product-closure.md）。
