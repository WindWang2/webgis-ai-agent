# LOOP_LEDGER.md — 迭代账本

## 轮次记录

### R0（2026-11-10）侦察 + 建树
- 基线 SHA 816705218；worktree/branch 建立；3 份侦察报告（后端链/工具面/前端面）。
- DEDUP_MATRIX：G17 核心 missing；开放 PR 无冲突；#2032 仅薄挂载点。
- 决策 D1-D8 落 PLAN.md。
- 下一步：WP02 schemas/models。

## 环境事实
- 测试解释器：`python`（conda 3.13.9，依赖齐全）。pytest 需在 worktree 根执行。
- 重型阶段资源纪律：默认串行（本任务无编译需求；pytest -x -q 定向子集）。

### R1（2026-11-10）后端纵向切片打通
- 新增域：app/services/data_import/{models,budgets,staging,discovery,interpretation,preview,commit,service}.py
- 接线：main.py 路由 + tools/_TOOL_MODULES + data_parser 公共守卫别名 + requirements(.lock) openpyxl
- API：app/api/routes/data_import.py（9 端点）；工具：app/tools/data_import_tools.py（5 工具，未测）
- 测试：tests/data_import/test_vertical_slice.py 3 passed（124s）
  - 覆盖场景 2（双 GeoJSON）、幂等（场景 14 机制）、不同解释→不同产物（场景 13 机制）、归属 NOT_FOUND、unsupported 诚实披露
- 修复记录：_load_owned async 化、shapely 2.x __geo_interface__、member container_ref 显式定位
- 下一步：容器 fixture 矩阵（GPKG 三层/ZIP 双套/CSV 变体/XLSX）+ 取消/恢复 + 工具经 dispatch 测试 + UI

### R2+R3（2026-11-10）容器矩阵 + 表格矩阵全绿（15 passed）
- 容器（5）：GPKG 三层选二（未选层无产物、线层真实 32650→4326 转换）、ZIP 双套 basename 归组
  （字段不串用、孤儿 .shp 披露）、KML、缺 .prj 声明 CRS（不声明→CRS_UNCONFIRMED；声明→转换成功）、
  partial 失败→修正重试（成功成员 ref 复用 duplicate=True）
- 表格（7）：Easting/Northing+投影 CRS（场景 5）、旧计划版本拒绝（场景 12）、分号+gb18030+引号（场景 6）、
  前导零+2^53+1 长整型保真（场景 7，>2^53 显式 number 也保真为字符串）、WKT 混族+非法行定位（场景 8）、
  纯表 ref:table-（场景 9）、XLSX 三 sheet/空表/公式无缓存（场景 10）
- 产品决策：勾选未配置成员 → save_plan 自动落默认解释（save_plan 为显式确认点）
- 修复：discovery `_MEMBER_SAFE_RE` 定义、read_only Workbook 无 context manager（discovery+interpretation 两处）
- 既有噪声（非本任务）：dataset_semantics store 在 Windows 因文件名含 ':' 写失败（warning，既有行为）

### R5+R6（2026-11-10）恢复/取消/预算 + 工具/API 全绿
- resilience 6 例：预览恢复（场景 16）、提交中取消无迟到成功（17）、跨 owner 同 bytes（18）、
  重启恢复+COMMITTING 归一化（19）、预算有界拒绝+清理（20，预算类 DiscoveryError 升级为整批拒绝）、
  过期惰性清理
- 工具 3 例（场景 21）：检查→计划→预览→提交→收据全链经真实 registry.dispatch；归属 NOT_FOUND；
  runtime context 已验证会话胜过 LLM 传参（dispatcher 剥离 + 工具侧 _resolve 双层）
- API 4 例：multipart 创建→详情/列表→计划→预览→commit 202+轮询→清理；404 归属；unsupported 披露；409 版本冲突
- 修复：budget 类发现失败升级 DataImportError(413)+staging 清理；memory_class 词表 medium；
  import_plan_configure 显式 args_model（registry 签名推导不支持 Dict 注解）

