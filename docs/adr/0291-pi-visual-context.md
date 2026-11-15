# ADR-0291: Pi 多模态地图上下文与视觉证据（G36）

## 状态

已接受（2026-11-14，G36-PI-VISUAL-CONTEXT）

## 背景

当前 Pi-only 对话路径只传文本：`ChatRequest` 无图片字段，bridge 的
prompt RPC 载荷只有 `message`；用户无法把「当前地图视口 / 框选局部 /
自己上传的参考图」交给正在对话的 agent。既有视觉链路（自动截图 →
VLM critic → AUTO_SAFE 修复）是**旁路评估**，不进入当前对话与工具执行，
其 8 条 FIFO 索引也是短期证据，不能当聊天附件的持久机制。

上游 Pi v1.5.2（pin `b4afdddf`）已具备全部所需契约：`prompt/steer/
follow_up` 原生 `images?: ImageContent[]`、`ToolResultMessage.content`
支持图像、模型目录 `input`/`inputLimits.images.resize` 编码预算。本仓
侧从未消费这些契约（基线 grep `ImageContent` = 0 命中）。

## 决策

1. **ref-only 线格式**：`ChatRequest.attachments`（≤4 条 `vev-` ref）
   只携 evidence id；bytes 只经受权 multipart intake 进入内容寻址 blob
   store。聊天请求体 256KB 整闸不放宽。
2. **证据身份独立于字节**：`vev-<id>` 是证据身份（来源类型、MapSpec
   revision/fingerprint、viewport 指纹、DPR/crop 链、origin/derived 双
   sha）；blob 按 derived sha 内容寻址跨会话共享（refcount），同字节
   不同版本/视口 = 不同证据。索引存 `session_data`
   （`_visual_context_evidence`），与 critic 的 `_visual_screenshots`
   生命周期完全独立；draft 有界（12/会话 + 24h TTL），发送即绑定消息行
   （`messages.attachments` 列，alembic `g36_visual_context`）。
3. **turn 时刻解析（单一裁决点）**：bridge 在 turn lease 内、prompt
   字节前 `_resolve_turn_images`：V33 台账复验（同 vendor transcript 已
   送 pixels 是否全部仍获准；失效 → vendor session 换代，换代失败 →
   阻断图片）→ 能力门（描述符确证 → spawn 锚点 → vendor get_state
   三重一致）→ 逐附件归属/tombstone/sha/预算裁决；产出原生
   `data["images"]` + 逐附件 SSE `attachment_receipt`。
4. **能力投影不推断**：管理员描述符 `vision=True`（env/file/override）
   才在 spawn 的 `models.json` 写 `input:["text","image"]` +
   `inputLimits.images.resize`（1568/1.5MiB/q82，与派生图预算同源）；
   None/False → 省略 input（Pi 默认 text-only），图片通道关闭、文本
   照常。描述符换代晚于 spawn → 送图拒绝（typed），换代回收沿用既有
   `recycle_if_config_stale` 边界。
5. **工具读图（窄投影）**：`webgis_visual_evidence_read` 只读当前会话
   证据；`ToolDispatchResult.result_images` 是唯一图像上行键（dispatch
   窄提取、≤1 图、base64 ≤2MiB、mime 白名单），`abi_tool_response`
   投影进原生 content；details 64KB 瘦身不变、不重复携带字节。
6. **图片内容不可信**：图片是观察/参考；其中文字/二维码/指令不构成
   授权；地图变更读权威数据；工具与派生预算拒绝 URL/路径/data URI。

## 后果

- 正向：三条生产旅程（视口可读性问答 / 参考图驱动 G33 修改 / 撤权后
  纯文本续问）同链可用；视觉证据与既有 CAS/锁/验收纪律正交组合。
- 权衡：撤权触发 vendor session 换代会丢弃对话记忆（GIS 状态权威在
  宿主、由既有 G22/G29 恢复机制重建）——这是「不重送未获准 pixels」
  的代价，宁换代不重送。
- 未支持（明确 typed unsupported，不静默冒充）：`layout_preview`
  来源（组合版面链路未实测）、SVG/PDF/动画/多帧/URL 图、工具结果
  多图。
- 兼容：全部加性字段/列/事件；旧客户端与无附件 turn 零行为差。
