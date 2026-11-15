# G36-PI-VISUAL-CONTEXT DEDUP_MATRIX

基线：1276692737（2026-11-14 复核）。判定依据全部追到生产调用链（file:line 为 base 行号）。

| 能力/需求 | 判定 | 依据（生产调用链） |
|---|---|---|
| Pi v1.5.2 pin / images RPC 契约 | **present（上游）** | gitlink=b4afdddf；rpc-types.ts:22-24 prompt/steer/follow_up images；rpc-mode.ts:409-440 透传；agent-session.ts:2018-2025 归一化入 user message |
| webgis 侧发送 ImageContent | **missing** | grep ImageContent app/ frontend/ = 0 命中；bridge data dict 无 images（agent_pi_bridge.py:2932-2934, 3105） |
| ChatRequest 图片/附件字段 | **missing** | chat_schema.py:104-170 仅 message/map_state/skill_name/project_id/canvas_actions；256KB 整闸 → 必须 ref 传 |
| 前端附图入口/发送 | **missing** | chat.ts:192-199 body 无附件；chat-tab.tsx:499-517 composer 仅文本 |
| 消息附件持久化/恢复 | **missing** | Message 模型（db_model.py:233-253）无附件列；get_session_detail 只回 id/role/content/timestamp（chat.py:1635-1643）；use-workspace-session.ts:256-261 只映射四字段 |
| 图片证据身份（evidence id/版本/viewport/crop） | **missing** | visual_observation/store.py 的 ScreenshotEntry 只有 sha/size/rev/fp（critic 专用，8 条 FIFO） |
| 受权读取/跨会话防护 | **partial** | resolve_visual_screenshot 有会话绑定护栏（store.py:214-249）+ require_owned_session 路由门 —— 但只覆盖 critic 截图，不覆盖聊天附件/参考图 |
| blob 存储/refcount/GC | **present（可复用）** | durable_blob_store.py + blob_refs.py（vref refcount、sweep、≤64 会话/blob） |
| 上传端点 | **partial** | visual_repairs.py:554-619 仅 critic PNG 截图（4MiB/PNG 魔数），无参考图（JPEG/EXIF）、无 map_region/layout_preview、无证据身份 |
| 参考图解码/EXIF/像素预算 | **missing** | 无生产路径（grep EXIF/PIL decode in app/api = 0；PIL 13.0 在 venv） |
| 视觉模型能力投影（models.json input） | **missing** | pi_rpc_client.py:~455 只写 {id,name,reasoning}；descriptors.py vision 字段存在但无消费到 models.json 的链路 |
| 图像工具结果（ToolResult ImageContent） | **missing** | pi_result_abi.py:271-298 content 恒 text；ToolDispatchResult 无图像字段；扩展 index.mjs:376-381 已可透传（上游就绪） |
| agent 读图工具 | **missing** | app/tools/ 无 visual_evidence 工具（tool 名查重无冲突） |
| 运行中附图（steer/follow_up images） | **missing** | pi_interactions.py:272 只发 {"message"}；TurnMessageRequest 无附件 |
| 删除/撤权后历史 pixels 防重送 | **missing** | 无 sent-images 台账；_isolate_vendor_session（bridge:1576-1592）仅按 GIS session 变化换代 |
| G29 交互/turn token/单 writer | **present（保留）** | pi_interactions.py:213-320 control gate + 身份复核 |
| G22 连续性/恢复目录 | **present（保留）** | #2093 已合并 |
| G33 thematic inspect/preview/apply | **present（复用）** | thematic_authoring_tools.py:106-248 |
| VLM critic / AUTO_SAFE | **present（复用）** | visual_observation/provider.py、auto_repair.py |
| 自动截图上传链 | **present（复用采集原语）** | visual-snapshot.ts:93-166 + captureMapCanvas（exporter.ts:88-100） |
| ChatEngine fallback | **已下线（不恢复）** | #2036 |

## 结论

G36 领域（聊天图片证据 → 原生 Pi ImageContent）在最新 master 上**整体 missing**，
无 owned-by-other 冲突；open PR=0。既有可复用件：blob store/refcount、owner 门、
截图采集原语、G29 steer 路由、G33 工具、视觉 critic（不动）。
不重造：critic 评分、AUTO_SAFE、模型选择器（G37）、会话恢复目录（G22）。
