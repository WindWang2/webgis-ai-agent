# G17 staged 导入工作台 —— 格式支持、资源限制与真实降级

> 代码基线：本分支（G17-IMPORT-WORKBENCH）。复现入口：`/api/v1/data-import/batches` 端点族
> 或前端「项目数据集 → 批量导入工作台」；标准工具 `import_batch_*` 族。

## 格式支持矩阵（发现 → 解释 → 提交）

| 格式 | 成员粒度 | 发现 | 表格解释 | 空间提交 | 边界与降级（真实） |
|---|---|---|---|---|---|
| .geojson/.json | 整文件 | read_info（零行读）；RFC7946 无 crs 成员 → `rfc7946_default` | — | FC → IngestPipeline | 内容魔数+驱动白名单（复用上传守卫）；>50MB 拒绝 |
| .gpkg | 每图层 | list_layers + read_info(layer=)；≤64 层 | — | layer 参数显式读取（绝不默认第一层；缺失即 `CONTAINER_REF_MISSING` 失败） | 缺 CRS 图层：不声明 → `CRS_UNCONFIRMED` 拒绝；声明 → set_crs+转换并记录证据 |
| .kml | 每图层 | 同上；DOCTYPE/实体/vsi 禁 | — | 同上 | KML 图层名做成员定位（container_ref），不依赖显示名 |
| .zip（shapefile） | 每套（容器内目录+basename 归组） | 组件齐全（.shp+.dbf 必需）成套；孤儿组件披露 | — | 提取到 staging 后读 .shp | 嵌套 zip 不支持（披露）；展开字节/条目数上限；缺 .prj 走声明通道 |
| .csv | 整文件 | 嗅探编码（utf-8-sig→utf-8→gb18030）/分隔符/表头；坐标列候选 | X/Y、WKT、纯表三模式；显式编码/分隔符/表头行；字段类型 text/number/boolean/date（默认原样字符串） | points/wkt + 已确认 CRS → FC；纯表 → `ref:table-*` | 未声明 CRS 的坐标列：预览为表+要求声明，提交拒绝（不按经纬度猜）；>2^53 整数保真为字符串 |
| .xlsx | 每 sheet | openpyxl read_only+data_only；空 sheet/公式无缓存披露 | 同 CSV（sheet 选择） | 同 CSV | openpyxl 为生产依赖；缺席时成员 `xlsx_engine_unavailable` 诚实披露；公式取缓存值，无缓存按空并披露 |

不支持格式的文件产生 `unsupported_format` 占位成员 + 注记（不静默丢弃，也不假装可导入）。

## 资源限制（`app/services/data_import/budgets.py`，全部可配置）

| 预算 | 默认值 | 超限行为 |
|---|---|---|
| 单批文件数 | 20 | 创建即拒绝（400），无部分落盘 |
| 单文件大小 | 50MB（沿用上传上限） | 拒绝 |
| 批次原始/展开字节 | 200MB / 200MB（展开按本源累计） | 拒绝 + staging 清理 |
| 容器成员/图层/sheet | 64 | 有界拒绝（413）+ 清理 |
| zip 条目 / 嵌套 | 4096 / 0 层（嵌套披露） | 拒绝 |
| 预览行数 | 默认 200，硬帽 2000 | 截断 + 采样声明 |
| 拒绝行内联 | 100（超出落 diag/ 产物，`GET /batches/{id}/diagnostics/{member_key}` 可取） | — |
| 提交行预算 | 500k 行/成员；输出 150MB | 诚实失败 `ROW_BUDGET_EXCEEDED`/`OUTPUT_BUDGET_EXCEEDED` |
| 并发成员解析 | 1（串行） | — |
| staging TTL / 会话批次上限 | 48h / 20 | 惰性过期清理（只删自己目录） |

## 权限与生命周期

- 归属 = 已验证会话（`verify_session_owner` + `X-Session-Token`），批次跨会话不可见
  （统一 404）；工具侧 runtime context 已验证会话优先于 LLM 传参。
- staging 删除 ≠ 移除数据集：`DELETE /batches/{id}` 只清未提交文件与配置，
  已提交 ref/数据集不受影响（UI 明确区分两个动作）。
- 崩溃/重启：残留 `committing` 状态在下次读取时归一化——无收据成员复位
  `planned`（无收据即未完成，晚到结果不得显示成功）。

## 已知边界（如实）

- 提交为 O(N) 内存路径（与既有上传摄入同类）；发现/预览近常数。
- 多几何列 shapefile/嵌套字段在清单/预览阶段披露，不支持静默丢弃式导入。
- 表格字段映射只负责读入；清洗/联表/地理编码不在本域（接既有 data_preparation）。

## 评审修复记录（独立评审 R1）

| 级别 | 问题 | 修复 |
|---|---|---|
| P0 | 前端无成员勾选控件（主流程不可达） | 受控 checkbox：乐观态 + 即时 `saveImportPlan({selected})`，失败回滚 |
| P1 | 重投影后摄入 CRS 标签仍为源 CRS | 摄入收到 OUTPUT CRS（EPSG:4326）；源 CRS 在收据 crs_evidence |
| P1 | 表格超 COMMIT_ROW_LIMIT 静默截断 | 与矢量一致：`ROW_BUDGET_EXCEEDED` 诚实失败 |
| P2 | >64 列静默丢弃 | `fields_total` 披露 + keep_fields 误判提示 |
| P2 | XLSX header_row 被忽略 | 读入时跳过前 N 行（与 CSV 一致） |
| P2 | zipset member_key 冒号碰撞 / Windows 非法文件名 | key 片段 `%3A` 转义 + 组件安全 stem |
| P2 | diagnostics_ref 悬空 | 新增诊断端点（同 owner，有界） |
| P2 | 取消竞态 / 删除竞态 | `cancel_requested` 持久意图 + 终态服从；删除持锁 |
| P2 | 批次级展开预算未生效 | 按本源累计，超限整批拒绝 + 清理 |
| P2 | notes 提交期无界 | save_state 唯一截断点（64） |
| P2 | diag 明细无上限 | 10k 行封顶 + 截断标记 |
| P2 | CSV 解析期间取消不生效 | 分块读取（5000 行/块）+ 块边界检查点 |
| P2 | 前端预览无代际守卫 / 轮询下限 | generation 守卫；pollIntervalMs=3000 |
| P2 | 死代码/漂移 | 移除未用别名、declared_crs_fallback 分支、死条件 |
