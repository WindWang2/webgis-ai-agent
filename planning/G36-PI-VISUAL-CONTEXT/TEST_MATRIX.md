# G36-PI-VISUAL-CONTEXT TEST_MATRIX

验证时间：2026-11-15（worktree g36-pi-visual-context-20261114T151654Z；.venv-g36 python3.13）
vendor/pi @ b4afdddf44（pin 一致；pi_doctor 6 域全 PASS；466 工具含 webgis_visual_evidence_read）

## 测试层级与结果

### 评审修复轮（commit 399f94238）
- P1#1/#14 工具能力门访问面修复（get_bridge_pool+session 路由+rpc 属性）；
  P1#2 V33 预检每 turn 执行（无附件纯文本 turn 同样换代）；P1#3
  slim_event_result 排除 result_images；P1#4 store mutate 纯化（CAS 重试
  副作用随 result 重建 + holder 取最终尝试）。
- 新增修复回归 5 例：slim 排除 / CAS 重试不双减（模拟 WATCH 重跑）/
  无附件预检换代 / 索引不可用→None / bound 不被帽驱逐。

### L1 单元（tests/unit/test_visual_context_core.py）— 33 passed
decoder（真实解码/EXIF/炸弹/动画/近空白/crop 链/缩略图）、evidence 身份不变量、
store（登记/解析/撤销/共享/FIFO/预算）、resolver（授权/预算/能力门）、
profile（投影/三重门/缓存）、V33 台账、dispatch 窄提取。

### L2 集成（tests/integration/test_visual_context_wiring.py）— 9 passed
intake 路由（map_viewport/ref JPEG EXIF/map_region 右下角像素核对/layout_preview
unsupported/SVG/假 PNG/截断 JPEG/缺 revision）、revoke 幂等 + 读取 404、
ChatRequest.attachments 线格式（前缀/上限）、bridge `_resolve_turn_images`
（归属解析→SSE 级 sent/台账/撤销→换代→refused）、能力门 V11、
G29 路由 steer payload images（V31）+ 台账一致性。

### L3 真实 Pi 旅程（tests/integration/test_pi_g36_visual_context_journey.py，
PI_JOURNEY_E2E=1）— 1 passed（driver 20 断言全绿，30s）

同链：HTTP intake（multipart 真实路由）→ /chat/stream 携 attachments →
生产 app（uvicorn + 真实 bridge/扩展/dispatch）→ 真实 Pi 子进程（pin 构建）→
stub LLM（OpenAI 兼容）→ 真实 GIS 工具 → 权威 MapSpec → HTTP 会话详情。
- v01：stub 收到 OpenAI vision 线格式（data:image/…；base64 长度与 intake
  derived_size 4/3 精确一致）；attachment_receipt SSE；解释型 turn 零 mutation。
- tool_read_ok：webgis_visual_evidence_read 经真实 deferred 发现 + dispatch。
- v02：参考图附件 → tool_search + webgis_thematic_style_apply（真实 CAS）→
  breaks=[0,10,20,50] 保留 + palette Reds + decided_by=agent + revision+1。
- v20：GET /chat/sessions/{id} 返回附件元数据（vev- ref/derived_sha256，无 b64）。
- v33：DELETE 撤销 → 纯文本 turn 完成，stub 图像请求数不变（旧 pixels 不重送）。

### L3' 回归（base 127669273 + 本分支）
- test_schema_bounds_1648 / pi_stream_nonstream_parity / pi_turn_lock_ownership_1108 /
  pi_durable_cancel / visual_repair_endpoint / pi_result_abi：91 passed。
- pi_bridge_pool_prod_routing / pi_runtime_mode_disclosure / canvas_affordance /
  deep_review_runtime_api02 / review_core_medium_regressions：77 passed。
- 修复轮后全量复跑：core 33✓ + wiring 9✓ + schema/parity/abi/lock/
  durable/visual_repair/cross_tenant 113✓ + 真实 Pi 旅程 20 断言✓；
  ruff/eslint/tsc 全清。
- **既有失败（非本任务引入）**：tests/unit/test_turn_settlement_seam.py 5 例
  （MagicMock issue 非 awaitable）——干净 base 127669273 同条件复现
  （5 failed, 4 passed），证据留存于执行账本。

