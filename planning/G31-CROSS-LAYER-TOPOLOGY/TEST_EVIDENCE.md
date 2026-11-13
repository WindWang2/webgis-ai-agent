# G31-CROSS-LAYER-TOPOLOGY — TEST_EVIDENCE

执行环境（记录：任务书 §五/WP12）
- worktree：g31-cross-layer-topology-20261112T144915Z @ base ca7b3ec27（branch zcode/g31-cross-layer-topology-20261112T144915Z）
- Python：主工作区共享 venv（shapely 2.1.2 / pyproj 3.7.2 / pydantic 2.13）；pytest 单进程（默认并发纪律）
- vendor/pi：本 worktree 子模块 pin b4afdddf44（v1.5.2），`scripts/build-vendor-pi.sh` 本地构建（bundle 84 files, 9.2 MiB；重阶段锁内）
- 前端：pnpm --frozen-lockfile；vitest 4.1.12 / tsc 5.x

## 1. 真实 Pi 旅程（J-G31，tests/integration/test_pi_g31_topology_journey.py）

门控 `PI_JOURNEY_E2E=1`（显式 opt-in）。driver 独立进程：node stub LLM（SSE）→ uvicorn 生产 app（真实 vendor/pi bridge，rpc-entry 子进程）→ JWT 用户流式 turn。

旅程：服务端种子（AOI + 带洞地块 refs）→ turn「检查地块拓扑」→ tool_search 发现 → `topology_ruleset_configure` → `topology_check_run` → REST/回执复核。

最终 verdict（driver18，2026-11-13）：
```json
{"journeys": {"tools_receipts": true, "run_completed": true, "findings_found": true,
 "count_semantics_disclosed": true, "gap_area_oracle": true, "witness_readable": true,
 "tool_status_consistent": true, "tool_findings_consistent": true}, "fail": [],
 "gap_area_m2": 39919.634}
```
- 缺口面积 39,919.6 m² vs 人工 oracle 200m×200m=40,000 m²（误差 0.2% —— 投影弦化，容差 ±1% 内）
- 同链贯通层级：真实 Pi 子进程（rpc-entry）✓ / 生产扩展面（tool_search 发现 → 裸名 dispatch）✓ / HTTP 回调 dispatch ✓ / 本域服务（ruleset+run+findings+witness）✓ / REST 复核 ✓ —— 五层同链联跑

旅程调试中发现并修复的真实缺陷（记录于 LOOP_LEDGER Round 3–4）：
① agent 无用户身份时 submit_run/runner 的所有权与会话锚定缺失；② REST 请求体 session_id 不能作为所有权锚（安全回归，已收紧为仅显式工具路径）；③ dispatch 透明解引用把 ref: 字符串换成内联载荷 → 工具层重物化为会话 ref；④ asyncio.run 拆散调用拖垮共享 loop → driver 后段复核改走 SSE 回执；⑤ WorkbenchError status kwarg 碰撞。

## 2. 单元/语义（tests/topology_workbench/test_evaluators_g31.py，20 passed）

人工可数 oracle（北京锚点 ~116.4E/39.9N，UTM 32650 平面米制）：
- T02 缺口 40,000 m² ±1%；T03 湖面排除后缺口消失、排除面积 90,000 m² 披露
- T04 嵌套 10,000 m²；T05 共边零面积不误报（boundary_touch 语义）
- T06 越界 9,000 m²（150m 建筑 × 60m 出界，±2%）
- T08 反经线/极区(89°)/超大范围(40°) → typed blocked（antimeridian/polar/extent）；正常范围 → EPSG:32650
- T09 自交/空/缺坐标各自剔除计数（invalid/empty/bad_coords），MultiPolygon 带洞有效
- T10 0.5m 端点通过 vs 2m 违规（1m 容差）；T11 出口区豁免计数可见
- T16 同快照重跑 key 稳定、同对要素多片独立 key；T17 finding 触顶 → partial+计数

## 3. API/持久化（tests/topology_workbench/test_api_g31.py，13 passed）

