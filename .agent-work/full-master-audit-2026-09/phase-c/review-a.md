# Review A（correctness / architecture）— Harness V8（88d4ecde）+ 本会话修复批次抽查

范围：capability_graph.py / qualification_v8.py / candidate_planner_v8.py /
capabilities+algorithms modelops 域包 / registry_validation 图闸 / ADR-0136 /
测试；fix commits 723c3b2d..88d4ecde 抽查（B-3=3ffcca96、C-2=cbbc202b、
C-7=55b93c55、D-9=cf47ad0d 重点，其余批次未深查）。

验证手段：代码走读 + 实际运行复现（python -c 复现脚本）+ 测试执行
（test_capability_registry_parity.py + test_capability_graph_v8.py：25 passed；
test_audit3_fixes.py + test_audit3_batch4_fixes.py：13 passed）。

---

## MAJOR

### [RA-1] MAJOR — `models_for_capability` 未按 kind 过滤，返回 tool 节点
- 位置：`app/services/gis_harness/capability_graph.py:202-207`
- 证据：方法沿 `REL_IMPLEMENTS` 反向邻居取节点，但该关系同时承载
  `model→capability`（:462）与 `tool→capability`（:420-423）两类边，函数
  不过滤 `node.kind == KIND_MODEL`。实际运行复现：
  `models_for_capability("image_segmentation")` → **13 个 tool 节点、0 个
  model**；`("model_image_segmentation")` → 2 个 model（碰巧无 tool 直连
  该 capability，测试因此未暴露）。docstring/ADR D2 契约均为
  "capability → 候选 model"。
- 影响：任何信任该 API 的消费方把工具当模型候选；`model_*` 词汇一旦被
  工具 metadata 声明（自然的后续演进）即互相污染。
- 建议：`reverse_neighbors(...)` 后按 `n.kind == KIND_MODEL` 过滤；补一条
  "tools 不出现在 models_for_capability" 的回归测试。

### [RA-2] MAJOR — `plan_candidates_v8` 产生重复候选并挤占 `max_candidates` 名额
- 位置：`app/services/gis_harness/candidate_planner_v8.py:131-138`
- 证据：`nodes = models_for_capability(...)（含 tool，见 RA-1）+
  tools_for_capability(...)`，无跨列表去重。实际运行复现：
  `plan_candidates_v8("image_segmentation", ctx)` → 8 个候选中
  `modelops_cancel_inference / modelops_check_compatibility /
  modelops_compare_results / modelops_estimate_resources` 各出现 **2 次**，
  占满 `max_candidates=8`，把本应入选的唯一工具挤出候选面。
- 影响：候选列表失真（同一工具双份 score/penalty 统计口径）、排序语义被
  稀释；确定性本身未破坏但结果错误。
- 建议：以 `node.key`（kind:id）去重后再进入资格/排序循环（即使 RA-1 修复，
  planner 侧去重也是必要的健壮性）。

### [RA-3] MAJOR — 跨 owner scope 同名模型触发 `duplicate_identity` **error 级**假告警（合法注册态被 fatal 闸打红）
- 位置：`app/services/gis_harness/capability_graph.py:437-460`（节点 id =
  `f"{model_id}@{version}"`，丢弃 scope）、`:291`（指纹条目同样丢 scope）、
  `:320-332`（`_add` 冲突 → severity=error）
- 证据：ModelOps registry 身份 = `(owner_scope, model_id, version)`
  （`app/services/modelops/registry.py:11,164,384-389`，R1-C6 明示跨 owner
  注册是设计目标）。实际运行复现：向第二个 scope 注册同一
  `tiny-landcover-seg@1.0.0` 后 build 图，`validate_graph` 输出
  `{'code': 'duplicate_identity', 'severity': 'error', 'detail': 'model:
  tiny-landcover-seg@1.0.0 declared by modelops_registry and
  modelops_registry'}`。该 error 经 `registry_validation.py:78-85` 进入
  `validate_gis_library()`，而 ADR-0136 规定 error 级发现需为零 ——
  **合法注册态 = fatal 假阳性**；且第二个 scope 的节点被静默丢弃，其
  `owner_scope_key`/兼容性 extras 由先到者顶替（真源被错误投影）。
- 建议：节点 id scope 限定（对齐 registry 自身的 listing 格式
  `{skey}/{model_id}@{version}`，registry.py:199）；指纹条目同样带 scope。

