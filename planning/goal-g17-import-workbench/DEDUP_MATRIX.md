# DEDUP_MATRIX — G17-IMPORT-WORKBENCH

对照基线 `816705218`（每项附源码证据）。状态：`present`（已实现，转兼容回归）/ `partial`（部分实现，补齐剩余）/ `missing`（本任务新增）/ `owned-by-other`（他人 PR 所有权）/ `blocked`。

| 能力 | 状态 | 证据 | 本任务动作 |
|---|---|---|---|
| 单文件 GeoJSON/SHP-zip/KML/GPKG(第一层)/CSV 上传→解析→ref | present | upload.py:106-121、data_parser.py:299-404 | 兼容回归（不破坏） |
| 多文件上传（其余文件 ignored_files 披露） | present | upload.py:157-170；test_upload_identity_v4.py:384-407 | **不修改旧端点**；新 staged 批次端点承担真多文件 |
| GPKG 多层枚举/逐层有界预览 | partial | geopackage_adapter.py:99-234（data_fabric 本地源，未接入用户上传） | 复用读取原语，接入上传 staging 工作流 |
| CSV 列名猜测（常见经纬度名）+ CRS assumed 披露 | present | data_parser.py:211-220、441-560 | 保留；新增显式 X/Y、WKT、纯表、分隔符、编码、字段类型 |
| 内容安全（魔数/驱动白名单/zip 炸弹/穿越） | present | data_parser.py:77-208 | staging 沿用同一守卫，不开放任意驱动 |
| 摄入管线（profile/quality/descriptor/dedup/取消/许可） | present | data_ingest/pipeline.py:150-415 | 每成员提交接入，继承语义 |
| 准备计划（角色映射/axis swap/重投影） | present | data_preparation/plan.py:257-336 | 导入字节解释/表→几何映射与之分工，不重复 |
| 表格产物端点（ref:table- 前缀取回） | present | chat_session_views.py:141-173 | 纯表用 prefix="table" 对齐 |
| artifact 登记（幂等 upsert） | present | artifact_registry.py:535-550（artifact_type 自由串） | additive 新 producer_tool="import_workbench"、table 类型登记 |
| 项目 attach（POST /{project_id}/datasets） | present | project.py:264-275 | 只读消费，不改归属校验（#2044 属既有问题） |
| staged 批次/成员清单/解释计划/预览/逐成员提交 | **missing** | 全仓无 data_import 域 | 本任务核心 |
| XLSX sheet 选择/公式缓存披露 | **missing**（生产依赖缺失） | requirements-dev.txt:29-30 | openpyxl 入生产 requirements + 能力探测 |
| 导入工具（Pi 标准 @tool） | **missing** | app/tools/ 无 import 工具 | 新增，经真实 registry.dispatch 验证 |
| 导入工作台 UI | **missing** | dataset-manager.tsx 单文件 input | 新增独立组件 + 薄挂载 |
| Pi pin / RPC / bridge / extension 生命周期 | owned-by-other | G12（任务书 §C） | 不触碰 |
| 数据资格/availability 生产接线 | owned-by-other | G13 | 只读消费 |
| 要素编辑/lineage | owned-by-other | G14（#1873 已合并基座） | 不做编辑；保留源 ID 披露 |
| 多画幅/标注/出版 renderer | owned-by-other | G15/G16（#1975/#1875 基座） | 不做 |
| 成果导出交付 | owned-by-other | G18 | 只用已有 API |
| 计算列派生 | owned-by-other | G19 | 不做字段公式 |
| 项目数据刷新 | owned-by-other | #2022 (G10) | 不做 |
| 参数化批量分析 | owned-by-other | G09（历史安排） | 不做 |
| UI 全局重设计 | owned-by-other | #2032 | 仅薄挂载，不动主题/布局 |
