# PR: feat(g12-pi-native-integration): Pi v1.5.1 原生工具发现、受控嵌套调用与运行时生命周期集成（ADR-0288）

## 来源（issue / 文档债 / 任务书）

- 目标来源：G12-PI-NATIVE-INTEGRATION 任务书（Pi v1.5.1 原生工具发现与运行时完整集成）；
  直接修复 [issue #2061](https://github.com/WindWang2/webgis-ai-agent/issues/2061)
  （Pi-only 下 /config/llm 更新静默无效）。前置：#1973（v1.5.0 升级/ADR-0282）、
  #2012（TurnPhase/幂等/ADR-0286）、#2036（Pi-only）均已合并，本 PR 只做增量。
- Baseline：`origin/master @ 816705218a5aa6202bf15d745cb31cddb3437f44`（2026-11-10
  执行时实测；与任务书快照无漂移）。
- Dedup：任务书已知清单全部复核——#1973/#2011–#2017/#2033–#2036 已合并（转回归保护）；
  开放 PR #2021/#2022/#2032 均不触 Pi core（无文件冲突面）；**无任何未合并分支在做
  vendor/pi 升级、tool_search/codemode 或 #2061 修复**（`git branch -r --no-merged` 全量
  核对）；G09 批量分析无 PR（不做计算编排）；G13–G16 无在途 PR（本 PR 以当前 master
  公开接口独立运行）。详见 `planning/G12-PI-NATIVE-INTEGRATION/DEDUP_MATRIX.md`。
- 版本决策：任务书标题锚定「已核 v1.5.1」；执行时复查发现 v1.5.2（发布晚于快照 5h、
  早于本任务启动 ~1.5h）。逐目录 diff 核实 **coding-agent 的 RPC/扩展/发现/嵌套/停机面
  在 v1.5.1↔v1.5.2 零差异**，v1.5.2 无本任务所需能力且无浸泡 → **pin v1.5.1**
  （dd30101cfc5b0609411dd80b6f93adbc14db14cf），v1.5.2 评估结论记入兼容矩阵（后续升级
  = pin-only 变更）。

## 根因与证据（Problem Evidence）

1. **长尾不可发现**：扩展把全部注册超集以默认 exposure 注册后 setActiveTools 收窄
   （`app/extensions/webgis-tools/index.mjs`）；上游 tool_search/codemode 内置扩展虽被
   `--no-builtin-tools` 保留（只清默认工具选择，vendor sdk.ts:282），但宿主从不激活 →
   模型只能靠 `list_available_tools` 两跳或 webgis_execute 代理，无法按需把真实工具
   schema 声明进下一请求。
2. **#2061**：`pi_rpc_client._spawn_process` 只读 `Settings.LLM_*` 写 models.json，
   而 `/config/llm` 写的是 `model_config` 运行时覆盖 → 更新返回成功但新 spawn 仍用旧
   配置（issue 正文定位 `execution_engine.py:570-586` + `pi_rpc_client.py:368-399`）。
3. **嵌套调用无投影**：全仓零处 `parentToolCallId`；mapper `_base_step_payload` 只有
   task/step/session → codemode 嵌套 GIS 调用会与顶层步骤混列、无法归属。
4. **停止/停机语义**：`PiBridge.abort` 只发 abort RPC（v1.5.1 提供 clear_queue——不清
   队列时排队消息 abort 后自行重启）；`stop()`=terminate→0.5s→kill，低于 v1.5.1 有序
   dispose 所需（tool settlement + credential writes 可数秒）。
5. **scope 校验默认 OFF**（`pi_surface_receipt.py` L44-73）：无发现合法路径时硬校验会
   误拒，只能 opt-in —— 发现闭环建立后此顾虑消除。

## 改动清单（Architecture & Files）

- **W01/W02 版本基线**：`vendor/pi` gitlink v1.5.0→v1.5.1（官方 tag 原样，零 vendor
  补丁）；`pi_compat.PI_MIN_VENDOR_TAG`→v1.5.1 + 过时 ChatEngine 回退文案改 503；
  `docs/PI_UPSTREAM_COMPAT_MATRIX.md` C11–C16 + 四栏差异裁决；`docs/adr/0288-*`。
- **W03 封套（dump v4）**：`pi_native_surface._native_annotations/_native_namespace/
  _pi_tool_definition`（前门=direct、长尾=deferred、annotations 由 side_effect **.value**
  派生且 UNCLASSIFIED 全缺席、namespace=domains 首域 gis.<slug> 有界）；`index.mjs`
  registerTool 透传（v2 dump 缺席字段零漂移）；pi_doctor surface 域披露
  direct=7/deferred=381。
- **W04/W05 发现闭环**：`index.mjs` 常驻激活集 += tool_search/codemode
  （`PI_NATIVE_DISCOVERY=0` 回退）；`pi_surface_receipt` 新增 `_DiscoveredStore`
  （≤16/turn、幂等、随 turn evict）+ `record_discovered_tool/discovered_tools`；
  `pi_bridge_surface_gate`：非 selected 直调走 grant（注册超集成员 ∧ echo 对拍
  **registry 现编译** spec），已 grant 工具后续 echo 也对拍现编译（撤权/schema 漂移后
  旧 echo 失效）；`PI_SURFACE_SCOPE_ENFORCE` 默认 ON（0/false/off/no 回退）；agent_pi_bridge
  gate 调用点传入 registry；grant/receipt 同步 evict。授权红线：发现只改声明，权限面
  ≡ 注册超集 + dispatch 闸序（等价 proxy 通道），绝无 LLM 自授权。
- **W06 嵌套投影**：`pi_event_mapper._base_step_payload` 加性 `parent_tool_call_id`
  （顶层缺席=旧契约逐字节兼容）；前端 `hud-types/message-reducer/use-sse-stream/
  tool-call-card` parentStepId 子行（step_start 分支只为带 parent 的事件建行，不与
  tool_call 双行；子行缩进复用既有 token，不触 #2032 主题/布局面）。
- **W07 停止/停机**：`PiBridge.abort` abort 前先 `clear_queue`（尽力而为不阻断）；
  `PiRpcClient.stop` 升序：stdin EOF（vendor 有序 shutdown）→ `PI_GRACEFUL_STOP_TIMEOUT_S`
  （默认 8s）→ 单次 SIGTERM → `PI_TERM_STOP_TIMEOUT_S`（2s）→ SIGKILL+收尸；等待全部
  offload 线程（不冻结事件循环）；绝不双发信号。
- **W08 配置生效（#2061）**：`pi_rpc_client._spawn_process` 只经
  `model_config.resolve_llm_config()`（Settings⊕运行行时覆盖单一解析点）并锚定
  `spawn_config_generation`（sha256[:16] 不可逆）；`model_config.config_generation()`；
  `PiBridge._recycle_worker` 共享回收骨架 + `config_generation_stale/
  recycle_if_config_stale`（turn 边界换代、在飞 turn 延迟不切半套）；
  `/config/llm` 更新后扫既有 worker 换代（不主动 spawn），响应加性披露
  expected_generation/pi_workers_recycled/pi_workers_deferred；生产禁动态改配置不变。
- **W09 compaction/恢复**：`index.mjs` 挂 `session_before_compact`/`session_compact_failed`
  （v1.5.1 实际存在的两个钩子；只记账，不注入自定义 summary）；context assembly 新增
  `GisStateRebuildProvider` + `GIS_STATE_REBUILD` 域（rank13、wrap fence、1600 cap；
  MapSpec/SessionPlan 权威现取：revision、图层含隐藏态（A11 约束）、待完成能力、证据
  refs；缺失不虚构、无 mapspec 即 skip）。
- **W10-W12**：doctor vendor 域 expected_config_generation；`PI_RUNTIME_CONTRACT.md` §12
  G12 运行时语义；J6b 旅程改发现优先；OpenAPI 快照再生成；planning/G12 账本全套。

## 验证命令与输出摘要（Test Evidence）

命令（worktree 内，venv=/home/kevin/projects/webgis/webgis-ai-agent/.venv；本地
redislite 于 127.0.0.1:16379 承载 writer-lock 域——CI nightly 由 compose Redis 承载）：

- `bash scripts/build-vendor-pi.sh` → exit 0（v1.5.1 根 lockfile → npm ci 分支；bundle+dist 双产物）
- `python scripts/pi_doctor.py` → 六域无 FAIL；surface: `388 tools; native 7/7; exposure direct=7 deferred=381`；rpc live PASS
- `python -m pytest tests/integration/test_pi_vendor_contract.py tests/integration/test_pi_schema_coercion_golden.py -m heavy` → **11+golden 全部通过**（新增 4 个真实契约：A2/A3 中文 $refs 发现、无匹配诚实、A4 codemode 嵌套 parentToolCallId、A14 EOF+SIGTERM 有序停机）
- `python -m pytest tests/integration/test_pi_model_config_generation.py -m heavy` → **2 passed**（A10 双 stub 真实请求证据：换代后真实 Pi 请求换到新端点/新模型/新 key 末4位；旧 provider 零新请求）
- `PI_JOURNEY_E2E=1 python -m pytest tests/integration/test_pi_stub_llm_journeys.py -m heavy` → **4/4 稳定通过**（J1-J7 + J6b 发现优先改法；全生产路径含真实 gate/dispatch/MapSpec）
- `python -m pytest tests/unit/{test_pi_surface_receipt,test_pi_abi_adversarial,test_pi_dynamic_surface,test_pi_tool_spec,test_pi_native_surface,test_pi_event_mapper,test_pi_extension_control_boundary,test_pi_extension_hardening,test_pi_extension_turn_token,test_pi_rpc_client,test_pi_cancellation_unified,test_pi_durable_cancel}.py tests/unit/context_assembly -q` → **236 passed**
- `python -m pytest tests/quality/test_api_compatibility.py` → 12 passed（快照已再生成）
- 前端：`npx tsc --noEmit` + `tsc -p tsconfig.test.json` 双绿；`npx vitest run lib/hooks lib/chat components/chat` → 45 files / 349 tests passed；`npx eslint <触碰文件> --max-warnings 0` → 0 警告
- 纪律：本地串行；`--no-cov`；不等线上 CI；未跑 3 万级全量 pytest。
- 关键 Oracle 两遍一致：journeys 车道 4 连跑全过（此前 J6b 抖动已在发现优先改法后消除）；
  contract/doctor 多轮复跑稳定。
- **存量抖动披露（诚实项）**：J6b（两段式计划资格→裸名执行）在 master@v1.5.0 干净
  基线（另一 worktree，同 fixture）10 跑 4 失败、同签名（`nearest_ok:False`，
  step_error "Tool not found"）—— turn-2 selector 未稳定激活 nearest_facility 的
  **存量行为**，非本 PR 引入（G12 分支同签名复现率相当）。本 PR 恢复 J6b 原始流程
  （不插 tool_search 掩盖），另增**确定性** J8 发现旅程（driver 3/3：中文
  tool_search → 声明面 merge → buffer_analysis 真实执行、零 step_error）承担发现
  闭环覆盖。J6b 的 `declared_nearest` 为证据字段（非硬门）。
- 评审后回归（第一轮 BLOCK 修复Commit）：unit 全 Pi 面 240 passed；vendor
  contract + schema golden 13 passed；config lane 2 passed；pi_doctor 六域无 FAIL；
  ruff 全绿；J8 driver 3/3。

基线对比（同 fixture 一次采足，v1.5.0@master代码 vs v1.5.1@本PR）：
注册超集成员**完全一致**（388 工具）；dump 字节 1,022,950→1,060,111（+3.6%，封套加性）；
native7 schema 12,986→13,815；spawn→ready 0.42-0.46s 持平；停机最坏时延 +8s（EOF 有序窗
—— 语义性变化换 vendor 有序清理，turn 内 abort 不变，已入 ADR/文档）。未测：峰值 RSS
精确值（如实未测，未写性能结论）。

## Review 意见与清偿（Independent Review）

- Reviewer：独立 general-purpose subagent（未参与实现；给定 base/head SHA + 完整 diff +
  用户场景，未预设通过）
- 第一轮裁决：**BLOCK — P1×4 + P2×4 + P3×1**（详见
  `planning/G12-PI-NATIVE-INTEGRATION/REVIEW.md`）
- 计数（第一轮）：P0 ×0 / P1 ×4 / P2 ×4 / P3 ×1 —— **全部清偿**：
  - `P1-1 grant 自授权 → 语义对齐 vendor（_getCallableTypes 源码核实）：deferred
    直调/沙箱嵌套天生合法，grant 只接受 deferred 非前门；恢复 tier-3/destructive/
    hidden/未注册名拒绝 + 计数器零执行测试`
  - `P1-2 换代不恢复 → chat.py turn 准入处补 recycle_if_config_stale（下一 turn
    边界完成换代）+ 断言`
  - `P1-3 继承 key 压过解析 key → 解析 key 覆盖 env + 继承 env 场景测试`
  - `P1-4 rebuild 读错形状 → canonical layer.layout.visibility + open_capabilities +
    PlanStep.evidence；测试改用真实 SessionPlan/MapSpec`
  - `P2-1 clear_queue TOCTOU → 发出前重校验在飞身份`；`P2-2 J6b 掩盖 → 恢复两段式 +
    新增确定性 J8 发现旅程（存量抖动经 master@v1.5.0 干净基线复现，如实披露）`；
    `P2-3 fresh-spec fail-open → 非空 echo fail-closed`；`P2-4 层截断披露`
  - `P3-1 compaction 钩子名/回退文档修正`
- 第二轮裁决：**BLOCK — N1（P1，scope 死存储致 SURFACE_SCOPE_VIOLATION 生产不可
  达）+ N2（P1，receipt 缺席被扫入 fresh-spec 复检的兼容回归）** → 均已修复
  （scope_mismatch 传播恢复 + 复检条件收窄为实际 grant 工具；旧 abi_route 测试
  按 ADR-0288 真实语义重写为三断言，6/6 通过）。P1-1..P1-4/P2-3/P2-4/P3-1 判
  FIXED；P2-1/P2-2/N4 为 PARTIAL/P3 留记（REVIEW.md 如实记录）。N3 字段契约
  快照已按门机制刷新（含本 PR 3 加性字段 + 2 存量漂移字段）。
- 测试口径修正（reviewer 指正）：第一轮「unit 240」未覆盖
  `test_pi_surface_abi_route.py`（该文件当时实际红——N1/N2 之证）。现按其口径
  实跑 `pytest tests/unit -k pi` → **1849 passed / 77 skipped / 1 xfailed**；
  唯一失败为本 worktree 环境差异（缺 frontend jiti；补链接后 19/19，master 基线
  同过）。
- 第三轮（终审）：见 REVIEW.md「第三轮复审记录」

## 风险与回滚（Compatibility & Rollback）

- 兼容性：全部加性——dump v4（v2/v3 解析方不受影响）、SSE `parent_tool_call_id`
  （缺席=旧行为）、OpenAPI 快照（additive 字段 + 基线既有漂移的再生成，非本 PR 引入）、
  API 响应 3 个 optional 字段。**行为变化（有意）**：`PI_SURFACE_SCOPE_ENFORCE` 默认 ON
  （发现 grant 提供合法路径；kill-switch 显式 0 回 G12 前）；abort 前先 clear_queue；
  停机改有序升序（最坏 +8s）。
- 回滚开关：`PI_NATIVE_DISCOVERY=0`（发现入口退场）、`PI_SURFACE_SCOPE_ENFORCE=0`
  （scope 放行）、`PI_GRACEFUL_STOP_TIMEOUT_S`/`PI_TERM_STOP_TIMEOUT_S`（停机窗）、
  整体回退 gitlink→v1.5.0 + rebuild（矩阵 §4）。
- 残余风险：v1.5.2 未采纳（评估见矩阵，pin-only 可升）；生产镜像构建本环境无 docker
  daemon 未跑（Dockerfile 未改动；pi-builder/bundle+jiti overlay 面未变，隔离包加载已由
  `test_deployed_bundle_loads_extension_without_builder_dependencies` 真实验证）。

## Out of Scope

- 计算编排/批量分析（G09 排重）；能力资格政策与 provider 生命周期（G13）；要素编辑
  事务/lineage（G14）；多画幅产品（G15）；标注求解（G16）；全局 UI 重设计（#2032）。
- ChatEngine 对话回退不恢复（#2036 裁决）；不自制 JS 引擎/第二注册中心/第二 agent loop。
