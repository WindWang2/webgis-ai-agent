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

## 2026-10-18 Subagent B（adversarial）结果 + P2 清偿

新增 `tests/unit/test_pi_abi_adversarial.py`（11 用例全绿）：跨 turn 重放 /
重复回调 dedup / 迟到回调 / 代内 registry 漂移 / receipt 容量界 / scope
enforcement 攻击矩阵 / kill-switch 等价 / result ABI 诚实性 / 扩展 echo 契约。

B 报告 3 个 P2，处置：
1. **registry_generation 锚不反映注入 registry**（`refresh_runtime_manifest()`
   无参重编译自建 init_tools registry —— pre-existing #1084 语义）：不改
   （非本方向所有权；production lifespan 首编用真实 registry，动态刷新走
   extensions_platform 传参路径；无离线可证安全影响）。登记为 follow-up。
2. **re-projection 披露分叉**（disclosure 拿新指纹、store 留原 receipt）：
   已修 —— `store.record` 返回实际生效 receipt，`record_turn_surface`
   透传；disclosure/链发射消费存量指纹。
3. **proxy echo 死分支**（wrap 已注册工具在扩展侧先拒，map 查找恒 miss）：
   已移除并注释构造性原因。

B 定性行为（不修，测试已钉）：dedup 键 = (tool, canonical args)（非
toolCallId）；receipt 缺席时 echo 不校验（master 兼容）；409 body 是统一
信封 `data.code`；post-dispatch profiler 采样进 details 是 master 行为。

无法离线覆盖（PR 中披露）：跨 pod Redis 协调、真并发竞态、动态注册→
manifest 端到端漂移（受 P2-1 限制）、真实 Pi runtime setActiveTools 降级。
