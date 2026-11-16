# G45 DEDUP_MATRIX — 逐项判 present/partial/missing/owned-by-other/blocked

判据：追到生产调用链（文件:行 为 worktree 内 master 655f769ca 状态）。

| # | 既有能力 | 判定 | 证据（生产调用链） | G45 处置 |
|---|---|---|---|---|
| 1 | 单点坐标/GeoJSON EPSG 重投影（EPSG↔EPSG legs） | present | app/utils/coord_transform.py:459-480 Transformer.from_crs always_xy；app/tools/coord_transform.py:123-181 reproject_coordinates 工具 | 复用（几何遍历 `_walk_coords`/`_collect_leaf_pairs` 同构逻辑保持兼容）；加回归测试；新作者流程不改它 |
| 2 | 中文 CRS（WGS84/GCJ02/BD09）转换 | present | app/utils/coord_transform.py:87-236 | 不动（任务书明确不做 GCJ/BD09 产品重写） |
| 3 | 导入期 CRS 读取/用户声明 + 4326 地图数据 | present | app/services/data_import/interpretation.py:533-549, 594-622 | 只消费其产物（session ref / fabric parquet），不重造导入 |
| 4 | 导出期重投影 + 非 4326 GeoJSON 拒绝 + 精度损失披露 | present | app/services/data_delivery/service.py:724-741；loss.py:165-206 | 复用格式语义：GPKG/geoparquet 保持 CRS；GeoJSON 走本域 4326 显示副本 |
| 5 | join 执行器内的点/面 CRS 转换 | present（G38 域内部） | app/services/data_join_authoring/executor.py:660-695 | 不接管（G38/G39 内部处理不是本域范围） |
| 6 | 授权源快照（session_ref/fabric_parquet/project_artifact/table_rows）+ 分批读 + 修订复核 | present | app/services/data_delivery/source_resolver.py:64-98, 265-287, 325-349 | 直接复用 resolve_source；表格源/无几何 → typed 不合格 |
| 7 | durable job（claim/watchdog/fencing/进度/取消复核） | present | app/services/data_operations/runner.py:97-122, 221-247, 285-372 | 复用 runner；发布前用**本域严格门**补 fail-open 弱点（runner.py:111-118 注释明示） |
| 8 | staging→回读 oracle→原子发布→DataObject CAS→artifact 台账→收据 | present | app/services/data_join_authoring/outputs.py:130-271 | 同构复用（目录 data/coord-ops）；kind=coordinate_operation 白名单登记 |
| 9 | 长表 GeoParquet 成果 + fabric-parquet ref | present | app/services/data_fabric/materialization_service.py:330+; vector_carrier.py:561 | 原生成果经 features_to_arrow(crs=target)+table_to_geoparquet；显示副本走 session FC ref |
| 10 | 操作候选（TransformerGroup）/操作锁定（from_pipeline 冻结重建）/环境证据/计划持久化 | **missing** | 全仓 grep TransformerGroup=0；无 coordinate_operations 域 | **本任务核心增量 WP03-05** |
| 11 | 预览（before/after 样本、AOI 适用性、非有限检查、独立 oracle） | **missing** | 无既有预览面 | WP06 |
| 12 | 全量 vertex 转换执行（all-or-nothing、Z 政策、预算闸、空几何政策） | **missing** | 无 | WP07 |
| 13 | 坐标操作作者 UI（候选对照/锁定/预览/执行/结果/下载） | **missing** | frontend 无 coordinate-operations 目录 | WP10 |
| 14 | Pi 工具（describe/list_operations/plan/preview/execute/status/result/cancel） | **missing** | app/tools/ 无 coordinate_operations_tools | WP11 |
| 15 | 独立数值 oracle（解析式 UTM/Mercator/官方控制点） | **missing** | 无（tests 无该 oracle） | WP12 |
| 16 | G40 资产域 | owned-by-other | #2124 已合并 | 按既有接口消费（本域不依赖） |
| 17 | G47 Dorling #2125 | owned-by-other | open PR | 不碰 |
| 18 | G48 像素 GCP 配准 | owned-by-other | 兄弟 worktree | 不等待、不占用（G45 不做像素配准） |
| 19 | G42/G43/G44 兄弟域 | owned-by-other | worktrees 无 PR | 共享接线处仅加性：main.py 路由行、tools/__init__ 行、迁移 down 链、DataObject 白名单行 |
| 20 | 修全仓 CI 基线（#2123） | blocked（非本任务目标） | issue 报告旧 master 48 后端/16 前端失败 | 本任务新增测试/门禁自身绿；触及文件的既有失败以干净 base 复现区分 |

结论：核心增量 = 10–15 项（候选/锁定/计划/预览/执行/成果证据/UI/Pi 工具/oracle）；
1–9 全部复用既有生产链，不造第二套。