### R7+R8（2026-11-10）前端工作台 + 旧契约回归
- 前端：lib/api/data-import.ts（XHR 创建 + transport 其余）、components/data-import/import-workbench.tsx
  （恢复/迟到守卫/有界渲染/配置面板/预览/收据/取消/清理）、dataset-manager 薄挂载（按钮+条件渲染）、
  i18n dataImport 双语 + messages.ts 登记
- vitest：client 8 例 + 组件 4 例 + 既有 dataset-manager/upload 测试 17/17 通过；tsc 我方文件 0 错
  （scene3d/three 8 个错误在 master 基线同样存在，非本任务引入）
- 旧契约回归（R8）：upload_identity_v4 + upload_api + shapefile + parser_crs×2 + upload_tools
  40/40 全绿（场景 22：ignored_files/单文件契约/register_ref 不变）
- 跨入口一致性：同一批次 plan_hash 与收据在 service(API)/工具(dispatcher) 两视角一致
- 环境注：worktree frontend/node_modules 为主工作区 junction（基线相同，未改 package.json）

### R9+反验（2026-11-11）故障注入 + 性能实测 + 文档
- 故障注入 3/3（暴露并修复两处真实防线缺口：commit 侧 container_ref 守卫、计划 hash 重推导权威比对；
  keep_fields 保存时 fail-fast）。附带修复：_run_commit 局部 import 遮蔽导致的 UnboundLocalError
- 性能实测（planning/PERFORMANCE.md）：45.4MB 近限额单文件（发现 3.2s/峰值1MB——metadata 优先成立；
  提交 217s/553MB——O(N) 如实披露）、20×4500 小文件批次、取消往返 6.2s、清理无残留
- 文档：docs/data-import-workbench.md（格式矩阵/资源限制/真实降级）、TEST_MATRIX.md（22 场景全落位）
- 全量 data_import 套件 32/32

### R10（2026-11-11）独立评审 → 修复
- 独立 reviewer（未参与实现）结论：P0×1（前端无勾选控件）+ P1×2（输出 CRS 标签/
  表格静默截断）+ P2×12
- 修复全部落位：P0-1 受控勾选（乐观+落服务端+回滚）；P1-1 摄入收 OUTPUT CRS；
  P1-2 表格 ROW_BUDGET_EXCEEDED；P2 族（列披露/XLSX header_row/zipset 冒号转义+
  安全组件名/diag 端点+取消意图+删除锁/批次展开预算/notes 截断/diag 上限/
  分块读取/前端守卫+轮询/死代码清理）
- 修复回归：tests/data_import/test_review_fixes.py 8/8（含 spy 断言 ingest crs 参数）
- 全量 data_import 40/40；前端 12/12；旧契约回归进行中
- 待复审：SendMessage 同一 reviewer

### R11（2026-11-11）复审收尾（第二轮）
- 复审判定：P0/P1 无残留；P2 中 10 项成立、2 项半成立；新发现 4 项
- 本轮修复：
  - 提交 zipset 目录哈希修复（ab848bbbd 已在 push 前提交）+ 测试补真实碰撞对（a:b/a_b 同目录），
    红检通过（移除哈希 → 测试失败）
  - DIAG_MAX_ROWS 真正接线（_write_diag 改用它 + 截断标记行）
  - diagnostics 端点 member_key 改 query 参数（含 "/" 的 zipset 子目录键可达；测试钉死业务 404 vs 路由 404）
  - P2-8 测试改双 zip（单套 471B < 阈值 700 < 双套 942B，per-zip 检查不触发 → 真红→绿）
  - 取消意图跨重启保留（归一化时 cancel_requested → cancelled）
  - DIAG_INLINE_MAX_ROWS 入 budgets（5000 魔法数收敛）
  - 删除误跟踪的空文件 x；修 F401；PR_BODY 别名计数 5→4
- 回归：data_import 41/41；前端 12/12；ruff 全绿
