# G17：staged 多文件、多容器图层与表格导入工作台

## 问题与用户收益

旧上传只处理第一个文件（其余诚实披露为 `ignored_files`），GPKG/KML/ZIP 不指定图层（默认第一层），CSV 只能猜常见经纬度列名、未声明 CRS 按 EPSG:4326 假设披露，无 XLSX、无拒绝行报告、无批量提交收据。

本 PR 交付一个可用的数据入口：用户一次拖入一批本地资料（GeoJSON / GPKG / KML / Shapefile-zip / CSV / XLSX），**先看到文件、容器成员和工作表清单**，选择要导入的成员，**明确字段与坐标的解释方式**（X/Y、WKT、纯表、字段类型、显式编码/分隔符、源 CRS 声明），**检查有界样本与可定位的拒绝行**，再一次提交为真实的会话数据引用（空间走既有 IngestPipeline）或表格产物（`ref:table-`）。浏览器工作台与 Pi 标准工具使用同一服务、同一导入计划、同一提交收据。

## 实现边界

- 新域 `app/services/data_import/`（staging / 容器发现 / 解释引擎 / 计划 / 预览 / 提交 / 服务门面），一个计划事实源（`batch.json` 原子状态）。
- 不修改 Pi vendor / RPC / bridge / extension；不新增第二套 agent loop、工具注册表、MapSpec store、图层 ID 或并行撤销栈。
- 不改旧 `/upload` 契约（多文件 ignored_files、单结果接口、缺 CRS 拒绝规则均保持）；既有 G03 摄入、data_preparation、table artifact、项目 attach 全部复用。
- 不做 G14 要素编辑、G15/G16 画幅/标注/出版、G18 导出交付、G19 计算列、G09 批量分析、#2022 刷新。

## 关键合同

1. **原件、解释和结果分开**：源字节不可变；`plan_hash = sha256(source_sha, member_key, canonical 解释, INTERPRETER_VERSION)` —— 同源不同解释得到不同产物，不被 bytes 去重误合并。
2. **检查不等于提交**：inspect/preview 不产生正式 ref。
3. **样本是样本**：预览默认 200 行（硬帽 2000），总数未知如实标 unknown，候选编码不升级为用户确认。
4. **坐标声明真实**：CRS 证据四态（source_metadata / declared / rfc7946_default / unknown）；unknown 预览为表、提交 `CRS_UNCONFIRMED`，绝不按经纬度猜；重投影后摄入收到 OUTPUT CRS，源 CRS 在收据证据中。
5. **类型和几何不静默丢失**：默认全列字符串透传（前导零保真）；显式 number 的 >2^53 整数保真为字符串（不经 JS Number 截断）；空几何/非有限/非法 WKT 逐行拒绝并计数。
6. **提交有明确边界**：按成员原子发布，批次收据诚实 partial；成功成员重试复用（`duplicate`），失败成员可独立修正重试；不把跨 文件系统/SessionStore/registry 假称 ACID。
7. **权限来自服务器**：每次操作 `verify_session_owner` + staging 归属比对，统一 404 不泄露存在性；工具侧 runtime context 已验证会话优先于 LLM 传参（提示注入防御）。
8. **最终成功可验证**：成功子项 ref 真实可读（幂等复用前也重新校验可读性）；伪造 ref 不得显示就绪。
9. **恢复不等于重新读取用户电脑**：staging 48h TTL；崩溃后残留 committing 归一化（无收据即未完成，晚到结果不显示成功）。
10. **导入不暗中修复数据**：拒绝行保留源行号/原因/有界原文；超出行/字节预算诚实失败，不静默截断。

## 已执行验证（本地真实运行）

| 套件 | 结果 |
|---|---|
| `tests/data_import/`（垂直切片/容器矩阵/表格矩阵/恢复取消预算/工具 dispatch/API 合同/故障注入/评审修复） | **40/40** |
| 旧契约回归（`test_upload_identity_v4` 含 ignored_files 钉扎、`test_upload_api`、`test_shapefile_upload`、`test_data_parser_crs`、`test_data_parser_missing_crs`、`test_upload_tools`） | **40/40** |
| 前端 vitest（client + workbench 组件 + 既有 upload/dataset-manager 契约） | **12/12** |
| ruff（全部新文件） | 通过 |
| tsc（新文件） | 0 错（scene3d/three 的 8 个错误在 master 基线同样存在） |

- 22 个验收场景全部有测试落位（见 `planning/goal-g17-import-workbench/TEST_MATRIX.md`）。
- 故障注入反验：无 layer 参数失败 / preview-commit 间篡改（PLAN_HASH_MISMATCH）/ 伪造未登记 ref —— 均真实触发防线。
- 跨入口一致性：同一批次的 plan_hash 与收据在 API 与工具两视角一致（同一 `batch.json` 事实源）。
- 性能实测（`planning/goal-g17-import-workbench/PERFORMANCE.md`）：45.4MB 近限额单文件 —— 成员枚举 3.2s / 峰值 ~1MB（metadata 优先，不读完整容器）；预览 200 行 1.4s；提交 O(N)（217s / 553MB Python 分配，与既有上传摄入同类，如实披露）。120k 行 CSV 取消往返 6.2s；staging 清理无残留。

## 独立评审

已按 §F 完成一轮未参与实现的独立评审 → 修复 →（复审中）。首轮发现 P0×1（前端无勾选控件）/ P1×2（重投影后 CRS 标签、表格超行数静默截断）/ P2×12，全部修复并有红→绿回归测试（`tests/data_import/test_review_fixes.py` + 前端勾选用例）。修复清单见 `docs/data-import-workbench.md` 评审修复记录。

## 兼容与迁移

- `/upload` 行为零变化；`data_parser.py` 仅尾部追加 5 个公共守卫别名（additive，其中 4 个被复用）。
- `requirements.txt/.lock` 新增 `openpyxl>=3.1.5`（XLSX 生产读取；read_only 模式不执行公式；运行时缺席诚实披露 `xlsx_engine_unavailable`）。lock 为手工 +5 行（`uv pip compile` 会重排平台 pin，故最小插入）。
- 无 schema 迁移、无 ADR 编号占用（决策记录在 planning 账本）。

## 已知限制（如实）

- 提交为 O(N) 内存路径（与既有上传摄入同类）；发现/预览近常数内存。
- CSV 分块读取的取消粒度 5000 行/块；XLSX 公式仅取缓存值（无缓存按空并披露）。
- 进程内取消/提交登记表假设单进程部署（与仓库现状一致）；多 worker 需后续改造。
- 表格字段映射只负责读入；清洗/联表/地理编码不在本域（接既有 data_preparation）。
