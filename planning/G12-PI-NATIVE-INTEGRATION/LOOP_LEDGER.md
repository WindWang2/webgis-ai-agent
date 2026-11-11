# LOOP_LEDGER — G12-PI-NATIVE-INTEGRATION

格式：`第N轮 | 改动 | 结果 | 通过否 | 下一步`

第0轮 | 研究与基线：3 研究agent + 主agent亲读 seam + vendor 关键事实亲核
（tool_search merge、parentToolCallId 事件、lockfile/jiti/system prompt、
--no-builtin-tools 语义）。版本决策 **v1.5.1**（v1.5.2 已评估：coding-agent
集成面零 diff、无所需能力、无浸泡 → 不升，记入 matrix）。| 研究完成 | 通过 |
Wave 1。

第1轮 | W01+W02：gitlink→v1.5.1（dd30101c，官方 tag 原样）；build-vendor
（npm ci，bundle+dist exit 0）；PI_MIN_VENDOR_TAG→v1.5.1；compat matrix
C11–C16 + 四栏差异裁决；ADR-0288；pi_compat 过时 ChatEngine 文案→503。
| pi_doctor 六域无 FAIL（rpc live 词表与 v1.5.0 一致）；vendor contract +
schema golden **9/9** | 通过 | commit 5f6f4a0 (Wave 1)。

第2轮 | W03：dump v4 原生封套（exposure direct/deferred、namespace gis.<域>、
annotations side_effect 派生且 UNCLASSIFIED 全缺席）；扩展透传；doctor 披露
direct=7/deferred=381。**修复 str-Enum .value 陷阱**（否则 annotations 全静默
为空——单测钉住）。| surface 单测 81 passed；extension 20 passed | 通过 |
commit bbc3b71 (Wave 2)。

第3轮 | W04+W05：tool_search/codemode 常驻（PI_NATIVE_DISCOVERY kill-switch）；
discovery grant（≤16/turn 幂等，echo 对拍**现编译** spec —— 撤权后旧 echo
失效）；PI_SURFACE_SCOPE_ENFORCE 默认 ON（0 回退）。真实契约车道：A2/A3 中文
query 经 $defs 命中 deferred 工具、请求2 merge 追加声明、无匹配诚实缺失、
A4 codemode 嵌套 parentToolCallId + id=<parent>/<n> + 触达仅注册工具。
**修复 harness stub 把 /healthz 计为模型请求的索引漂移**。| contract 10 passed
（3 新）；surface/adversarial 102 passed | 通过 | commit 1998a69 (Wave 3)。

第4轮 | W06：mapper parent_tool_call_id 加性投影（顶层缺席零漂移）；前端
parentStepId 子行（step_start 分支 + ToolCallChain 缩进，不触 #2032 面）。
| mapper 19 passed；前端 258 tests + tsc 双 tsconfig + eslint 全绿 | 通过 |
commit c7e41a4 (Wave 4)。

第5轮 | W07：abort 前 clear_queue（Esc 语义）；stop 升序（stdin EOF→8s 窗→
单次 SIGTERM→2s→SIGKILL，等待 offload 线程）；真实 rpc-entry 停机契约
（纯 EOF 有序自退 <8s exit 0；EOF+SIGTERM 相邻 → 0/143 不需 SIGKILL）。
| contract 11 passed；cancellation/lifecycle 80+15 passed | 通过 |
commit 37c5cad (Wave 5)。

第6轮 | W08（#2061）：spawn 只经 resolve_llm_config()（单一解析点）+
spawn_config_generation 锚定；/config/llm 代漂移换代（在飞 turn 延迟）+
expected/recycled/deferred 披露；共享回收骨架重构。**A10 双 stub 真实请求
证据**（换代后真实请求换端点/模型/key 末4位，旧 provider 零新请求）。
OpenAPI 快照再生成（含基线既有漂移，如实记录）。| config lane 2 passed；
api compat 12 passed | 通过 | commit 2b41dcc (Wave 6)。

第7轮 | W09：扩展 compaction 钩子只记账（before/failed——after 不存在于
v1.5.1，不编造）；GisStateRebuildProvider（GIS_STATE_REBUILD rank13，wrap
fence 1600 cap；MapSpec/SessionPlan 权威现取，隐藏层=A11 约束保留，缺失不
虚构）。| context_assembly 78 passed | 通过 | commit e6ff968 (Wave 7)。

第8轮 | W10-W12：J6b 改发现优先（修复 deferred 语义下 'Tool not found'
抖动 —— **4/4 稳定**，全生产发现闭环）；doctor expected_config_generation；
PI_RUNTIME_CONTRACT §12；基线对比（v1.5.0 vs v1.5.1：注册面成员一致 388、
dump +3.6% 加性字节、spawn→ready 持平 0.42-0.46s、停机最坏 +8s=语义性）。
**环境修复**：Redis 不可用导致旅程全红（writer admission fail-closed）→
redislite 16379（真实协议）。| journeys 4/4；chat 组件 91；全 Pi 单测 236 |
通过 | commit c2ff31d (Wave 8)。

第9轮 | 独立评审（BLOCK: P1×4+P2×4+P3×1）→ 全部修复并提交：
P1-1 grant 语义对齐 vendor（deferred 天生合法；只接受 deferred 非前门；恢复
tier3/destructive/hidden/未注册名拒绝+零执行）；P1-2 config 换代在 chat.py turn
准入恢复；P1-3 解析 key 覆盖继承 env；P1-4 rebuild 读 canonical
layout.visibility + open_capabilities + PlanStep.evidence（测试改真实形状）；
P2-1 clear_queue 前重校验；P2-2 J6b 恢复两段式 + 新增确定性 J8 发现旅程
（driver 3/3）+ 存量抖动经 master@v1.5.0 干净基线 10 跑 4 失败同签名复现（如实
披露，非本 PR 引入）；P2-3 fresh-spec 非空 echo fail-closed；P2-4 层截断披露；
P3-1 钩子名/回退文档。| unit 240 passed；contract+golden 13 passed；config 2
passed；doctor 无 FAIL；ruff 全绿；J8 driver 3/3 | 通过 | 复审 → PR。
