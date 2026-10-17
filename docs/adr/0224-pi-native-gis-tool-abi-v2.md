# ADR-0224: Pi Native GIS Tool ABI V2 —— Turn-scoped Qualified Tool Surface

- 状态：Accepted
- 日期：2026-10-18
- 方向：Goal 04 —— Pi ↔ Python GIS Harness 的正式 native tool ABI
- 基线：master `ddd10fb4`
- 前置：ADR-0103（动态工具面 spawn 超集 + per-turn setActiveTools）、
  ADR-0180（D2 字节预算 / D3 input gate / D5 metrics）、ADR-0104（turn
  token 回调鉴权）、ADR-0215（RuntimeSituation 供给）

## 背景与问题

ADR-0103 已把传输链完整落地：spawn file-dump（v2 无类型 dict）→ 扩展
registerTool 全量超集 → prompt marker per-turn `setActiveTools` →
`/pi-tools/execute`（bridge secret + HMAC turn token + live-turn 检查）→
`dispatch_tool` → ToolDispatchService（单一执行真相）。但四个结构性缺口
在 2026-10-18 recon 中确认：

1. **无版本化工具 ABI**：dump 是 ad-hoc dict——tier/side-effect/资源分级/
   凭证类/指纹/registry generation 全部缺席；扩展与后端之间没有可判 stale
   的契约锚。
2. **turn 工具面不消费 RuntimeSituation**：`compute_turn_active_tools` 只看
   检索分数 + tier + 字节预算；凭证缺席 / offline / durable worker 不可用
   要到 dispatch 期 capability bind 才裁决——模型先看到「可调」再吃
   INELIGIBLE。
3. **激活面无对照真相**：marker 是文本带外控制面（已消毒），但没有一份
   按 turn 铸造、可被回调侧验证的记录（candidates/selected/dropped/
   fingerprint）。
4. **结果面无契约**：`PiToolResponse.details: Any` + 启发式 64KB slim。

## 决策

**投影，不是第二真相。** 四个新模块全部是 registry → Pi 的只读派生，
执行权威零转移（native 调用仍 100% 走 ToolDispatchService）：

```
ToolRegistry/ToolDescriptor（唯一真相）
   → pi_tool_spec.compile_tool_spec（PiToolSpecV1 + fail-honest schema
     projection + canonical-JSON 指纹）
   → spawn dump v3（v2 键保留 + abi/registry_generation/spec 加性字段）
   → compute_turn_active_tools ⊕ RuntimeSituation（白名单投影）
       → qualification（封闭 drop codes）
       → apply_surface_byte_budget（既有 D2）
       → SurfaceReceipt（(session_id, turn_id) → 有界存储，turn 内不可变）
   → 扩展 registerTool + setActiveTools（不变）
   → /pi-tools/execute（鉴权不变 + toolFingerprint/surfaceFingerprint
     echo 对照 receipt；mismatch → typed surface_stale 拒绝）
   → dispatch_tool → ToolDispatchService（闸不变）
   → details.resultAbi（pi_result.v1 envelope：ref-first、≤4KB）
```

### 关键裁决

1. **Fail-honest schema projection**（`pi_tool_spec`）：JSON Schema 白名单
   之外的装饰键删除（封闭 finding codes 披露）；`description` 文本是唯一
   允许截断的"弱化"（≤512 字符，记 finding）；约束键（enum/pattern/
   min*/max*/type/required/…）verbatim 永不改写；union/$defs 原样保留。
   **放不进 per-tool 预算（`PI_SPEC_MAX_SCHEMA_BYTES`，默认 16KB，
   registry.schema_size 口径）→ 整工具逐出 native 面**（proxy 长尾仍可
   达），绝不裁 schema 换预算。
2. **指纹纪律**：spec 指纹 = 契约字段 + findings 的 canonical-JSON
   SHA-256 short digest；`registry_generation`（runtime manifest 指纹）是
   **来源锚不进 spec 指纹**（避免无关工具增删抖动指纹），但参与 surface
   指纹（顺序敏感：`[(name, spec_fp)] + generation`）。dump 的每个 spec 携带
   自身 `fingerprint` —— 扩展 echo 与 `SurfaceReceipt.tool_fingerprints`
   同源对照，二者不一致即永假阳性，故必须同源。
