# PROGRESS — 05｜工程包交换（滚动更新）

## 已交付（R0–R7）

### 后端（app/services/project_exchange/ 九模块）
- contracts.py：format 门/硬帽/分类词表/manifest 模型（strict extra=forbid）
- closure.py：项目依赖闭包读取（upload/session/artifact/external 分类；范围收敛）
- inspector.py：inspect/plan（零写入）+ 确定性逻辑 ID 分配
- payloads.py：三条权威载荷通路（upload 文件/会话 store/产物 BlobStore）
- writer.py：标准 GIS 物化（多图层 gpkg/栅格字节拷贝/CSV）+ 独立读回保真
- exporter.py：导出 durable runner（staging→ZIP→原子发布→owner 侧车→会话面板）
- untrusted_zip.py：不可信 ZIP 防线（slip/bomb/白名单/哈希对账/流式预算）
- importer.py：受控上传接收面 + 静态审读 + 拓扑序注册 + 图内 ID 重映射 + 补偿式回滚
- verify.py：导入后逐项对账 + 复现证据（exact/semantic/partial/not_evaluated）

### 入口
- REST：/projects/{pid}/exchange/{inspect,plan,export} + /project-exchange/*（上传/预检/导入/回执/by-job）
- Pi 工具六件（deferred 超集注册）：inspect/export_plan/export/import_inspect/import_plan/import
- 前端：artifact-center 工程包按钮 + PackageExchangeDialog（导出/导入双页签）+ i18n（zh/en）+ api client
- 文档：docs/adr/0306 + docs/api-docs.md 再生成

## 测试证据（全部实测，本 worktree）

| 面 | 结果 |
|---|---|
| tests/unit/project_exchange/ 全套（79 测试） | **79 passed, 1 skipped**（perf 测试默认跳过，显式 -m perf 跑通） |
| 契约层（adr numbering/alembic metadata/import boundaries/ci-local gate） | 44 passed |
| Pi surface 回归（pi_status_fail_closed/pi_bridge_lock/flow_product_surface） | 18 passed |
| api-docs drift（再生成后） | 22 passed（含 adr/import boundaries 复跑） |
| 上传删除顺序/错误净化等邻接面 | 抽查通过 |
| 前端 vitest（project-exchange client） | 5 passed |
| 前端 eslint（新文件 --max-warnings 0） | 0 问题 |
| ruff（全部新文件+改动文件） | 0 问题 |

### 性能基线（首轮；3 数据集 × 1000 要素，74 KiB 包，本地 SQLite + tmp DATA_DIR）
- 载荷解析 0.01s；物化+ZIP 0.35s；包审读 0.00s；解包 0.00s；导入注册 0.09s；全程 <0.5s

## 基线既有失败（stash 验证非本任务引入，不修不掩盖）
- tests/test_tool_meta_contract.py::test_extension_example_names_exist / test_extension_routes_distribution_before_status_pull（index.mjs 内容面，干净 master 复现）
- frontend tsc -p tsconfig.test.json：live-spec.test.ts(129,70) TS2353（干净 master 复现）
- scripts/debt_ratchet.py（registry.py/remote_sensing.py god_modules 棘轮，基线漂移）
- scripts/check_dependency_bounds.py（pandas/sqlalchemy 上界收紧要求，master 既有）

## 必过旅程状态
1. ✅ 原生包往返（T6 test_roundtrip.py：导出→受控上传→真实 runner→重映射→校验 semantic）
2. ✅ 标准交换独立读回（T7：geopandas + ogrinfo CLI + manifest 台账哈希对账）
3. ✅ 恶意/越权/取消矩阵（T8：篡改哈希/跨 owner/许可披露/取消无半成品/无覆盖入口）
4. ✅ 双入口（REST + Pi 工具同 services 层；T9/T10；UI 对话框 + 会话面板自动纳入）

## 剩余
- R1 独立审查（逐行 diff + 邻接权威）→ 修复
- R2 复审 + 关键 journey 复跑 + PR
