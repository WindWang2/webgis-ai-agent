# Typed Tool Surface V1 — Recon（Phase 0 勘察）

- 日期：2026-09-13
- 基线：`origin/master` @ `580b33e9`（Merge PR #1272 ads-v1），branch `harness/pi-typed-tool-surface-v1`，worktree `.worktrees/pi-typed-tool-surface`
- 方向：方向 4 —— Pi Typed Dynamic Tool Surface
- 结论先行：**任务书的多数波次（T1/T3/T5/T6 主体）已被 ADR-0103/0104/0119 系列在 master 落地**。本任务不做第二套动态面；开发量转移到三个真实缺口：
  1. **T2 缺口**：per-turn 激活面（`compute_turn_active_tools`）只有名字预算（k_max=30），没有 schema 字节预算，selection reasons/dropped 在 Pi 路径被丢弃（不进披露面）；
  2. **T4/T7 缺口**：参数 schema 校验只存在于 registry 执行管线深处（dedup/wave 排队/ref 解析之后），validation error 以 tool 业务失败形状返回（`status=error` + `tool_failed` 事件），Pi 边界没有 pre-dispatch strict gate 与机器可读 typed error；
  3. **T9 缺口**：invalid tool-name rate / argument validation failure rate / proxy fallback rate / per-turn surface schema bytes 没有 metrics 面与回归门。

## 1. 执行时基线事实

| 项 | 事实 |
| --- | --- |
| origin/master SHA | `580b33e923e992cd6706659d455dfd72ef55d033` |
| open PR | #1270 `fix/ci-adaptive-hygiene`（CI hygiene + mapspec CLI alias） |
| 最近 merged | #1272（ads-v1）、#1271（AC-V11）、#1269、#1266-#1268、#1258-#1263 |
| 本任务分支 | `harness/pi-typed-tool-surface-v1`（自 origin/master 新建） |
| #1270 文件面 | `app/services/mapspec/coordinator.py`、`frontend/lib/mapspec-compiler/compiler.ts`、`docs/quality/*`、`tests/conftest.py`、`tests/quality/structural_baselines.json` —— 与本任务无交集；不吞入 |

## 2. 生产调用链（before）

```
┌─ spawn（进程启动一次）───────────────────────────────────────────────┐
│ app/main.py lifespan                                                 │
│   → ToolRegistry() + init_tools()        （327 工具，真实注册表）     │
│   → set_tool_registry(registry)                                      │
│   → PiRpcClient.start()                                              │
│       → pi_native_surface.dump_surface_file(.pi/agent/native-tools.json)
│           pi_surface_for_spawn:                                      │
│             tools = native7(完整 schema) ∪ registered_surface_names  │
│                    （model_visible ∧ ¬tier3 ∧ ¬external_unavailable， │
│                      实测 316 个，dormant 标记，~191KB 原子写）       │
│             default_active = 冻结 native7                            │
│       → node rpc-entry.js --extension app/extensions/webgis-tools/index.mjs
│           扩展：registerTool(全部 316) + registerTool(webgis_execute)│
│                 setActiveTools(default_active)（MAX_ACTIVE=48 天花板）│
└──────────────────────────────────────────────────────────────────────┘
┌─ per turn（每个用户回合）────────────────────────────────────────────┐
│ PiBridge.prompt / stream_prompt                                      │
│   → pi_turn_context.bind_turn_prompt                                 │
│       → _compile_surface（SessionPlan→ToolSurface 阶段披露行）        │
│       → _active_tools_block_for                                      │
│           → pi_native_surface.compute_turn_active_tools              │
│               V3 DynamicToolSurface.select(ToolSelectionContext)     │
│               （core 前门 + capability 反查 + 词法/语义检索 +         │
│                 contract 过滤 + k_max=30 + V6 置信度弃权）            │
│               → 名单恒含 native7，末道 tier 双检                      │
│       → attach "[WEBGIS_ACTIVE_TOOLS:[...]]"（用户同形 marker 先中和）│
│   → Pi before_agent_start：解析 marker → setActiveTools（下回合生效） │
└──────────────────────────────────────────────────────────────────────┘
┌─ tool call 执行（所有工具同管线）────────────────────────────────────┐
│ 模型 → Pi 工具（native 裸名 / 注册面裸名 / webgis_execute 代理）      │
│   → index.mjs postToBridge → POST /pi-tools/execute                  │
│       （X-Pi-Bridge-Secret + 签名 turn token + is_active 校验 409）   │
│   → agent_pi_bridge.dispatch_tool                                    │
│       → resolve_pi_tool_call（native|execute|reject 分类；            │
│           registered_surface=registered_surface_names∪native7；      │
│           proxy 内包 native 名 → wrap-reject）                        │
│       → registry.list_tools 存在性 → tier≥3 拒绝（SEC-01/02）         │
│       → ToolDispatchService.dispatch                                 │
│           dedup（(tool,args) 占位/完成态）→ analysis reuse →          │
│           wave semaphore（heavy 2 槽）+ session gate →                │
│           ToolRegistry.dispatch → _dispatch_impl：                    │
│             别名折叠 → planned 拒 → tier3_confirmed 闸 →              │
│             normalize_tool_arguments（声明式归一化+修复证据）→         │
│             透明 ref 解引用（skip 游标键）→                           │
│             Pydantic 校验（oversized Any-载体旁路 #699/#1113）→        │
│             unknown-field 拒绝（#828）→ JSON-string list 宽容解码 →    │
│             NaN/Infinity 扫描 → GeoJSON 结构校验（预算化）→            │
│             执行 → std_error_response 失败形状                        │
│       → 结果：ref 落存（geojson_ref）+ slim_event + map_actions +      │
│         harness_failure 分类（V5 typed diagnose）+ tool_metrics 行    │
│   → PiToolResponse（content/details/isError）→ 扩展回传 Pi            │
└──────────────────────────────────────────────────────────────────────┘
```