3. **资格断言纪律与 RuntimeSituation 一致**：unknown = 不排除；只有阳性
   缺席事实（`credentials_present[id] == False`、`offline is True`、
   `durable_worker is False`）才逐出（封闭 codes：
   `tier3_excluded` / `side_effect_destructive` / `credential_absent` /
   `offline_network_tool` / `durable_worker_unavailable`）。dispatch 期
   capability bind 仍是唯一执行权威——本层是 turn 侧投影，闸的先验披露。
4. **DESTRUCTIVE 副作用类逐出 native 注册面**（低 tier 同样）：红线
   "side-effect tool 不得经 native path 绕过确认"。proxy 长尾通道不变，
   dispatch tier 闸仍守。逐出在 `registered_surface_names` 单点生效——
   dump 与 dispatch 分类集 ≡ 扩展注册现实的不变量保持。
5. **SurfaceReceipt 按 (session_id, turn_id) 存储**（bounded 128，turn
   清理 evict；同 turn 首次铸造后**不可变**——registry 代内漂移只影响下
   一 turn，re-projection 告警并保留原 receipt）。不进 turn token：token
   在 prompt 组装**之前**铸造（时序事实），且保持对扩展不透明。
6. **Echo 校验是 stale 检测，不是权限**：权限链不变（bridge secret →
   turn token → live-turn）。echo 缺席 = master 兼容行为；receipt 缺席
   （动态面关/弃成）→ 放行。mismatch → **typed 200 isError 拒绝**（模型
   可读 guidance：re-discover → webgis_execute），不是 HTTP 错误。
7. **Opt-in 面成员硬校验** `PI_SURFACE_SCOPE_ENFORCE`（默认 OFF）：
   bare-name 回调不在 receipt.selected → typed 拒绝；proxy 通道不受限。
   默认关的理由：`setActiveTools` 不可用时扩展退化为全超集激活，硬校验会
   把合法调用误拒——作为生产 pilot 的渐进开关。
8. **Result ABI envelope 是加性披露**：`details["resultAbi"]`
   （`pi_result.v1`：status/summary/refs/map_action_ids/background_job_ids/
   evidence/error class）；大对象 ref-first 绝不内联；≤4KB 预算收缩顺序
   warnings → evidence → summary（status/refs/error 恒保留）。content 通道
   与既有 slim 逻辑不变。
9. **Kill switch 全家**：`PI_TOOL_ABI=0` → dump v2、无 qualification、无
   receipt、无 echo 校验（master 行为逐位一致）；`PI_RESULT_ABI=0` →
   details 原样；`PI_SPEC_MAX_SCHEMA_BYTES=0` → per-tool 预算关闭；
   `PI_DYNAMIC_TOOL_SURFACE=0` → 整个动态面回冻结 7 + proxy（既有）。
10. **扩展向后兼容**：v3 保留 `tools`/`default_active`/`execute_proxy`
    （v2 解析继续工作）；扩展新增的只是 per-call `toolFingerprint` echo
    （从 dump v3 `spec.fingerprint` 读取，v2 dump 下自然缺席）。

## 与其他方向的关系

- #1618 settle：不触碰（`_settle_turn_outcome` 区零改动）。
- #1615 skill procedure binding：surface qualification 的输入面留有
  situation 扩展位（`to_qualification_dict` 白名单），消费其约束属后续。
- #1616 clarification：同文件（stream_prompt）additive 区，无语义重叠。
- #1600/#1589：不回退（error_class / env allowlist / confirm_tier3_once
  全部原样；本方向零新增 subprocess env）。

## 后果

- 正向：Pi 侧工具面从"无类型 schema 列表"升级为带指纹的版本化 ABI；
  turn 面资格与 dispatch 资格共享同一事实源（RuntimeSituation）；stale
  call（registry 漂移 / 跨 turn）有机器可判定证据链。
- 代价：spawn 时编译 ~344 个 spec（一次性，实测 <1s）；每 turn 一次
  qualification + receipt 铸造（≤40 spec，纯内存）。
- 风险与对策：echo 永假阳性（已由同源指纹设计排除，见裁决 2）；
  scope enforce 误拒（默认 OFF + proxy 通道不受限）；receipt 泄漏
  （bounded + turn 清理 evict，与 dispatch cache 同纪律）。
