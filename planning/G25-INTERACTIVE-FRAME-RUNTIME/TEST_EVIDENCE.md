# G25 TEST_EVIDENCE — 测试与运行证据

执行环境：worktree `webgis-wt-g25-frame-runtime-20261111T173941`（BASE
b8f2a6ecc）；Node 24 / pnpm 10.34 / Python 3.12（uv venv）；Playwright
Chromium 1.65；16 核 / 62GiB。重任务均持 `.heavy-task.lock`。

## 1. 确定性单元/契约（vitest，jsdom）

命令：`pnpm vitest run <file>`（frontend/ 下）

| 文件 | 用例 | 结果 |
|---|---|---|
| lib/cartography/where-ast.test.ts | 8 | ✅（后端 evaluate_where 语义对齐） |
| lib/cartography/frame-runtime.test.ts | 9 | ✅（状态机/指纹/后继/守卫） |
| components/map/frames/frame-camera-hub.test.ts | 8 | ✅（传播/隔离/回声/期望相机/代际） |
| components/map/frames/frame-runtime-stores.test.ts | 4+4 | ✅（registry 预算 / LRU 失效） |
| lib/cartography/frame-effective-spec.test.ts | 7 | ✅（过滤/共享分级/typed error/时间归一） |
| components/map/frames/frame-grid.test.tsx | 7 | ✅（开合/激活 CAS/键盘/预算/观察/后继） |
| lib/cartography/frame-edit-scope.test.ts | 4 | ✅（A03 仅本幅路由） |
| components/map/frames/frame-viewport.test.tsx | 5 | ✅（登记释放/捕获/contextloss/30 次挂卸） |

## 2. 后端（pytest，独立 venv + 隔离 SQLite）

命令：`.venv/bin/python -m pytest tests/unit/gis_harness/frame_products/test_g25_observation_wiring.py --no-cov -q`

- **6 passed**：DTO frames 归一（词表/≤16/export_verified 拒绝/缺席 None）、
  status 接线（live+snapshot+not_observed 独立、frameA 不补 frameB）、
  stale revision 不 verified、无观察全 not_observed。

## 3. 真实浏览器（Playwright Chromium，WebGL）

### J17 mock 档 ✅

命令（独立端口，见 §5 教训）：
`E2E_MODE=mock E2E_PORT=3325 pnpm exec playwright test e2e/journeys/j17-frame-interactive.journey.ts --workers=1`

- 3 轮稳定通过（6.5s / 10.0s / 13.6s→修复后）。断言链：入口 chip →
  三瓦片稳定 frame_id → 活动瓦片**真实 maplibre canvas**（WebGL）→
  页面 canvas 总数 ≤2（A09 真实计数）→ 未挂载帧观察 not_observed →
  激活第二幅（set_frame_product + expected_revision CAS + scope 徽标 +
  活动瓦片转移）→ 键盘 ']' / '[' 切换 → Escape 关闭回 chip。
- 旁证：j1 @smoke 同环境通过（4.6s）→ 环境健康；j16 通过（4.8s）→
  G15 面板旅程不回归。

### J17 real 档 ✅（真实服务端半场端到端，8.2s 通过）

本地栈（全部本方向独立启动，独立端口/数据目录）：
- LLM stub `node e2e/tools/llm-stub.mjs` :8767（确定性 OpenAI 线协议）
- 后端 uvicorn :8001（`/tmp/g25_data` 独立 SQLite + DATA_DIR；
  `GIS_MANIFEST_STRICT=0` 本地降级——悬挂 capability 全为 G17/G19/G10/
  G16 方向工具，非本方向引入）
- vendor/pi 子模块 init（pin dd30101 = BASE 期望）+ `scripts/build-vendor-pi.sh`
  构建 → Pi RPC agent 启动（match=True；修前 chat 503 fail-closed）
- admin `manage.py create-admin`；真实登录 200
- 前端 next dev :3325（独立端口——教训见 §5）

通过断言链（全真实服务）：
1. UI 真实对话建会话（POST /chat/stream，stub 文本回合）
2. `scripts/seed_journey_frames.py`（j2 同款 runner-local 纪律 + fail-closed
   所有权守卫）经**真实 apply_gis_mutation 管线**落
   UpsertSource/UpsertLayer/SetLayout/SetFrameProduct（=mapspec 路由与
   frame_product Pi 工具同一生产写路径）；返回 mutation_revision>0 与
   mapspec_fingerprint
3. 真实 POST /cartographic-observation：指纹门接受 + frames 增维
   （live_observed/snapshot/export_verified-拒绝）
4. `--mode verify` 读回 frame_product_status 终验：逐帧行
   fp-r-2020=live_observed / fp-r-2022=snapshot /
   fp-r-2024=not_observed（词表外模式 → 证据缺席，不冒充）

平台边界（如实披露，非本方向越权代修）：
- 制图 MapSpec 仅经 live-turn SSE 到达浏览器；无 turn-less/reload 恢复
  通道 → 「seed 后浏览器内网格交互」真栈不可达（mock 档以同构 SSE 夹具
  在真实浏览器覆盖该面）。
- LLM stub call-count 脚本 × Pi-only 多轮调用 seam 失配（dispatch cache
  miss → tool_calls=0）：工具回合本地不可用（G12/G22 修复面）。

## 4. 全量回归

- 前端全量 `pnpm vitest run`（i18n 修复前）：**5221 passed / 4 failed**；
  4 失败全部在干净 BASE（stash 复跑）同样出现 → 既有失败：
  1. `data-import/import-workbench.tsx` L724 裸 CJK（G17 文件）
  2. `comparison-view.test.tsx` clip-path `0px` 归一 ×2（jsdom 31）
  3. `poi-info-panel.test.tsx` 顶部翻转 ×1
  （主工作区 816705218 不现——差异来自 #2084-2086 三合并；非本方向
  改动引入，登记不改。）
- 本方向引入的 17 处裸 CJK 已全部迁 i18n（`frameGrid` 命名空间 zh/en），
  修复后 no-raw-cjk 仅剩上述 BASE 1 处。
- 后端受影响面回归：frame_products 全目录 + mapspec mutation 合同
  （提交前跑，见 PR 流程）。

## 5. 环境教训（跨方向共享）

- **playwright `reuseExistingServer` 会吃掉别的 worktree 的 dev server**：
  默认端口 3310 被 G22 worktree 占用 → 我的旅程跑在对方代码上（现象：
  j16 过、我的 chip 永不出现）。所有并行方向 E2E 必须显式独立
  `E2E_PORT`。
- `pnpm vitest` 并发实例可能争用 vite 缓存 → 串行/等待重跑后正常。

## 6. 已知非致命噪声

- react-map-gl（本仓新 major）× maplibre 6.10：帧视窗挂载时 1 条
  console error（`Invalid type: 'container' …`，库内部竞争）。定位：由
  `id` prop 路径触发（去 id 消失但功能受损）；地图渲染/断言/计数均正常。
  记录不改（升级归属 G22/Pi 方向）。
