# G36-PI-VISUAL-CONTEXT BASELINE

## 执行环境

- 主仓库：`/home/kevin/projects/webgis/webgis-ai-agent`（主工作区，HEAD=816705218，仅读）
- 本任务 worktree：`/home/kevin/projects/webgis/webgis-worktrees/g36-pi-visual-context-20261114T151654Z`
- 分支：`zcode/g36-pi-visual-context-20261114T151654Z`
- Base SHA：`1276692737a8cf58d0604fb40ae0e70fc61779a2`（= 研究快照 master；fetch 于 2026-11-14T15:16Z，与快照一致，无新提交）
- 默认分支：master；open PR = 0（gh pr list 复核）
- vendor/pi gitlink @ base：`b4afdddf44e059c67ccf5097e5317d913521b470`（v1.5.2，与任务 pin 一致）
  - 注意：主工作区 vendor/pi 工作副本为旧 checkout（dd6bea41e，旧 master）——契约读取一律用
    `git show b4afdddf44:...` 或本 worktree 的子模块。
- #2094–#2116 GitHub reviews / inline comments / conversation comments 复核：PR 2097/2093/2116 抽查均为 0；与快照「23 PR × 3 通道为空」一致。独立评审在 planning/G2x-*/REVIEW.md。

## 上游 Pi 契约（pin b4afdddf，已复核源码）

- `packages/coding-agent/src/modes/rpc/rpc-types.ts:20-24`：
  `prompt|steer|follow_up` 均接受 `images?: ImageContent[]`；`abort` 无 images。
- `packages/coding-agent/docs/message-types.md:25-37,161-177`：
  `ImageContent = {type:"image", data: base64, mimeType}`；
  `ToolResultMessage.content: (TextContent | ImageContent)[]`，details 不是图像通道。
- `packages/coding-agent/docs/models.md:81-102` + `models-configuration.md:27-39`：
  自定义模型省略 `input` 默认 text-only；`inputLimits.images.resize`（maxWidth/maxHeight/
  maxBytes/jpegQuality，默认 2000×2000 / 4.5MiB / q80）控制新图编码；历史图不随模型切换重写；
  `images.maxPerMessage/maxPerRequest` 目录可声明但 Pi 不自动拒绝历史。
- get_state / set_model / get_available_models RPC 存在（C9 已消费 set_model）。

## 本仓接缝（base SHA 行号）

- `app/schemas/chat_schema.py:121-169` ChatRequest：message(≤5000)/map_state/skill_name/
  project_id/canvas_actions；整请求 256KiB 门（model_dump_json）。无图片字段。
- `app/agent_pi_bridge.py:2886` `stream_prompt(message, session_id, cartography_context,
  on_turn_result, env_block, *, context_org_id/…)` → 内部 `data` dict + `bind_turn_prompt` →
  `self._rpc.request("prompt", data)`（~3092-3104）。turn 令牌/租约/单 writer 纪律在此。
- `app/agent_pi_bridge.py:2348` 非流式 `prompt()` 同构。
- `app/agent_pi_bridge.py:187` `PiToolResponse{toolCallId, content: list[dict], details, isError}`。
- `app/services/chat/pi_result_abi.py:271-298` `abi_tool_response` 统一 content=[{type:"text"}]。
- `app/extensions/webgis-tools/index.mjs:376-381` 扩展原样透传 `result.content`（ImageContent 可过）。
- `app/services/chat/pi_turn_context.py:232` `bind_turn_prompt` → typed context assembly（scoped
  ContextItem 流 + receipt）。
- `app/services/chat/pi_rpc_client.py:~455` spawn 时写 `models.json`：自定义模型只写
  `{id, name, reasoning:true}` —— 无 `input` → Pi 视为 text-only。G36 helper 需投影
  `input:["text","image"]` + `inputLimits.images.resize`。
- `app/services/chat/model_runtime/descriptors.py` `ModelDescriptor.vision: Optional[bool]`（None=未知）；
  来源 defaults/env(MODEL_DESCRIPTOR_OVERRIDES)/file(MODEL_DESCRIPTORS_FILE)/upsert_override。
  默认描述符 vision=None → 默认不开放图片。
- `app/services/gis_harness/visual_observation/store.py`：截图 FIFO 索引（MAX 8/会话，按 SHA 去重）、
  revision+fingerprint 硬门（C13 旧观测不驱动新地图）、`resolve_visual_screenshot` 会话绑定护栏。
  → 这是 critic 的短期索引，不是聊天附件的持久机制。
- `app/services/gis_harness/visual_observation/blob_refs.py`：vref refcount（≤64 会话/blob）+
  vshot blob + GC sweep。可直接复用为 blob 生命周期原语。
- `app/services/durable_blob_store.py`：内容寻址 blob store（sha256 校验读取）。
- `app/api/routes/visual_repairs.py:554-619` `POST /sessions/{id}/visual-snapshots`：4MiB、
  PNG 魔数+IHDR 初筛、owner 依赖 `require_owned_session`、reverify 预算。仅 PNG、≤4096²。
- `app/tools/thematic_authoring_tools.py` G33 inspect/preview/apply（CAS/expected_revision）。
- ChatEngine fallback 已下线（#2036）；bridge 不可用 = unavailable/503 诚实语义。

## 与 G36 相关的既有实现（去重）

- 自动截图上传链（前端 visual-snapshot.ts）已存在：节流 + revision echo + 8 条 FIFO。
- VLM critic（rules_only/vlm/hybrid）与 AUTO_SAFE 修复已存在（provider.py/auto_repair.py）。
- G29 steer/follow_up 原生运行中交互已存在（#2097）。
- G22 Pi v1.5.2 对齐 + 长会话连续性已存在（#2093）。
- 无任何 ImageContent / prompt images / 聊天附件 / 图像 evidence DTO 实现（grep 无命中——见 DEDUP_MATRIX）。

## 决策记录

- D1: 不升级 vendor pin（v1.5.2 已含 images 契约）。
- D2: 证据身份/引用放新领域目录 `app/services/visual_context/` + 路由挂 visual-context 专属 router；
  blob 复用 durable_blob_store + blob_refs 式 refcount（引用键独立于 vshot 语义）。
- D3: chat 接线走加性字段 `attachments`（typed refs），prompt images 在 bridge 层由授权 resolver
  解析为原生 ImageContent[]。
- D4: 模型 vision 能力经 ModelDescriptor.vision（env/file/override 三来源，管理员维护）+ models.json
  input 投影 helper；unknown/None → 阻止图片、文本正常。
