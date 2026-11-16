## 用户问题

已有 EPSG 转换、导入重投影与导出变换，但用户**无法审阅和固定**一次矢量重投影所使用的坐标操作：`Transformer.from_crs` 每次静默选择操作、无法查看候选与缺格网原因、无法冻结操作定义与环境证据、执行中操作/精度条件可能被悄悄更换。对测绘/GIS 专业用户，坐标操作的选择（含精度、ballpark、格网依赖）是必须可审阅、可复现的决策。

## 行为增量（G45 坐标操作工作台）

- **候选审阅**：冻结源 CRS + AOI → 当前离线环境的坐标操作候选（可执行项含 accuracy/ballpark/适用范围；**不可执行最佳已知项保留并给出缺格网名与官方来源**，不静默消失制造「最佳可用」错觉；unknown accuracy 绝不显示为 0）
- **服务器签发锁定**：客户端只传 candidate_id（任意 pipeline/格网路径无入口）；锁定重算候选集匹配后冻结 PROJJSON 定义 + pyproj/PROJ/格网清单环境证据；环境漂移 → `OPERATION_ENV_CHANGED`，绝不静默 fallback
- **有界预览**：before/after 坐标对（≤8）、源/目标范围、非有限（输入/输出分相）与适用范围外发现；「预览成功 ≠ 全量成功」明示
- **durable 全量执行**：源冻结（ABA 隔离）→ 冻结操作逐顶点变换（all-or-nothing：任一非空几何失败即整任务失败不发布成果；空/null 几何独立计数保留）→ 行/顶点/字节预算分配前闸 → **严格发布门**（持久取消/claim/attempt 不可确认即阻断，不继承 runner fail-open）→ 原子发布 + DataObject CAS + 台账
- **成果与交付**：带 target CRS 的 GeoJSON 权威成果 + GeoParquet 投影；G18 无 target_crs 交付 GPKG **保持 CRS、不再次选操作**；GeoJSON 交付走独立验证的 WGS84 显示副本（4326 转换链证据入收据）；显示副本失败不伤主成果（状态分开）
- **轴/单位/维度合同**：官方轴序仅展示、载体恒 x/y（`always_xy`，EPSG:4326 官方 lat-first 不触发二次交换——非对称点裁决）；单位从 CRS 权威读取（ftUS 常数对账）；Z 逐值保留 + 回执明示 horizontal-only；compound/垂向/动态 datum（ITRF epoch）/M 维 typed 拒绝
- **恢复与幂等**：崩溃窗口按权威文件/manifest 收敛（lifespan `recover_all`）；同 plan/源/操作幂等复用已验证成果（前缀世代匹配）；内容被删 → typed 失效不凭历史收据

## 复用与范围

复用 G18 source resolver/交付格式语义、G23 durable runner、G38 outputs 发布范式、G24+ 既有选择/权限体系；不新增第二套 loop/registry/状态机。G40 资产按已合并接口无依赖；G47 #2125、G48 无交集。共享接线仅加性：main.py 路由行、tools/__init__ 行、JobKind/DataObject/capability/algorithm 登记、迁移 `g45_coordinate_operations`（down=g40 单头链，空库 upgrade/downgrade/re-upgrade 实测）。

## 实际验证（本 worktree 实测，证据见 planning/G45-COORDINATE-OPERATIONS/TEST_MATRIX.md）

- **域测试 60 passed**（独立解析 oracle：Redfearn UTM/WebMercator 教科书级数实现非 pyproj 搬运；含评审回归 8 项）
- **真实 Pi 旅程**：Pi 子进程 + node stub LLM → tool_search → webgis_coordop_* 全链（1201 要素含空/null 几何 → UTM 50N → completed 收据 → G18 GPKG 下载读回），21 gates passed
- **真实浏览器旅程 j25**（real 档）：登录 → chat 会话 → 产品链播种（autofix/apply 铸 1201 点 FC + 声明 CRS）→ 面板候选/锁定/预览 → 执行 completed 1201/1201 → 重开（服务器状态源）→ G18 GPKG 下载（SQLite magic + 字节回读）passed
- 前端 tsc 0 错 + 面板组件测试 13 passed（含 i18n 完整性）；OpenAPI snapshot/api-docs/staleness 三闸绿
- 迁移：空库 upgrade → downgrade → re-upgrade 真实执行通过

## Review / Fix / Rereview

Round 1（未参与实现的 reviewer，含 /tmp 探针实证）：**GO-WITH-FIXES**（无 P0；2×P1 + 3×P2 + 8×P3——幂等世代复用失效、线程内进度协程静默丢弃、政策承诺未实现、整文件解析、lifespan 恢复缺线等）。全部 P1/P2 与可快修 P3 已修复并补 8 项回归（commit `fix(g45): Round-1 独立评审修复`）。Round 2 复审结论见 planning/G45-COORDINATE-OPERATIONS/REVIEW.md。

## 兼容 / 迁移 / 回退

- base 655f769ca（PR 前 fetch 无漂移）；对既有 `reproject_coordinates` 工具与 `transform_geojson` 零改动（新作者流程独立）
- 迁移纯 additive 两表（coexistence guard 可重入）；回退 = `alembic downgrade g40_cartographic_symbol_assets`（实测）
- OpenAPI 为 additive 新路由

## 未运行项（如实）

- C35 高精度格网正向转换：本机 PROJ 数据目录 0 受信格网（离线基线），无法真实执行；缺格网阻断面（候选披露/锁定拒绝）已验证
- #2123 全仓 CI 基线债不在本任务范围（未触及文件的失败可在干净 base 复现；本任务新增/触及面全绿）
