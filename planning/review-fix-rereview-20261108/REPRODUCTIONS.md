# REPRODUCTIONS — 每个修复的先红后绿证据

命令统一在 worktree 根（后端）或 frontend/（前端）执行；
Python：`/home/kevin/projects/webgis/webgis-ai-agent/.venv/bin/python`（3.13.17, pytest 9.1.1）；
前端：`./node_modules/.bin/vitest run`（vitest 4.1.12）。

## A-1 — SPATIAL_JOIN 声明 CRS 盲区（P1）

- 测试：`tests/unit/test_geocompute_execution.py::test_spatial_join_declared_projected_right_side`（+双侧 3857 守卫用例）
- 红（修复前，master 代码）：
  ```
  payload = ops.execute_node(self._ctx(), join, payloads)
  assert payload["metadata"]["join_pairs"] == 1
  E   assert 0 == 1
  ```
- 绿（修复后）：`37 passed`（全文件）×2 遍；邻接 federation/v5调度/spatial_decision/engine_bugs `51 passed`
- 语义复现（审计 A 运行时验证同源）：左 WGS84 点 (116.39, 39.91)，右 EPSG:3857 多边形（真实空间覆盖该点）→ 修复前 0 行，修复后 1 行

## C-1 — style-only 恢复不同步前端地图状态（P1）

- 测试：`frontend/lib/mapspec/user-mutation.test.ts`（refreshMapSpecAfterExternalMutation 4 例）+ `frontend/components/sidebar/map-product-versions.test.tsx`（1 例）
- 红（修复前）：
  - 组件级（真实行为断言）：`AssertionError: expected true to be false`（恢复后 HUD visible 不收敛）
  - 单元级：`TypeError: refreshMapSpecAfterExternalMutation is not a function` ×4
- 绿（修复后）：`Test Files 2 passed (2), Tests 26 passed (26)` ×2 遍；tsc --noEmit exit 0；eslint（4 文件）exit 0

## B-5 — 匿名会话导出者不可达诊断 sidecar（P2）

- 测试：`tests/unit/test_export_diagnostics_sidecar.py::test_anonymous_session_owner_reads_own_diagnostics`
- 红：`+ 403 = <Response [403 Forbidden]>.status_code`（匿名 + 有效 owner_token 期待 200）
- 绿：`12 passed`；同文件原有契约（owner 200/他者 403/missing 404/无凭据 401/遍历抵抗）全保持

## A-2 — service_area graph/dataset 不成对 AttributeError（P2）

- 测试：`tests/test_network_temporal_harness.py::test_service_area_graph_without_dataset_fails_typed`
- 红：`app/services/network/snapping.py:137: AttributeError`（NoneType.edges）
- 绿：`ValueError`（match="network_dataset"）；全文件 `13 passed` + `tests/test_network.py 11 passed` 合计 24×2 遍

## B-4 — 分布式锁同步释放 asyncio.run 必失败（P2）

- 测试：`tests/unit/test_distributed_lock_sync_release.py`（2 例，loop 亲和 fake client 模拟 redis-py 池亲和语义）
- 红：例1 `eval_calls == []`（DEL 从未发出）+ 例2 `TypeError`（_bound_loop 不存在）；审计 B 的生产日志证据：`session lock sync release failed ... attached to a different loop` → 锁活到 30s TTL
- 绿：`2 passed` ×2 遍；邻接锁套件（locks_leak/chaos_lock_registry/nested_mutations/pi_bridge_lock）`45 passed`

## B-1 — overwrite 锁内 descriptor 计算（P2 perf）

- 测试：`tests/unit/test_session_overwrite_lock_discipline.py`（2 例）
- 红：例1 `observed == [True]`（持锁状态下计算）；例2 `TimeoutError`（第二次覆写拿不到锁 —— 锁被 gated 的锁内计算持有）
- 绿：`2 passed` ×2 遍；邻接（async_session_data/l1_cache/disk_lifecycle/nested_mutations）`71 passed`
- 性能口径：50k-feature checkpoint 覆写场景，descriptor 遍历 O(features) 从锁内移到锁外 —— 停顿窗口不再阻塞任意会话的 store/get/delete（无新增限制作弊；行为语义不变）

## TESTFLAKY-1 — xdist 清盘竞争（P2 test-infra）

- 红（master 既有，非本分支引入）：`tests/test_map_export_download_auth.py` 串行 `6 passed`，`-n 4` 连续三轮 `1 failed`/`2 failed`/`2 failed`（404 假红，随机测试受害）
- 绿（修复后）：`-n 4` ×3 全 `6 passed`；两文件合跑 `-n 4` ×2 全 `18 passed`
