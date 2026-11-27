# TEST_MATRIX — 05｜工程包交换与可移植复现

| # | 测试面 | 文件 | 判据 | 状态 |
|---|---|---|---|---|
| T1 | manifest 合同（format 门/有界/逻辑 ID 白名单/slug 确定性） | tests/unit/project_exchange/test_contracts.py | 全绿 | 计划 |
| T2 | 闭包读取（upload/session/external/layer/detached 分类；范围收敛） | tests/unit/project_exchange/test_closure.py | 全绿 | 计划 |
| T3 | inspect/plan（逻辑 ID 确定性+去重；分级判定；披露收集） | tests/unit/project_exchange/test_inspector.py | 全绿 | 计划 |
| T4 | 不可信 ZIP 矩阵（slip/绝对/穿越/符号链接/重复条目/坏扩展/嵌套 zip/炸弹/条目帽/manifest 缺失/哈希不符） | tests/unit/project_exchange/test_untrusted_zip.py | 全绿（每负例 typed 码） | 计划 |
| T5 | 物化 writer（多图层 gpkg/空图层/栅格拷贝/表格/台账/独立读回保真） | tests/unit/project_exchange/test_writer.py | 全绿 + geopandas/rasterio 独立读回 | 计划 |
| T6 | 原生包往返（合成 fc+栅格+表格+workflow+map → 导出 → 全新项目导入 → IDs 重映射 → 校验 semantic） | tests/unit/project_exchange/test_roundtrip.py | 全绿（旅程 1 核心） | 计划 |
| T7 | 标准 GIS 独立读回（ogr2ogr CLI + geopandas/rasterio 读 gpkg/tif，CRS/字段/几何/nodata） | tests/unit/project_exchange/test_standard_interop.py | 全绿（旅程 2） | 计划 |
| T8 | 恶意/越权端到端（篡改包/许可拒绝/越权上传/错误上传 ID/取消无半成品） | tests/unit/project_exchange/test_security.py | 全绿（旅程 3） | 计划 |
| T9 | Pi 工具注册与有界响应（六工具在 registry；typed 错误面） | tests/unit/project_exchange/test_pi_tools.py | 全绿 | 计划 |
| T10 | REST 契约（路由挂载；鉴权负例 401/404；上传白名单） | tests/unit/project_exchange/test_routes.py | 全绿 | 计划 |
| T11 | 复现证据（环境版本漂移；verify_import 分级；显式重算对比） | tests/unit/project_exchange/test_verify.py | 全绿 | 计划 |
| T12 | 需求契约映射（任务书条款 → 生产调用链证据） | tests/unit/project_exchange/test_requirement_contract.py | 全绿 | 计划 |
| T13 | 前端 api client（冲突映射/体积格式/轮询终态） | frontend/lib/api/project-exchange.test.ts | vitest 全绿 | ✅ 已跑 |
| T14 | 门禁（eslint 0 warning / tsc 无新增错误 / ruff / mypy 白名单） | ci-local --fast 子集 | 无新增 | 计划 |

## 基线既有失败（非本任务引入，记录不修）

- `frontend/lib/mapspec/live-spec.test.ts(129,70)` TS2353 —— 干净 master 基线复现（stash 验证），非本任务面。
- 迁移检查/全量 pytest 基线在 R1 阶段补跑记录。

## 性能基线（R1 前补）

- 小包（3 数据集×1k 要素+1 栅格）导出/导入耗时与内存；命令与环境随跑随记。