### L4 前端
- `tsc --noEmit -p tsconfig.json`：clean（含 G36 全部新文件）。
- tsconfig.test.json：仅未触碰文件既有错误（delivery-dialog.test 等）。
- vitest：chat.test.ts 30/30；message-reducer + use-workspace-session 40/40；
  hooks（sse bind/token-batcher）8/8；i18n 31/32（1 失败为 import-workbench/
  scale-portrayal-panel 裸 CJK —— 未触碰文件的既有违规）。
- **未运行**：真实浏览器（Playwright）端到端 —— 本机无浏览器依赖栈；前端
  验证止于类型/单元/组件层。如实登记，不声称 L4 全链通过。

### V 表覆盖映射（33 项）

| V | 层级 | 证据 |
|---|---|---|
| V01 | L3 | v01_stub_received_image + receipt + no_mutation |
| V02 | L3 | v02_* 4 断言（breaks/palette/revision/evidence） |
| V03 | L1+L2 | crop 像素级测试（右下角蓝色带）+ 越界拒绝 |
| V04 | L1 | viewport_hash 敏感性（平移/缩放→新身份；噪声稳定） |
| V05 | 设计 | resolver revision 属主复核 + 工具 stale 门（L1 stale 分支）；旅程内 revision 变化由 expected_revision CAS 保证 |
| V06 | L1+L2 | reference 不携 revision（不变量 + EXIF 用例断言 null） |
| V07 | 设计 | capture 只取 map canvas；label 如实「不含页面图例」 |
| V08 | L2 | layout_preview → 422 typed unsupported |
| V09 | L1 | resolver 逐图预算拒绝（turn_image_budget_exceeded 等） |
| V10 | L1+L2 | gate 门 1 拒绝 + typed reason；文本照常 |
| V11 | L1+L2 | 门 2（spawn 锚点）/门 3（get_state）拒绝 |
| V12 | L2 | 假 PNG/截断 JPEG → 400 decode_failed |
| V13 | L1+L2 | EXIF orientation 校正 + 派生无 EXIF |
| V14 | L1 | near_blank/transparent 观察标记（不拒绝） |
| V15 | L1+L2 | SVG/动画/多帧拒绝（animated_not_supported） |
| V16 | L1+L2 | 跨会话 ref → None/404 |
| V17 | L1 | 共享 blob 撤销 A 保 B |
| V18 | L1 | 同字节双版本双身份 |
| V19 | L1 | bound 不被 draft FIFO 淘汰 |
| V20 | L3 | v20_detail_attachments + v20_meta_bounded |
| V21 | 既有 | Last-Event-ID resume 只读重放（DUP-1 既有回归覆盖，附件在 resume 短路路径不重发） |
| V22 | 既有+设计 | 取消边界沿用 bridge turn lease；附件解析在 lease 内随取消收尾 |
| V23 | 设计 | intake 按会话归属 + 草稿 store 会话切换清空（前端 INV-2 同语义） |
| V24 | L3+设计 | 工具描述/回执明示图片文字非授权 |
| V25 | 设计 | 工具描述明示精确值走真实数据工具 |
| V26 | 既有 | dispatch/turn/owner 门天然继承（同一 registry 注入） |
| V27 | L1 | 非 result_images 键不投影；工具只收 vev- id |
| V28 | L1+L2 | 撤销/缺失 → 解析 None / 读取 404 |
| V29 | 设计 | 台账区分「曾经看过」与「本轮送过」（receipts + ledger） |
| V30 | 设计 | 修改与视觉复验分离（回执 status/reason 逐附件） |
| V31 | L2 | steer payload 携 images + 台账 |
| V32 | 部分 | pi_doctor 生产链可用；隔离包部署检查未执行（Docker 依赖），如实登记 |
| V33 | L1+L2+L3 | 台账复验→换代→不重送（三层各有断言） |

## 未测/已知限制（如实）
- L4 浏览器端到端未运行（本机无 Playwright 栈）。
- V32 生产包隔离环境（Docker）未执行；pi_doctor + 真实子进程旅程已覆盖
  依赖/入口/关闭路径（GIS_VISUAL_CONTEXT=0 typed disabled 有单测）。
- 真实视觉模型的识图准确率未测（无凭据）；stub 只证明协议/身份/结果链。
