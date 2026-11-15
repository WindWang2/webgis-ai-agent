# G36-PI-VISUAL-CONTEXT INTERFACES

## 新增模块（本域所有权）

```
app/services/visual_context/
  evidence.py    VisualEvidenceRecord / EvidenceRejected / SOURCE_TYPES /
                 mint_evidence_id / viewport_identity_hash
  refs.py        vevimg-/vevr- blob 引用计数（store_evidence_ref /
                 release_evidence_ref / read_evidence_bytes / blob_has_live_ref）
  store.py       register/resolve/revoke/bind/list/purge + INDEX_KEY
                 "_visual_context_evidence"（session_data）
  decoder.py     decode_image / derive_model_image / make_thumbnail
  resolver.py    resolve_attachments / AttachmentReceipt / SentImageLedger
                 （get_sent_image_ledger 单例）
  profile.py     model_catalog_entry / VisionGate.check / state_model_accepts_image
app/api/routes/visual_context.py   POST/GET/GET image/DELETE
  /chat/sessions/{sid}/visual-context/evidence[...]
app/tools/visual_context_tools.py  webgis_visual_evidence_read
frontend/lib/api/visual-context.ts uploadVisualEvidence / list / thumb / revoke
frontend/lib/visual-context/attach-store.ts   useVisualAttachStore
frontend/lib/visual-context/map-capture.ts    registerMapCapture / captureCurrentMap
frontend/components/sidebar/visual-attach.tsx VisualAttachBar（菜单+chips+框选模态）
```

## 修改的既有接缝（全部加性）

| 文件 | 变更 |
|---|---|
| app/schemas/chat_schema.py | ChatAttachmentRef；ChatRequest.attachments ≤4；SessionMessage.attachments |
| app/agent_pi_bridge.py | stream_prompt/prompt(attachments=)；_resolve_turn_images（V33 台账+能力门+解析）；_slim 剥 result_images |
| app/services/chat/pi_rpc_client.py | models.json 走 model_catalog_entry（vision 投影）；spawn_model_id / spawn_declared_image_input / spawn_epoch 锚点 |
| app/services/chat/pi_result_abi.py | abi_tool_response content 追加 result_images 窄投影 |
| app/services/tool_dispatch_service.py | ToolDispatchResult.result_images + _extract_result_images |
| app/api/routes/chat.py | _attachment_persist_meta；stream_prompt 携 attachments；user 消息持久化 attachments + bind；session detail 回 attachments |
| app/api/routes/pi_interactions.py | TurnAttachmentRef；steer/follow_up payload.images；台账 attachments 披露 |
| app/services/history_service_async.py | save_message(attachments=) → 返回消息 id |
| app/models/db_model.py + migrations g36_visual_context | messages.attachments JSON 列 |
| app/tools/__init__.py / app/main.py | 工具与路由注册 |
| frontend lib/api/chat.ts | streamChat/sendTurnMessage attachments 参数；attachment_receipt 事件类型 |
| frontend lib/hooks/useMapBridge.ts | send 第三参 attachments |
| frontend lib/hooks/use-sse-stream.ts | 草稿消费/乐观消息/回执回填/失败回滚 |
| frontend lib/hooks/use-workspace-session.ts | 恢复 attachments + 受权缩略图 |
| frontend components/sidebar/chat-tab.tsx、map/map-panel.tsx | 附图条挂载；地图采集注册 |
| frontend messages/{zh-CN,en-US}/sidebar.json | chat.visualAttach.* 22 键 |

## 线格式契约

- ChatRequest.attachments: `[{"evidence_id": "vev-[0-9a-f]{16}", "purpose"?: str}]` ≤4
- intake 响应（VisualEvidenceResponse）：evidence_id/source_type/mime/origin_sha256/
  derived_sha256/derived_size/width/height/mapspec_revision/fingerprint/viewport_hash/
  crop/label/state/observations —— 无任何字节
- SSE `attachment_receipt`：{session_id, turn_id, attachment:{evidence_id, source_type,
  status: sent|refused, reason, derived_sha256, width, height, mapspec_revision,
  viewport_hash, observations}}
- prompt RPC wire（上游 v1.5.2 契约）：{message, images?: [{type:"image", data: b64,
  mimeType}]}（sessionId 键 vendor 忽略，沿既有行为）
- 模型目录投影：vision=True → input:["text","image"] + inputLimits.images.resize
  {maxWidth:1568, maxHeight:1568, maxBytes:1572864, jpegQuality:82}

## 关键常量（单一语义源）

MAX_INTAKE_BYTES=8MiB；MAX_DECODE_PIXELS=30MP；MAX_DECODE_SIDE=8192；
MAX_DERIVED_SIDE=1568；MAX_DERIVED_BYTES=1.5MiB；MAX_ATTACHMENTS_PER_TURN=4；
MAX_TURN_BASE64_BYTES=4MiB；MAX_IMAGE_BASE64_BYTES=2MiB；drafts≤12/24h；
登记预算 60/h；工具结果 ≤1 图/2MiB b64；缩略图 ≤320px。

## 与 G37+ 的共享缝
- profile.model_catalog_entry 是 models.json 模型条目的唯一投影入口；G37 合流时
  保留其语义（vision 投影与模型选择正交）。
- ChatRequest.attachments 与 G37 的会话设置无耦合；attachments 不受模型切换
  影响（能力门按当前 spawn 锚点 + get_state 实时裁决）。

## 关闭/回退
- GIS_VISUAL_CONTEXT=0：工具 typed disabled（有单测）。
- 无描述符 vision=True：图片通道整体关闭，文本/既有行为零变化。
- 迁移 downgrade：drop messages.attachments（加性列，可安全回退）。
