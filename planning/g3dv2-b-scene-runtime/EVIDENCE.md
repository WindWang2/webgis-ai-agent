# G3DV2-B EVIDENCE — 验证证据与真实运行

## 提交链（branch zcode/g3dv2-b-scene-runtime，base origin/master@70dab2b9）
1. `4d6d56960` B0 侦查账本（BASELINE/GAP/PLAN/重叠矩阵/DECISIONS/TASKS/LOOP_STATE）
2. `6309f8487` M2 glTF/3D Tiles 严格解析（byteStride 交错正确读、sparse 子集、normalized 词表、refine 严格化+继承；+12 测试）
3. `25d1f11e3` M3 跨资产累计预算 + 全局信号量 + setStyle refCount 泄漏修复（+9 测试）
4. `9c415d084` M4 生命周期（cancelled/context_lost）+ WebGL context lost/restored（+5 测试）
5. `3174a0c89` M1 纯 3D 真 ACK（信封指纹回退/scene_work/DTO scene3d_assets/corpus s13/OpenAPI 刷新；+23 测试）
6. `6c2ea304a` M5 工作台交互 + j9 纯 3D ACK 断言（+4 测试）
7. `ab9145b29` lint 清偿
8. `858aef27f` j9 toggle 语义修正 + 拾取卡命名空间限定键

## 真实浏览器证据（Chromium headless + SwiftShader WebGL）
- **j9 journey 通过（3/3 次单跑：17.1s / 15.8s / 全量套件内亦过）**：SSE step_result（v1.7 mapspec + fly_to + **render_work 信封**）→ GLB fetch → custom layer 真渲染 → **纯 3D（零 2D 图层）cartographic-observation POST 断言通过**：
  - POST body `mapspec_fingerprint ===` 信封指纹（HUD/游标无指纹 → 信封回退生效）；
  - `scene3d_assets` 含 mounted 证据；
  - `apply_ack.work.work_id === rw-4-j9scene3dack01`（work echo 对账）；
  - 3D toggle（aria-pressed 门控）→ 屏心点击 → 3D 拾取卡（zh aria-label）+ `feature_selected` IR `scene3d:campus`；
  - `window.__mapErrors` 0 条、无 fatal console error。
- **j1–j8 全量离线重跑**：11 passed / 7 skipped（非 mock 变体跳过）。并行负载下存在既有抖动（两轮全量分别出现 j9/j7 各一次超时，**两者单跑均通过**——dev server 编译竞态，非本分支引入；#1834 起 CI lane 承担全量职责，见其 EVIDENCE 已知限制 7）。

## 本地质量门（全部实跑）
| 门 | 命令 | 结果 |
| --- | --- | --- |
| 前端单测 | `npx vitest run --maxWorkers=2` | **4659 passed + 1 expected-fail**（492 文件；唯一红 = `lib/map-commands/catalogue-contract.test.ts`，**origin/master 干净检出同红**——#1829 后端 4 条 3D 命令未注册前端目录，与本分支零文件交集） |
| TS 类型 | `tsc --noEmit && tsc -p tsconfig.test.json --noEmit` | **0 错误** |
| Lint | `eslint . --max-warnings 0` | **0 错误 0 警告** |
| 后端单测 | `pytest tests/cartography/ -q --no-cov` | **2912 passed**；唯一红 = `test_mutation_parity_corpus[set_scene_invalid_mode]`，**origin/master 干净检出同红**（#1829/#1830 遗留，与本分支无关） |
| 后端定向 | `pytest tests/unit/gis_harness/test_scene3d_observation_evidence.py + render_work_corpus_v1 + quality/test_api_compatibility` | **全绿**（8+12+11） |
| Ruff | `ruff check`（6 个改动后端文件） | 通过 |
| i18n | zh-CN/en-US map.json 键集 diff | 双向零差 |

## 生产 bundle 实测（next build --webpack，flock 串行）
- master@70dab2b9：`.next/static/chunks` 75 文件 / **5.89MB**；最大 page chunk 671.1KB（three 经 map-panel 动态可达路径，不变）。
- 本分支：75 文件 / **5.91MB（+20KB，~0.3%）**；增量集中在 scene3d chunk（276.9→289.5KB，+12.6KB）与共享 chunk 微增。**零新依赖、零新 route chunk**。

## 既有 master 红灯对照（干净 origin/master 检出复跑取证）
1. `frontend/lib/map-commands/catalogue-contract.test.ts` —— 幽灵命令 `get_3d_capabilities/apply_scene_representation/fly_camera/run_3d_analysis`（#1829 app/ 发射 vs 前端 COMMAND_CATALOGUE）。
2. `tests/cartography/test_mutation_parity_corpus.py::[set_scene_invalid_mode]`。
3. 归属 g3d04/g3d05 的审查域，本 track 不越界修复（PR_OVERLAP_MATRIX 纪律）。

## 环境事实
pnpm 10.34.9 / node v24.21.0 / three 0.186.1 / next 16.4.3 / vitest 4.1.11；Python 3.13.17（主仓 .venv，requirements.lock 与 worktree 逐字节一致）；Playwright 1.64.1 + chromium（SwiftShader 软渲染）；无 GPU 真机 —— 所有 WebGL 结论仅代表软渲染环境（诚实边界，见 RISKS）。