J-G31 全旅程（HTTP+DB+refs+BlobStore+eager celery）：检查 → witness 定位（Polygon/stale=false/双源 locator）→ 例外（理由必填、accepted_exception 计数）→ 修复（源编辑）→ 复验 resolved（resolved_count≥1、例外跨运行延续）。
- T12 complete 角色+scope → scope_not_allowed；T13 where 全集消费（examined=10/60）
- T14 bbox scope 披露 bbox_intersects_coarse；T15 revision 前进 → witness stale=true
- T18 取消 → cancelled 无迟到 findings；T19 越权 404/列表过滤
- T20 源变更 → 例外 stale、新缺口 open；T21 提交后漂移 → source_changed_after_submission
- T23 归属选择类候选（无假 receipt，源字节不变）；all-blocked 不显绿；导出回读核验 verified=true

## 4. DSL/修复桥（tests/topology_workbench/test_ruleset_repair_g31.py，13 passed）

词表/负值/NaN/未知键/重复 id/角色缺失 typed 拒绝；容差单位分离（面规则拒收 tolerance_m）；digest 随参数变化；fill_gap 无 keeper → typed 拒绝；cross_layer 候选=resolve_overlap（归属必选）。

## 5. 资源曲线（tests/topology_workbench/test_perf_curve_g31.py，3 passed）

10k 级实测（单进程 pytest，VmHWM 峰值 RSS）：
| 用例 | 输入 | 候选对 | findings | computation | wall | 峰值RSS |
|---|---|---|---|---|---|---|
| cross_layer_no_overlap（贴邻网格） | 10k×10k 面 | 0（缝隙嵌入不交叠） | 0 | full | 0.05s | 390MB |
| area_coverage | AOI 100 面 × coverage 10k 面 | — | 100（3m 缝隙） | full | 4.0s | 407MB |
| line_endpoints_connected | 10k 线 × 10k 设施点 | 10,000 | 5,000（触顶） | partial（诚实） | 4.2s | 420MB |

注：线端点用例故意全部远端未连接 → 5000 finding 上限触发 → partial + 计数披露（不伪全量 pass）。预算拒绝路径：快照 >50k 要素提交期 typed 拒绝（contracts.MAX_FEATURES_PER_SNAPSHOT）。

## 6. 兼容回归

| 套件 | 结果 |
|---|---|
| tests/data/（quality/repair/field_compute 等全量）+ selection_handles | 1212 passed, 11 skipped, **1 pre-existing fail**（test_field_compute openapi 快照 —— 干净 base git stash 后同败，非本任务引入） |
| tests/unit/feature_edit/（G14） | 123 passed |
| tests/test_api_docs_drift / alembic_metadata / deploy_migration_wiring | 全 passed（api-docs 已再生成） |
| alembic upgrade head（fresh sqlite） | 0105 应用成功（4 张 topology_* 表 + CHECK/索引与模型一致） |
| tests/test_api_auth_required | 1 pre-existing fail（data_import.py G17 遗留；干净 base 同败。本任务 17 个端点全部 get_current_user 强制认证） |

## 7. 前端（vitest）

- topology-workbench-panel：10 passed（维度分离渲染/全量计数披露/相机定位/例外理由必填/归属选择披露/复验/绑定提交/typed 错误翻译/stale 披露）
- quality-panel（旧样例审计回归）：5 passed；project-assets-section 挂载回归 passed；i18n key-completeness passed
- tsc -p tsconfig.test.json：72 errors = clean base 基线（本任务文件零贡献；修复过程中曾引入 21 个已全部清零）

## 8. 未运行 / 边界（如实）

- 真实浏览器 E2E（Playwright）：面板以 jsdom/Testing Library 覆盖请求链与交互；未启动真浏览器
- 真实多副本 Redis/celery worker：USE_REDIS=false eager 同形路径；多副本未验证（G23 同边界）
- GEOS 不可中断单步的取消延迟：约束=预算上限（RULE_WALL_CLOCK_S 120s/规则 + 批间 2s 探针），未做注入实测