### [RA-4] MAJOR — Qualification 的 `unknown` 状态不可达：上下文缺席 → 静默 `eligible`，违背 ADR D3 诚实契约
- 位置：`app/services/gis_harness/qualification_v8.py:124`（`unknown` 列表
  声明后**从未 append**）、`:183-185`；`qualify_model_for_input` :200-229
- 证据：模块 docstring（:5-6、:77 "缺席面 → unknown 诚实披露"）与
  `qualify_model_for_input` docstring（:194-195 "每项缺席 → unknown 诚实
  披露（不猜）"）均承诺缺席披露；实际代码仅在 `res == 0`（地理 CRS）分支
  产生 soft reason。`raster_bands=None` / `resolution=None` /
  `owner_scope_key=""` 等缺席面 → **零 reason → ELIGIBLE**。测试
  `test_compatible_input_eligible` 恰好喂满上下文，未覆盖缺席路径。
  另：默认 `QualificationContext()` 无 owner scope → 跨 scope 模型全部
  可见（资格层租户隔离静默失效，仅靠执行层兜底）。
- 影响：这正是 §30 关注的"把无据裁决伪装成合格"的邻近形态 —— V8.4 的
  estimate 有 basis 披露，V8.3 的 qualification 没有等价物。
- 建议：每个检查维度在"实体有声明但上下文缺席"时产出
  `QualificationStatus.UNKNOWN`（或至少 degraded + `*_unknown` reason）；
  owner scope 缺席时对非种子模型降级披露。

## MINOR

### [RA-5] MINOR — 图缓存指纹未覆盖 artifact registry（投影源 ≠ 指纹源）
- 位置：`app/services/gis_harness/capability_graph.py:257-294` vs `:473-481`
- 证据：build 第 5 段投影 21 个 `artifact_type` 节点，但
  `source_fingerprints()` 只有 capability/algorithm/runtime_manifest/
  modelops 四段 —— artifact registry 变化（测试插件注册、后续动态化）不会
  触发图重建，dangling 校验随之失真。缓存契约"source registry
  fingerprint → cache key"对第五个源不成立。
- 建议：补 `artifact_registry` 指纹段。

### [RA-6] MINOR — kill switch 是装饰品：`v8_capability_graph_enabled` 零调用方
- 位置：`app/services/gis_harness/capability_graph.py:32-34`；
  `registry_validation.py:78-85`
- 证据：grep 全仓仅定义与 `__all__` 出现；registry_validation 的图闸无条件
  运行（`GIS_CAPABILITY_GRAPH_V8=0` 不回退任何行为）。ADR-0136「兼容性」
  明示该开关为回退手段 —— 运维面承诺与实现不符。
- 建议：图闸与后续 planner 接线统一过开关，或从 ADR 撤回该承诺。

### [RA-7] MINOR — `_MODELOPS_TASK_TO_CAPABILITY` 词表不完整且无完整性闸
- 位置：`app/services/gis_harness/capability_graph.py:506-517`
- 证据：`app/lib/modelops/capabilities.py:25-40` 的封闭词表 `TASK_TYPES`
  共 **11** 项，映射表只有 **10** 项 —— `classification` 缺席
  （`seeds.py:199` 的种子模型带 `(embedding, classification)` 双任务）。
  未命中 task_type 经 `.get()` **静默丢边**，`validate_graph` 只查 dangling
  不查 missing，未来新增任务类型将继续无痕消失（本仓审计反复打击的
  "科学门槛无痕消失"形态）。
- 建议：`TASK_TYPES ⊆ 映射键 ∪ 显式豁免表` 的断言/单测；`classification`
  要么入映射要么显式豁免。

### [RA-8] MINOR — 模型 ExecutionEstimate 的 basis 标注失实：猜测标成 `declared`
- 位置：`app/services/gis_harness/qualification_v8.py:328-339`
- 证据：`est.latency_class = "slow"` 硬编码、`est.gpu = "gpu" in
  provider.lower()...` 子串推断，两者 basis 均写 `"declared"`。V8.4 契约
  （ADR D4 / 模块 docstring :10-11 "不把猜测伪装成测量"）下这属于把估计
  冒充声明 —— 恰是该维度要消灭的形态。`test_model_estimate_gpu_declared`
  反而把失实标注钉死成了预期。
