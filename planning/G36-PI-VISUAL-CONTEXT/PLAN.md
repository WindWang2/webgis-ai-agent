# G36-PI-VISUAL-CONTEXT PLAN

## 架构（新增 visual-context 领域目录 + 加性接线）

```
app/services/visual_context/
  __init__.py
  evidence.py       # VisualEvidenceRecord DTO + 生命周期（draft/bound/revoked）
  refs.py           # vev- blob / vevr- refcount（复用 BlobStore 协议，仿 blob_refs）
  store.py          # 证据注册表：session_data 索引 + blob 存取 + 并发原子更新
  decoder.py        # 参考图有界解码（PNG/JPEG、EXIF 方向、像素/字节预算、派生重编码）
  resolver.py       # turn 时刻：授权 ref → 有界 ImageContent[] + 送模台账 + 预算裁决
  profile.py        # 视觉能力投影：ModelDescriptor.vision → input/inputLimits + runtime 核对
app/api/routes/visual_context.py   # 受权 intake/list/thumbnail/delete/capture 端点
app/tools/visual_context_tools.py  # webgis_visual_evidence_read 只读工具
frontend/lib/api/visual-context.ts # 前端 API client
frontend/lib/visual-context/attachments.ts # 草稿附件状态（zustand slice 或局部 store）
```

接线（全部加性）：
- `chat_schema.py`：ChatRequest.attachments: list[ChatAttachmentRef] ≤4（evidence_id+用途），SessionMessage.attachments
- `chat.py` chat_stream：解析附件 → stream_prompt(attachments=...)；_persist_pi_transcript 存 Message.attachments
- `agent_pi_bridge.py` stream_prompt/prompt：lease 内 resolve → data["images"]；每 turn 校验送模台账（V33 撤权 → vendor session 换代或阻断）
- `pi_rpc_client.py`：models.json 写入时投影 vision input/inputLimits（profile helper）
- `pi_interactions.py`：TurnMessageRequest.attachments → steer/follow_up images（V31）
- `tool_dispatch_service.py`：ToolDispatchResult.result_images（有界）→ `pi_result_abi.abi_tool_response` content 追加 ImageContent
- `use-sse-stream.ts` handleSend：草稿附件随 send；乐观消息带附件 chips
- `chat-tab.tsx` composer：附件按钮（当前图/框选局部/上传参考图）+ chips
- `use-workspace-session.ts`：恢复附件元数据；get_session_detail 回 attachments
- Alembic 迁移：messages.attachments JSON NULL 列

## 关键语义决策

1. **ref-only 线格式**：ChatRequest 只携 evidence_id（≤4），256KB 门不动；bytes 只经 intake 端点（multipart）。
2. **证据身份**：`vev-<id>` 独立于 blob sha；blob `vevimg-<sha>` 内容寻址可共享；draft（未发送）有界清理（≤12/会话 + 24h TTL）；发送即 bound（Message 行引用），不被 critic FIFO 淘汰（独立于 `_visual_screenshots`）。
3. **来源类型**：map_viewport / map_region / reference_upload / layout_preview（v1 只实现前三；layout_preview 若组合页链路未实测则 typed unsupported）。
4. **地图证据绑定**：revision+fingerprint+viewport identity（center/zoom/bearing/pitch 的稳定 hash）+ DPR/crop 元数据；同 revision 不同 viewport = 不同证据（V04/V18）。
5. **送模预算**：单图派生 ≤1.5MiB base64、≤1568 长边（与投影 resize 一致）、每 turn ≤4 图、总 ≤4MiB base64；超限在 resolver 明确拒绝并逐图报告（V09）。
6. **能力门**：ModelDescriptor.vision is not True → 拒图（文本照常，V10）；spawn 后 get_state 核对 input 含 image（V11）；描述符代际漂移 → 重新核对或拒绝。
7. **V33**：resolver 维护 per-vendor-session sent ledger（sha→evidence/gen）；新 turn 前复验；失效 → `_isolate_vendor_session` 强制换代（new_session + 重绑上下文，GIS 状态权威在宿主）或阻断请求。
8. **工具结果图**：仅 webgis_visual_evidence_read 显式产出 result_images（≤1 图/次），abi 窄投影；details 64KB 瘦身不变。
9. **图片内容不可信**：decoder 工具描述/系统提示明确图片文字非授权；工具只读证据，不接受 URL/路径/data URI（V24/V27）。

## 工作包顺序（每包含测试）

1. WP-A：decoder + store + refs + intake 路由（V12/V13/V14/V15/V17/V18）
2. WP-B：profile helper + models.json 投影 + ChatRequest.attachments + resolver + bridge data["images"]（WP01 纵向链：真 Pi 子进程 stub LLM 收到 ImageContent）
3. WP-C：Message.attachments 迁移 + 持久化 + session detail 恢复 + steer images（V20/V21/V31）
4. WP-D：工具 result_images + webgis_visual_evidence_read（V26/V27/V28）
5. WP-E：前端 composer 附图 + 框选 + 上传 + chips + 恢复（V01/V03 入口）
6. WP-F：V33 撤权换代 + 并发/过期/审计（V04/V05/V16/V22/V23/V33）
7. WP-G：真实 Pi 全链旅程（LLM stub 收图→ thematic apply → MapSpec 修订）+ 生产包检查（V01/V02/V32）
8. 独立 review → 修复 → 复审 → PR

## 测试层级登记（TEST_MATRIX 汇总）

- L1 单测：decoder（坏图/EXIF/预算）、store（并发/去重/tombstone）、refs（共享/回收）、resolver（预算/授权/失效）、profile（unknown/false/漂移）
- L2 集成：intake 路由 owner 门；chat→bridge prompt images（FakeRpc 捕获）；撤权换代
- L3 真实 Pi：pi 子进程 + 扩展 + dispatch（stub LLM 解码 ImageContent 断言像素/SHA）
- L4 浏览器：composer 附图→消息→恢复（Playwright 1 worker，条件允许时）
