# Goal 04 Worklog — Pi Native GIS Tool ABI V2

## 2026-10-18 recon 结论

（详 `PR_OVERLAP_MATRIX.md`）传输链已在 master 完整存在；本方向交付 typed ABI +
situation qualification + SurfaceReceipt + fingerprint 回调绑定 + Result ABI。

## 架构决策（自动裁决记录）

1. **PiToolSpecV1 是投影，不是第二真相**。`pi_tool_spec.py` 从
   `ToolRegistry.descriptor(name)` + live schema 派生 frozen dataclass；指纹 =
   canonical-JSON SHA-256 short digest（复用 `descriptor.canonical_json` 同款纪律，
   但独立实现于本模块，避免 import 方向问题——descriptor 在 app/tools，无环）。
2. **Schema projection fail-honest**：只删「无损装饰」键（封闭 finding codes +
   白名单外的键）；约束性键（enum/pattern/min-max/required/type）永不改写。
   描述文本截断是唯一允许的"弱化"，仅限 description 字符串且必须记 finding。
   超 per-tool schema 预算 → **整工具逐出**（proxy 长尾仍可达），不裁 schema。
3. **qualification 输入 = RuntimeSituation 白名单投影**（复用
   `to_qualification_dict()`，不建第二消费契约）。断言纪律与
   runtime_situation 一致：无来源 = unknown = 不排除；只有**阳性缺席事实**
   （credentials_present[id]=False、offline=True、durable_worker=False）才排除。
4. **SurfaceReceipt 按 (session_id, turn_id) 存储**（bounded 128，turn 清理时
   evict），不进 turn token（token 在 prompt 组装**之前**铸出，时序上不可能；
   且 token 保持对扩展不透明）。回调侧：`toolFingerprint`/`surfaceFingerprint`
   echo 字段（additive，缺席=master 行为）+ receipt 对照，mismatch → typed 409。
5. **dump v3 additive**：保留 `tools`/`default_active`/`execute_proxy` 键（扩展
   v2 解析继续工作），新增 `abi`、`registry_generation`、per-tool `spec`。
6. **注册面不变量保持**：`registered_surface_names` 同时是 dump 与 dispatch 分类
   依据（"matching the extension's registration reality"）——per-tool 预算与
   destructive 副作用类排除都在**这一处**生效，两边同步，不产生分类≠注册漂移。
7. **flags**（PI_* 前缀，与 PI_DYNAMIC_TOOL_SURFACE 同族，flag registry 只盘点
   GIS_*，不新增 GIS_*）：
   - `PI_TOOL_ABI`（默认 ON，kill switch=0 → 完全 master 行为：dump v2、无
     receipt、无 echo 校验）；
   - `PI_RESULT_ABI`（默认 ON，kill switch）；
   - `PI_SURFACE_SCOPE_ENFORCE`（opt-in 默认 OFF：bare-name 面成员硬校验——
     防 setActiveTools 降级时模型绕过激活面；默认关保持兼容）；
   - `PI_SPEC_MAX_SCHEMA_BYTES`（默认 16384；0=关闭）。
8. **Result ABI envelope** 放 `details.resultAbi`（additive key），content 通道
   不变；envelope 有界（≤4KB）且 ref-first（geojson/image → ref，不进 payload）。
9. **destructive 副作用类（低 tier）逐出 native 面**：Goal 04 红线「Tier 3 /
   side-effect tool 不得绕过确认」；tier>=3 闸不变，DESTRUCTIVE class 工具
   不再进 dump 超集（proxy 长尾可达，dispatch tier 闸仍守）。排除记因
   `side_effect_destructive`。

## 实施清单

- [x] recon + overlap matrix
- [ ] app/services/chat/pi_tool_spec.py
- [ ] app/services/chat/pi_surface_receipt.py
- [ ] app/services/chat/pi_result_abi.py
- [ ] pi_native_surface.py 接线（dump v3 + qualification + receipt）
- [ ] pi_turn_context.py / providers.py turn_id 线程化
- [ ] agent_pi_bridge.py（PiToolRequest echo 字段 + details envelope + cleanup）
- [ ] routes/pi_tools.py（echo 校验 + scope enforce）
- [ ] index.mjs（v3 spec 解析 + echo）
- [ ] tests + ADR
