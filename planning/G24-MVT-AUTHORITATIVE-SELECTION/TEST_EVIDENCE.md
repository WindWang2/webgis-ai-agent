# G24 TEST_EVIDENCE — 测试与性能证据

环境：16 CPU / 62GB RAM；后端 = 主仓 `.venv`（只读借层）+ worktree 代码；前端 = worktree 独立 node_modules（pnpm 10.34.9）；vitest 8.3.6 / node 24。

## 1. 后端（pytest，全部 worktree 内复跑）

| 套件 | 命令 | 结果 |
|---|---|---|
| selection handles 域内 | `pytest tests/unit/test_selection_handles.py --no-cov -q` | 16 passed |
| selection handles REST | `pytest tests/unit/test_selection_handle_routes.py --no-cov -q` | 10 passed |
| selection 工具（真实 ToolRegistry dispatch） | `pytest tests/unit/test_selection_tools.py --no-cov -q` | 5 passed |
| 身份防线（wfid 链/歧义/2^53） | `pytest tests/unit/test_g24_identity_guards.py --no-cov -q` | 5 passed |
| 大图层证据（100k 档） | `pytest tests/unit/test_g24_scale_evidence.py --no-cov -q -s` | 3 passed, 1 skipped（1M 档 gated） |
| 编辑→选择旅程（acceptance） | `pytest tests/acceptance/gis_agent/test_g24_selection_edit_journey.py --no-cov -q` | 2 passed |
| 既有回归：feature_edit 全族 | `pytest tests/unit/feature_edit/ --no-cov -q` | 123 passed |
| 既有回归：MVT 编码/位置 id | `pytest tests/unit/test_mvt_positional_id.py tests/unit/test_mvt_encoder.py --no-cov -q` | 46 passed |
| 既有回归：feature_pages | `pytest tests/unit/test_feature_pages.py --no-cov -q` | 11 passed |
| OpenAPI 快照 | `API_SNAPSHOT_UPDATE=1 pytest tests/quality/test_api_compatibility.py`（additive 刷新，0 breaking） | 11 passed, 1 skipped（刷新） |
| 全量 tests/unit | `pytest tests/unit --no-cov -q`（重跑无 -x） | 见 LOOP_LEDGER 最终记录（首跑 426 passed 后停在预存错误 test_api_v2.py —— 干净 BASE b8f2a6ecc 同条件复现 4 errors，预存非本方向引入） |

## 2. 前端（vitest，worktree 内）

| 套件 | 结果 |
|---|---|
| lib/edit/（geometry-hit 6 + mvt-pick 12 + layer-edit-bridge 既有 + sketch 既有） | 13 files / 125+18 passed |
| lib/selection/（store v4 + brush + 大数据契约） | 全 passed |
| lib/api/selection-handles.ts 编译 + selection-table-data.test | 3 passed |
| components/map/feature-layer-editor.test.tsx（新） | 4 passed |
| map-panel.selection-truth / map-panel.test / lib/map-components/（既有回归） | 15 files / 128 passed |
| typecheck | `tsc --noEmit` exit 0 |
| eslint（改动文件） | 0 error 0 warning |

（全量 `vitest run` 最终计数见 LOOP_LEDGER。）

## 3. 性能证据（P7）

```
[G24 P7] tier=100000 dataset=14.6MB index_build=4359ms indexed_lookup=34.5ms response=132B linear_scans=0
```

- 传输解耦：100k 点数据集 14.6MB，一次点击权威回查响应 **132B**、零线性扫描
 （`_find_feature_by_id` 计数器=0，索引路径 O(1)）。
- 1M 档位：`G24_MILLION=1 pytest tests/unit/test_g24_scale_evidence.py -k million`
 （资源分档披露；本机未默认执行）。
- 前端解耦断言：mvt-pick 单元测试钉住「pick 不整包水合」（仅 /feature/{id} 单请求，
  无 requestRefFC 全量调用路径进入编辑拾取）。

## 4. 边界与未测项（诚实披露）

1. **真实浏览器 E2E（A25）未执行**：需要全栈 dev server + Playwright + 宿主重任务锁
 （并行方向占用）；已用组件级测试（真实 maplibre map mock + store）+ 服务端真实旅程
 覆盖同等断言面。浏览器级点击→编辑走通留待集成环境（PR 标注）。
2. **真实 Pi 子进程 + LLM stub 旅程**：本方向工具为确定性只读， journeys 走真实
 ToolRegistry dispatch + 真实数据面/事务；Pi 原生 RPC/discovery 由 #2081 既有套件覆盖
 （工具按同一注册规范，Pi 面可发现性由 registry 导入测试保证）。
3. `tests/unit/api_contract/test_api_v2.py` 4 errors 为 **预存失败**（干净 BASE 同条件复现，
   见上表）；非本方向引入、未在本方向修复（不吞并他人问题域）。
4. fieldcompute UI 内的选择集入口未新增按钮（物化通道 + 端到端通道有测试；UI 深接线
   留给消费方向按需启用 —— 已在 DUPLICATION_MATRIX 标注）。