- 建议：改标 `estimated`（或 `unknown`），相应测试断言同步修正。

### [RA-9] MINOR — 边预算截断静默，违背模块自述契约
- 位置：`app/services/gis_harness/capability_graph.py:335-339`
- 证据：`_edge` 在 `MAX_EDGES` 触顶时直接 return，不记 issue；而
  模块 docstring（:16-17）与常量注释（:77 "超过即截断并在 issues 披露
  （不静默）"）承诺披露。仅 node 预算（:320-326）有披露。
- 建议：补 `edge_budget_exceeded` issue。

### [RA-10] MINOR — C-2 残留：`removeLayerFromSpecOnce` 的 undefined 分支整体旁路 await 后会话复核
- 位置：`frontend/lib/mapspec/user-mutation.ts:423-426, 445-446, 457-458`
- 证据：`enqueuedSessionId === undefined` 时预检 warn+放行，但成功/异常
  两条 await 后复核均以 `enqueuedSessionId !== undefined && ...` 为前置
  —— undefined 调用方保留了 C-2 要修的原始洞（POST 在飞期间切会话 →
  旧会话 revision/spec 写入新会话游标）。当前生产调用方均显式传参
  （layerCommands.ts:444、user-mutation.ts:522），仅防御路径敞开。
- 建议：入口 `const bound = enqueuedSessionId ?? sessionId`，await 后统一
  复核 `cursor === bound`。

### [RA-11] MINOR — C-7 语义变化未回写 ADR：`RestoreStyle` 从"豁免逐层锁"变为整笔锁拒
- 位置：`app/services/mapspec/lifecycle_engine.py:683-726`
  （`intent_lock_targets` 含 RestoreStyle 全快照目标 + ReorderLayers）、
  已删除的 V6 守卫 docstring 明示 "RestoreStyle …不走逐层锁语义（ADR
  披露）"（55b93c55 diff）
- 证据：W15 统一守卫覆盖面是旧守卫**超集**（旧：Remove/PatchPresentation/
  PatchStyle/Upsert；新：另含 Reorder/组件族/SetLayout/RestoreStyle），
  谓词 `_lock_matches` 双向族前缀与旧 `_locked_family_hit` 语义一致，
  batch 路径（apply_presentation_batch 仅收 PatchLayerPresentationIntent，
  `guard_locked_partitions(intent.layer_id)`）等价覆盖且 origin 覆盖
  agent+system —— 删除本身**正确**。但 RestoreStyle/Reorder 的收紧行为
  改变了既有 ADR 披露口径，未见文档更新与钉死该语义的回归测试。
- 建议：更新 ADR 披露 + 为 RestoreStyle 含锁层拒绝补回归测试。

## NIT

### [RA-12] NIT — 图自身 `fingerprint()` 不含 corpus/extras
- `capability_graph.py:231-238`：payload 仅 (key, source_registry, label)
  + edges；仅 summary 差异的两个投影同指纹。`to_dict` 以 graph_fingerprint
  名义透出，语义偏宽。建议纳入 extras 哈希或收窄文档口径。

### [RA-13] NIT — `get_capability_graph` 双重计算 source 指纹（TOCTOU + 双倍磁盘 IO）
- `capability_graph.py:536-549` vs `:494`：缓存键与 build 内部各算一次
  `source_fingerprints()`（各含 ModelRegistryStore 磁盘 load）；两次结果
  理论上可分叉 → 缓存键与 `graph.source_fingerprint` 字段不一致。算一次
  透传即可。

### [RA-14] NIT — reorder_layer 内联守卫是恒真的自比较死代码
- `frontend/lib/map-commands/layerCommands.ts`（reorder IIFE）：
  `enqueuedReorderSession` 读取与比较之间无 await，必然相等；真实保护在
  `commitMapSpecMutation` 内部守卫（user-mutation.ts:363-390）。建议删除
  或改为入队时捕获，避免给读者"此处已有会话绑定"的错觉。

### [RA-15] NIT — D-9 细节：ver 强转可 500、db 依赖对公共路径并非零开销
- `app/api/routes/static.py:87-90`：`int(user.get("ver") or 0)` 遇畸形
  ver claim（签名令牌自带，低概率）抛 ValueError → 500 而非降级；
  `db=Depends(get_async_db)` 对 public/signed 请求也解析（创建 session
  对象，无连接）——"零额外开销"表述略过。功能面（fail-closed、仅 admin
  分支复核、public/signed 判定独立）验证正确。

