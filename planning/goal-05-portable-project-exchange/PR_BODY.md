# PR 正文（R2 后定稿）——05｜GIS 工程包、跨系统数据交换与可移植复现 Harness

## 基线与 pin
- 基于 origin/master `f34cb69dda6150ee4135e6d2a476708149d9eef7`（2026-11-28 fetch 实测，与任务书快照一致）
- vendor/pi：**未改动**（v1.6.1 = `ef4a1cfc`；上游 v1.7.0 存在，按线间裁决归 01 线）
- 分支 `zcode/goal-portable-project-exchange-20261127`；独立 worktree 开发；未触碰 master 与其他任务 worktree

## 实际新能力
**层级 A（核心验收）· 原生可重开工程包**：项目级依赖闭包（Project/Dataset/Workflow/Artifact/MapProduct 权威读取）→ 五态分类（embedded/external_reference/redacted/missing/unsupported）与三级复现分级 → 标准 GIS 物化（多图层 GeoPackage + COG/GeoTIFF 字节保真 + CSV，全部复用 G18 writers，不写第二套读写器）→ ZIP 容器（manifest schema v1 + 文件台账 + fidelity receipt + DISCLOSURE.md）→ 原子发布进既有 exports 下载链（owner 侧车 + 会话文件面板自动纳入）。

**层级 B（核心验收=标准数据可读）**：包内 `standard/layers.gpkg`（多图层）、`standard/rasters/*.tif|png`、`standard/tables/*.csv` 经 **独立库读回验证**（geopandas/pyogrio/rasterio + ogrinfo CLI + manifest 逐文件 sha256 对账）；样式保留原生 MapSpec + 披露（不声明 SLD/.qgz，不伪造桌面 GIS 全保真）。

**WP3 安全导入**：不可信 ZIP 五道防线（中央目录审读/路径规范化/扩展名白名单/逐条目流式预算+压缩比/sha256 对账，绝不 extractall）→ import inspect/plan → **全新隔离项目**拓扑序注册（数据集→工作流→产物→地图）+ 图内 origin_id/source_ref 精确重映射 + 会话 ref 原位恢复（尽力而为，恢复报告可见）+ **补偿式回滚**（失败=零残留，原项目零接触）→ 导入后逐项校验（exact/semantic_equivalent/partial/not_evaluated 诚实分级）。

**WP5 双入口**：REST（/projects/{pid}/exchange/{inspect,plan,export} + /project-exchange/{packages/upload,imports/*,exports/*,jobs/*/receipt}）与 Pi typed 工具六件（project_exchange_{inspect,export_plan,export,import_inspect,import_plan,import}，deferred 超集注册，输入仅受控 ID）共用同一 services 层与权限；UI 入口挂既有产物中心（导出/导入双页签对话框，i18n zh/en）。

## 去重矩阵摘要
`project_bundle/project_package/project_import/workspace_bundle` 于 master 零命中；最近邻（WorkspaceSnapshotService/workflow_v4 Package/model_authoring 对偶/G18 单数据集交付）均为局部子集——详见 planning/goal-05-portable-project-exchange/DEDUP_MATRIX.md（每项含 path:line 证据与剩余增量归属）。开放 PR #2290/#2291 属 01/03 线，与本线无路径交集。

## 测试与性能数据（全部本地实测）
- tests/unit/project_exchange/ **79 passed, 1 skipped**（perf 显式跑通）：合同门/闭包分类/ZIP 防护矩阵（slip、绝对路径、穿越、符号链接、重复条目、保留名、坏扩展、嵌套 zip、真实 1000x 压缩 bomb、条目帽、哈希篡改）/多图层 gpkg 保真/栅格字节拷贝 nodata/CSV 往返/**导出→上传→真实导入 runner→重映射→校验** 全旅程/geopandas+ogrinfo 独立读回/跨 owner/许可披露/取消无半成品/无覆盖入口（签名层断言）/Pi 工具注册与 typed 错误/REST 鉴权负例/需求契约映射
- 契约层：adr numbering、alembic 单 head、import boundaries、ci-local gate **44 passed**；Pi surface 回归 18 passed；api-docs 再生成后 drift 22 passed
- 前端：vitest 5 passed；eslint --max-warnings 0 通过；tsc 无新增错误（基线既有 live-spec TS2353 见下）
- ruff 全部新/改文件 0 问题；mypy 白名单未涉新增文件（棘轮规则）
- 性能（3 数据集×1000 要素，74 KiB 包，首轮基线）：载荷解析 0.01s / 物化+ZIP 0.35s / 包审读+解包 ~0.00s / 导入注册 0.09s，全程 <0.5s

## 未测项与基线遗留（诚实披露）
- 基线既有失败（stash 验证与本改动无关，不掩盖）：tool_meta_contract 2 项（index.mjs 内容面）、frontend tsconfig.test live-spec TS2353、debt_ratchet god_modules 棘轮、check_dependency_bounds（pandas/sqlalchemy 上界）
- 未本地复现的 CI lane：db-migrations（无新迁移）、real-services-smoke、runtime-validator；QGIS 桌面未安装——标准互操作以独立库读回为证据，不声称 QGIS 实测
- 浏览器 E2E：UI 走 vitest + 类型/lint 门禁；journey 级 Playwright 未新增（工作台挂载为既有页面内薄入口，mock 模式旅程面见 REST/Pi 集成测试）
- 限制（已在包内 DISCLOSURE.md 与 manifest 披露）：layer 类型数据集/云模型任务不物化；PNG 数据集载荷不进 UploadRecord（词表约束，会话栅格恢复双轨承担）；导入 verdict 上限 semantic_equivalent（转换后字节形态变化）

## 迁移/兼容/回退
- **无新 DB 表、无迁移**：包/导入台账 = 磁盘 manifest/receipt（与 data_delivery 同构）；导入产物全部落既有表
- 既有行为零改动（除 main.py 两行挂载、tools/__init__.py 一条注册、api-docs 再生成、artifact-center 一枚按钮）；新 REST 前缀独立、Pi 工具 deferred 不进前门
- 回退：revert 单提交即可；产物目录（DATA_DIR/project-packages、exports 内 wgproject-*.zip）可安全删除

## 冲突边界
变更文件与 01/02/03/04 线无写面交集（Pi host/provider、移动对象、抽样、图符/出版导出均未触碰）；共享接线仅 main.py/include_router、tools/__init__.py/_TOOL_MODULES 各一条 additive 行。PR 前重新 fetch 复核（R2）。

## R1/R2
**R1**（独立审阅代理逐行 diff + 运行时实验）：1×P0（manifest 引用路径任意文件读取）+ 4×P1（回滚死代码/路由 user 键错/回执 schema 500/gpkg 中间态哈希）+ 8×P2 + 8×P3 —— **全部 P0/P1/P2 修复**并锁定回归（tests/unit/project_exchange/test_r1_fixes.py 10 例 + 全量 90 例绿）；P3 除幂等竞态一项记录为与 data_delivery 同款已知取舍外全部修复。明细与逐项关闭证据见 planning/goal-05-portable-project-exchange/REVIEW_R1.md / REVIEW_R2.md。
**R2**：远端复核无漂移；冲突矩阵（#2290 零交集；#2291/#2292 仅共享 3-4 个一行式 additive 注册文件）；关键 journey 复跑绿；`git diff --check` 干净。
