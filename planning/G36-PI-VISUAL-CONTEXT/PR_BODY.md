# G36-PI-VISUAL-CONTEXT PR_BODY

## 用户问题

当前 Pi 会话只能看到文字——用户指着地图问「哪些线不易辨认」「按这张参考图配色调整」时，agent 收不到任何图像，只能靠文字描述猜。既有视觉链路（自动截图 → VLM critic）是旁路评估，不进入当前对话与工具执行。

## 行为增量（G36 / ADR-0291）

1. **三类视觉证据进入当前对话**：composer 附图条支持「当前地图视口（渲染稳定后采集画布，不含 DOM 图例）」「框选局部（截图上拖选，CSS→设备像素链显式）」「上传参考图（PNG/JPEG，真实解码+EXIF 方向校正+元数据剥离）」；出版版面 `layout_preview` 在组合页链路验证前返回 typed unsupported（视口不冒充纸面）。
2. **原生 Pi 图像通道**：`ChatRequest.attachments`（ref-only ≤4，请求体 256KB 门不动）→ bridge turn lease 内解析为上游 v1.5.2 原生 `prompt`/`steer`/`follow_up` `images`；逐附件 `attachment_receipt` SSE（sent/refused + reason，绝不静默丢图）。
3. **证据身份与授权**：`vev-` 证据身份（来源/MapSpec revision+fingerprint/viewport 指纹/DPR/crop 链/origin+derived 双 SHA）独立于内容寻址 blob（vevimg-/vevr- 引用计数，与 critic 的 vshot- 完全独立）；每端点 `require_owned_session`；跨会话猜 ref 不可读；同字节跨会话共享、单侧撤销不影响他方。
4. **能力门（WP08）**：管理员 `ModelDescriptor.vision=true` 才在 spawn 的 models.json 投影 `input:["text","image"]`+`inputLimits.images.resize`；描述符换代早于 spawn（旧 worker）或 vendor get_state 不含 image → 拒图并给 typed reason，文本会话照常。
5. **agent 读图工具**：`webgis_visual_evidence_read`（只读、仅本会话证据、结构化 stale/needs 状态）经 `ToolDispatchResult.result_images` 窄投影返回 ≤1 张原生 ImageContent；details 64KB 瘦身不变。
6. **撤权防重送（V33）**：SentImageLedger 按 (session, vendor generation) 记录已送 pixels；任一失效 → 下一 turn 前强制 vendor `new_session` 换代（transcript 重建；GIS 状态权威在宿主），换代失败阻断图片——宁换代不重送。
7. **持久化与恢复**：`messages.attachments`（JSON 列，迁移 `g36_visual_context`）存有界元数据；会话详情回读附件；前端恢复渲染受权缩略图 + 来源/版本 chips。
8. **运行中附图（V31）**：G29 steer/follow_up 路由接受附件，同一 bridge 裁决面，原生 images 通道。

## 复用与范围

- 复用：durable_blob_store/BlobStore 协议、require_owned_session、G29 控制门与 turn token、G33 thematic inspect/preview/apply（V02 旅程真实走通）、G22 换代边界语义、captureMapCanvas/waitForRenderSettle。
- 不做（非目标）：视觉评估/评分（critic 既有域）、模型选择器（G37）、SVG/PDF/动画/URL 图、图片生成、OCR 执行器、组合版面附图（typed unsupported）。
- Pi pin 不变（v1.5.2 b4afdddf）；无 vendor 变更。

## 实际验证（不等待线上 CI；详见 planning/G36-PI-VISUAL-CONTEXT/TEST_MATRIX.md）

- **L1 单元 28✓**：解码（EXIF/炸弹/动画/近空白/crop 像素级）、身份不变量（V04/V06/V18）、store 生命周期（V17 共享/V19 bound 不淘汰/V28 tombstone）、resolver 预算（V09/V16）、三重能力门（V10/V11）、V33 台账、dispatch 窄提取（V27）。
- **L2 集成 9✓**：intake 路由全拒绝面（V08/V12/V15）、撤销幂等、线格式、bridge 解析/换代、steer images（V31）。
- **L3 真实 Pi 旅程 1✓（20 断言）**：stub LLM 收到 OpenAI vision 线格式（base64 长度与 intake derived_size 精确一致）→ 参考图驱动 `webgis_thematic_style_apply` 真实 CAS（breaks 保留/palette 改变/revision+1/decided_by=agent）→ 撤销后纯文本 turn 不重送旧 pixels → 会话详情含附件元数据（无 base64）。
- **回归**：schema/bridge/ABI/visual_repairs 等 168 项通过；`test_turn_settlement_seam` 5 例为**既有失败**（干净 base 127669273 同条件复现，与本分支无关）。
- **前端**：`tsc` 主配置 clean；vitest chat/reducer/session/hooks 78✓；eslint 本域文件 0 警告；i18n 双语 22 键齐（1 个 i18n 既有失败在未触碰文件）。
- **pi_doctor**：vendor/build/extension/surface/rpc/env 6 域 PASS（466 工具含新读图工具）。

## Review / Fix / Re-review

- 独立 reviewer（未参与实现）全量 diff 评审：4 P1 + 10 P2 + 9 P3（两个
  缺陷实证复现）；架构/契约/owner/预算/迁移面判定 clean。记录见
  `planning/G36-PI-VISUAL-CONTEXT/REVIEW.md`。
- 修复 commit 399f94238：P1×4 全修（工具能力门访问面 / V33 每 turn 预检 /
  SSE base64 泄漏 / CAS 重试双减）+ P2×8 + P3×5；新增修复回归 5 例；
  全量复测通过（core 33 / wiring 9 / 真实 Pi 旅程 20 断言 / 回归 113 /
  ruff/eslint/tsc 全清）。
- 原 reviewer 复审结论（终版见 REVIEW.md 附录）：无阻塞发现，可合并。

## 迁移 / 回退

- 加性列 `messages.attachments`（nullable；downgrade 直接 drop）；无数据回填；旧客户端零影响（attachments 缺省）。
- 关闭路径：`GIS_VISUAL_CONTEXT=0` 工具 typed disabled；无 vision 描述符时图片通道整体关闭、文本行为不变。

## 未测项与限制（如实）

- L4 真实浏览器（Playwright）端到端未运行（本机无浏览器测试栈）；前端验证止于类型/单元层。
- 生产 Docker 隔离包检查未执行（V32 部分）；pi_doctor + 真实子进程旅程已覆盖依赖/入口/关闭路径。
- 真实视觉服务识图准确率未测（无凭据）；stub 证明协议/身份/结果链，不证明模型理解质量。