---

## 抽查结论（修复批次）

- **B-3（3ffcca96）per-run 通道清理时序 — 通过**。`close()` 幂等
  （`_memmap_dir` 守卫 + 置 None，engine.py:1784-1793, 1957-1970）；
  异常路径 close 一次（run() 内 BaseException 处理器 :692-696），`run()`
  finally 只 pop 不 close（:291-293）→ **无二次 close**；superres 自带
  finally close+pop；`_run_tiled` 成功路径 finalize 内部 close，遗留键由
  run() finally pop 兜底；run_id 键控后并发 run 互不干扰。
- **C-2（cbbc202b）会话守卫 — 主路径通过**（capture-at-entry + await 后
  复核齐备：visibility-transaction postPresentationOnce 成功/superseded
  双分支、component-mutation 两处、commitMapSpecMutation）；残留见 RA-10。
- **C-7（55b93c55）守卫删除 — 覆盖面通过**（超集 + 谓词等价 + batch 等价，
  见 RA-11 证据）；文档口径未同步。
- **D-9（cf47ad0d）ver 复核 — bypass 面闭合**。复核仅在 `is_admin` 分支
  内发生，public/signed 判定相互独立不受影响；row 缺失或不匹配 → 降级
  （fail-closed）；全仓无其它以 `get_current_user_optional` + role==admin
  门禁私有读的路由。

## 禁止事项核查（任务书 §30，V8 范围）

- **fake success：未发现**。源缺席 → `'absent'` + `source_unavailable`
  issue（不伪装）；未知 capability → excluded + reason；不符资格 →
  excluded 携带结构化 reason。RA-4 的"缺席即 eligible"属诚实性缺口
  （MAJOR 处置），非伪造成功路径。
- **无界缓存/上下文：未发现**。`_graph_cache` 单槽；corpus 400 / label
  96 / id 128 截断；extras 各项有界；候选 `max_candidates` 截断；
  reliability 快照 ≤32。
- **为测试 special-case：未发现**。`reset_capability_graph` /
  `graph_build_count_for_tests` 仅控制缓存生命周期，不改变裁决逻辑；
  模型注册 fixture 是测试Setup而非生产分支。

## 其他验证记录（无 finding）

- 指纹碰撞/时钟：内容哈希（无时钟依赖）；分段 16 hex + 合成 32 hex，
  碰撞风险可忽略。线程安全：缓存读写全程持 RLock；`_RUN_LOCAL` /
  `_active_providers` 按 run_id 键控后并发安全（GIL 下 dict 原子操作 +
  键隔离）。
- modelops 指纹仅取身份（id@version）是**安全的**：registry.register 对
  同 key create-only（:384-389），同版本不可变内容重注册。
- ledger `||` 前缀匹配正确：V8 用单竖线前缀 `entity|` 匹配
  `entity||class`（ledger `_key` :76-77），entity 词法（kind:id，charset
  校验后无竖线）保证无前缀歧义；`attempts` = TTL 内连续未清零失败数，
  成功清零 —— penalty=min(1, fails/4) 语义与 ADR D5 一致。
- 排序确定性：候选按 (score, kind, id)；models/tools 各自按 id 排序；
  同输入同序（测试钉死）。
- V7/V8 资格判断并存边界：当前无运行时冲突 —— V8 planner 未接线
  （delivery 文档诚实披露），V7 `check_preconditions` 仍是唯一裁决路径；
  qualification_v8.py:149-153 明确不复制 V7 详细前置。接线后两套并行
  （V8 计划证据 vs modelops 执行时 check_compatibility）的漂移闸缺失，
  建议后续补 parity 测试（与 RA-7 同类）。
- parity/ontology/词表门：`test_capability_registry_parity.py` +
  `test_capability_graph_v8.py` 25 passed；model_* 域包经
  `iter_capability_packs` 入册（capabilities/__init__.py:31）；
  `domain="temporal"` 与既有 temporal 域包一致（capability_registry 无
  domain 封闭词表校验，仅注释性口径）。

---

## 计数

- BLOCKER: **0**
- MAJOR: **4**（RA-1, RA-2, RA-3, RA-4）
- MINOR: **7**（RA-5 … RA-11）
- NIT: **4**（RA-12 … RA-15）