## 3. 与最近 PR / 既有方向的重叠矩阵

| 本任务波次 | master 既有实现 | 重叠度 | 处置 |
| --- | --- | --- | --- |
| T0 Pi boundary facts | `index.mjs`（registerTool/setActiveTools/before_agent_start 实装）、`pi_rpc_client.py` spawn、`specs/pi-as-agent-host.md` | 已实现 | 只补 probe 量化，不新做 |
| T1 Unified ToolSchema source | `pi_native_surface._pi_parameters` + `registry.get_schemas_subset`（live registry 单一真相；$defs 保留、session_id 剥离、additionalProperties:false） | 已实现 | 不动 |
| T2 Turn Tool Surface Compiler | `tool_surface_v3.select/project`（reasons/dropped/byte_budget/fingerprint/confidence/abstain） | **部分**：Pi per-turn 只用 select() 名单；byte_budget 与 reasons/dropped 未接 | **本任务主战场之一** |
| T3 Native/facade tools | native7 + 316 注册超集 + webgis_execute fallback（bounded：MAX_ACTIVE=48，k_max=30） | 已实现 | 不动 |
| T4 Strict input validation | registry `_dispatch_impl` 全套（归一化→Pydantic→unknown-field→NaN→GeoJSON） | **位置错**：发生在执行管线深处，Pi 边界无 pre-dispatch gate | **本任务主战场之二** |
| T5 Security/side-effect gating | spawn 三重过滤 + selection contract 过滤 + 末道双检 + dispatch tier3 闸 + bridge secret + turn token（HMAC 15min + active-turn 409） | 已实现 | 只做等价性测试钉住 |
| T6 Dynamic refresh | per-turn setActiveTools（vendor「下回合生效」语义=不在执行中变更）；PI_DYNAMIC_TOOL_SURFACE kill-switch；marker 中和 | 已实现 | 不动 |
| T7 Result contracts | geojson_ref/slim_event/map_actions/harness_failure/background_job_ids/cancelled 结构化 | **部分**：schema validation error 伪装成业务失败（status=error + tool_failed 事件），无独立 retryability 语义 | 随 T4 一起收口 |
| T8 Pi/legacy parity | 结构性同管线（同一 dispatch_tool→service→registry） | **缺测试钉**：校验/闸等价无显式 parity 断言 | **本任务交付** |
| T9 Metrics/benchmark | tool_metrics JSONL+aggregator（arg/result bytes、duration、error、plan 关联、mapspec revision）；Stage.TOOL_SURFACE 链发射（dynamic_count/confidence） | **缺**：surface 级指标（invalid-name/validation-fail/proxy-fallback/active-bytes）无指标面与回归门 | **本任务交付** |

最近 merged PR（#1271 AC-V11 制图线、#1272 ads-v1 数据供给线、#1266-#1269）全部在 cartography/data 域，与 Pi tool surface 零文件交集。历史最近相关线：#1150（ADR-0103 本体）、#1175（ADR-0119 V6 弃权）、#1189（V7）。

## 4. 关键既有事实（T0 probe 锁定，非文档猜测）

Pi extension API（实装于 `app/extensions/webgis-tools/index.mjs`，vendor/pi 本地构建、仓内 gitignored）：
- `pi.registerTool({name,label,description,promptSnippet,parameters,execute})` —— parameters 为 JSON Schema 形状（typebox 兼容），从 dump 原样传入；
- `pi.setActiveTools(names)` —— vendor 语义「下个 agent turn 生效」；不可用时 loud-degrade（全超集保持激活）；
- `pi.on("before_agent_start", event => ({systemPrompt}))` —— per-turn 面 + 身份注入点；
- dump 格式 v2 `{version,tools,default_active,execute_proxy}`；扩展对 dump 外名字过滤（`nativeNames.has`），MAX_ACTIVE=48 硬天花板。

真实规模实测（本机 registry 构建，`init_tools` 全量）：
- registry 327 工具；注册超集 316；dump ~191KB；
- schema bytes：max 6527（webgis_component_update）/ median 528 / mean 604；
- native7 恒激活面 = 17,277 bytes；k_max=30 动态面理论上限 ~35KB/turn，无字节预算。

Pi 侧不做 args 校验（扩展只透传 params）；schema 面上 `additionalProperties:false` 只是投影形状，权威校验在 Python。

## 5. 防重复施工清单

**复用（不新写）**：ToolRegistry schema 投影、V3 selector、归一化声明表、tier3_confirmed 闸、turn token、dump v2、V6 弃权、tool_metrics。
**扩展**：`compute_turn_active_tools`（字节预算 + 披露面）、`_dispatch_tool_bound`（pre-dispatch 校验闸）、registry `args_model()` 公开访问器（additive）。
**不做**：第二套 schema 源、native 面扩到全量 327、改 ToolDispatchService 核心语义、删 proxy、把 #1270 的 CI 修复吞进本线。

## 6. 风险与兼容面

- `index.mjs` 与 `index.ts` 死副本纪律（#694）：改 `.ts` 必须同步编译/同步 `.mjs`——本任务如需改扩展必须双写同步（预期不改扩展：marker 协议与 MAX_ACTIVE 已够用）。
- pre-dispatch 校验必须与 registry 语义零漂移：复用同一 Pydantic model + 同一归一化函数 + 同一 oversized 旁路判据，禁自造第二套校验规则。
- 字节预算只能影响「本轮激活谁」，绝不能改 spawn dump 内容（dormant schema 压缩已被明确禁止——见 pi_native_surface 注释纪律）。
