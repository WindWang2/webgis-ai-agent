# FIX_LEDGER — 变更→测试→commit→finding 映射

| commit | 分类 | 变更文件 | 测试（先红后绿） | Finding |
|--------|------|----------|------------------|---------|
| 0c74f572d | F3 前端稳定性 | frontend/lib/mapspec/user-mutation.ts（+refreshMapSpecAfterExternalMutation）、frontend/components/sidebar/map-product-versions.tsx（接线）、两测试文件 | lib/mapspec/user-mutation.test.ts ×4、components/sidebar/map-product-versions.test.tsx ×1；26/26 两遍 + tsc/eslint 0 | C-1 (P1) |
| 88cea9daf | F1 可信结果 | app/services/geocompute/ops.py（_op_spatial_join CRS 归一）、tests/unit/test_geocompute_execution.py（+2 用例） | geocompute 37/37 两遍 + 邻接 51 | A-1 (P1) |
| c2707b4dc | F1 安全/权限 | app/api/routes/map.py（get_export_diagnostics 分层鉴权）、tests/unit/test_export_diagnostics_sidecar.py（+1 用例） | 12/12；既有契约全保持 | B-5 (P2) |
| b450012c4 | F4 质量门禁 | tests/test_map_export_download_auth.py、tests/unit/test_export_diagnostics_sidecar.py（tmp_path 隔离） | -n4 ×3 全绿（master 上 1/2/2 随机红） | TESTFLAKY-1 |
| 74b1f9ac8 | F2 执行健壮性 | app/services/network/service_area.py（入口校验）、tests/test_network_temporal_harness.py（+1 用例） | 24/24 两遍 | A-2 (P2) |
| 42621250a | F2 并发/生命周期 | app/services/distributed_lock.py（绑定 loop 递交 + 常量）、tests/unit/test_distributed_lock_sync_release.py（新文件 2 用例） | 2/2 两遍 + 邻接 45 | B-4 (P2) |
| 7fc214cac | F4 性能 | app/services/session_data.py（overwrite 锁外 descriptor + 守卫回写）、tests/unit/test_session_overwrite_lock_discipline.py（新文件 2 用例） | 2/2 两遍 + 邻接 71 | B-1 (P2 perf) |

## 保护矩阵对拍（git diff origin/master..HEAD 变更文件 × 7 PR 清单）

本分支全部变更文件：
- app/services/geocompute/ops.py — 无 PR 占有 ✓（#2016 占有的是 app/lib/gis/algorithms/**）
- app/services/data_fabric/query/federation.py — 未改（仅审计引用；修复在 geocompute 层）✓
- app/services/network/service_area.py — 无 PR 占有 ✓
- app/services/distributed_lock.py — 无 PR 占有（B-6 相关的 session_context_refs.py 未动）✓
- app/services/session_data.py — 无 PR 占有 ✓
- app/api/routes/map.py — 无 PR 占有（#2012 占有 chat.py/health.py；#2013 占有 upload.py）✓
- frontend/lib/mapspec/user-mutation.ts、frontend/components/sidebar/map-product-versions.tsx — 无 PR 占有（#2014 占有 lib/mapspec 之外的区域：lib/interaction、lib/map-operations、components/map、components/agent；#2015 占有 lib/map-kit、lib/api/publication.ts）✓
- tests/*（本分支新增/修改的 5 个测试文件）— 无 PR 占有 ✓
- 无 pyproject.toml / .goal-loop-ledger.md / vendor / 依赖变更 ✓

## Fix-2（R2 冷审闭环）

| commit | 分类 | 变更文件 | 测试（先红后绿） | Finding |
|--------|------|----------|------------------|---------|
| 4b12c8359 | F3/F5 状态同步 | frontend/lib/mapspec/user-mutation.ts（commit-accept 守卫 + Math.max）、frontend/lib/hooks/use-sse-stream.ts（SSE 同款守卫）、两测试文件 | user-mutation.test.ts +2（stale 不镜像/不剪行、max 5≠11 红→绿）、use-sse-stream.test.ts +1（rev-3 stale 事件红→绿）；合 136/136 两遍 | R2-N1 (P2)、R2-N2 (P3) |
| 1b8077691 | F1/F2 收口 | app/api/routes/map.py（统一 404 detail）、app/services/network/service_area.py（both-None 守卫）、app/services/distributed_lock.py（coro.close）、三测试文件 | detail 断言/AttributeError/warning 捕获先红后绿；27/27 两遍（network 合跑 38）；ruff 通过 | R2-N3/N4/N6 (P3) |

保护矩阵对拍（追加）：use-sse-stream.ts（frontend/lib/hooks/，无 PR 占有）✓；其余文件同 Fix-1 已核。
