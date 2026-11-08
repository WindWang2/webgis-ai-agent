# TEST_MATRIX — 本地验证证据（证据级别标注）

Python：`/home/kevin/projects/webgis/webgis-ai-agent/.venv/bin/python`（3.13.17, pytest 9.1.1）；前端：vitest 4.1.12。
证据级别：unit（纯单元）/ integration（跨模块接线）/ contract（路由契约）——本分支修复全部可用本地 unit/integration/contract 级验证，无需 Pi RPC/浏览器/真实服务。

## 修复定向测试（每项两遍一致）

| 修复 | 测试 | 级别 | 结果（×2） |
|------|------|------|-----------|
| A-1 | tests/unit/test_geocompute_execution.py（39 用例，含 +2 新增） | unit | 37→39 passed ×2（早期 37，加守卫用例后计入下方全量 194） |
| C-1 | frontend/lib/mapspec/user-mutation.test.ts（20）+ components/sidebar/map-product-versions.test.tsx（6） | unit+component | 26 passed ×2；tsc exit 0；eslint 4 文件 exit 0 |
| B-5 | tests/unit/test_export_diagnostics_sidecar.py（12，含 +1 新增） | contract | 12 passed ×2 |
| TESTFLAKY-1 | tests/test_map_export_download_auth.py | contract | -n4 ×3 全绿（master 基线 1/2/2 随机红）+ 两文件合跑 -n4 ×2 18 passed |
| A-2 | tests/test_network_temporal_harness.py（13，含 +1） | unit | 13 passed ×2（并入全量） |
| B-4 | tests/unit/test_distributed_lock_sync_release.py（2 新增） | unit（loop 亲和 fake） | 2 passed ×2 |
| B-1 | tests/unit/test_session_overwrite_lock_discipline.py（2 新增） | unit（并发时序） | 2 passed ×2 |

## 全量 targeted 回归（所有触及面，连续两遍完全一致）

后端 17 文件（geocompute/federation/锁×4/会话×4/导出×2/网络×2/spatial×2）：
```
-n 4 …tests/unit/test_geocompute_execution.py test_data_fabric_federation.py
test_distributed_lock_sync_release.py test_session_overwrite_lock_discipline.py
test_export_diagnostics_sidecar.py test_map_export_download_auth.py
test_network_temporal_harness.py test_network.py test_session_locks_leak.py
quality/test_chaos_lock_registry.py unit/test_nested_session_mutations.py
test_pi_bridge_lock.py test_async_session_data.py test_session_l1_cache.py
test_session_disk_lifecycle.py unit/test_spatial_decision_correctness.py
test_spatial_engine_bugs.py
→ run1: 194 passed (12.44s)   run2: 194 passed (10.81s)
```
前端（lib/mapspec 全目录 14 文件 + map-product-versions + agent-runtime）：
```
vitest run lib/mapspec/ components/sidebar/map-product-versions.test.tsx lib/agent-runtime.test.ts
→ run1: 15 files / 90 passed   run2: 15 files / 90 passed
```

## 静态门禁

- ruff check（本分支全部 6 个触及的源文件）：All checks passed
- frontend tsc --noEmit：exit 0
- git diff --check：干净

## master 既有失败（分栏：非本分支引入，未跳过不掩盖）

- tests/test_pdf_export_auth.py::test_pdf_export_auth_before_rendering[anonymous-owner-vector-pdf] 与 [authenticated-user-vector-pdf]：在 HEAD（含 stash 掉本分支改动后）即失败 —— master 既有红，与本分支变更无关（本分支未触及 vector-pdf 链）。已如实记录，不改动（其根因属在途 #2015 出版链邻接区域）。

## 未运行项（诚实披露）

- 未运行完整 tests/（2336 文件）全量——按任务资源纪律（-j4 上限、错峰）执行 targeted 面；全量 lane 由 CI 承担。
- 未运行浏览器/Playwright、真实 Redis/PostGIS/Pi RPC lane——本分支修复的验证路径全部为本地 unit/integration 级（见证据级别列），无一项依赖这些外部服务才能复现或验证。

## Fix-2 追加（R2 闭环验证）

| 修复 | 测试 | 级别 | 结果（×2） |
|------|------|------|-----------|
| R2-N1/N2 | user-mutation.test.ts +2（stale 不镜像/剪行、Math.max 5≠11） | unit | 红→绿；lib/mapspec+use-sse-stream+组件合跑 136/136 ×2 |
| R2-N1 SSE 变体 | use-sse-stream.test.ts +1（rev-3 stale 事件不剪 row-9/不加 LOLD） | unit(hook) | 红→绿 |
| R2-N3 | test_export_diagnostics_sidecar.py（wrong-token detail == 缺席 detail） | contract | 红→绿 |
| R2-N4 | test_network_temporal_harness.py（both-None ValueError） | unit | 红→绿 |
| R2-N6 | test_distributed_lock_sync_release.py（无 never-awaited warning） | unit | 红→绿；27/27 ×2（network 合跑 38） |

含 Fix-2 的全量 targeted 回归：后端 194 ×2、前端 138 ×2 完全一致。
