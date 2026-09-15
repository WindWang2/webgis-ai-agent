# ADR-0185: VLM Visual Critic Runtime —— 具身多模态视觉感知与图面缺陷结构化诊断

- 状态：Proposed（随 `agent/01-vlm-visual-critic-runtime` 分支评审）
- 日期：2026-09-14
- 关联：ADR-0158（视觉裁判与自愈闭环 —— 本 ADR 是其 P2 judge 的运行时升级）、
  ADR-0060（validity 阶梯天花板）、ADR-0061（诚实评估 / 无证据 ≠ 修正）、
  specs/cartographic-quality-rules-and-memory-spec.md、
  方向任务书 agent-swarm/01-vlm-visual-critic-runtime
- 接口规格书：docs/dev/vlm-visual-critic-spec.md

## 背景与问题

ADR-0158 落地的 L5 视觉裁判 seam（`app/lib/harness/visual_evaluator.py`）解决了
「裁判接线」问题，但 judge 本体仍是**薄打桩面**：

1. **Agent 看不见图**。内置 VLM callable 只发一张图 + 一段自由文本 prompt，输出
   无 JSON-schema 约束、无图面坐标定位、无逐维度量化评分。当出现标注互相重叠
   完全不可读、深色底图配深色图斑对比度缺失、相邻分类色板极难分辨、遥感底图与
   矢量图斑剧烈偏移时，裁判常常「评了个寂寞」——Agent 自满判定已完成。
2. **维度缺口**：既有白名单 5 维里第 5 维是 `polish_completeness`（装饰完整性，
   偏静态规则可查），缺少 **spatial_alignment**（遥感底图/矢量叠加对齐）——这一维
   恰恰是确定性规则最难覆盖、最需要视觉感知的。
3. **provider 单一**：内置实现硬编码 OpenAI 兼容 chat completions，Gemini 结构化
   多模态（`responseSchema`）无通道；注入 seam（`CARTO_VISUAL_JUDGE`）要求使用方
   自备整个 judge，离线/回放/测试场景缺一套确定性 Mock。
4. **输入无预检**：截图字节直接 base64 进 prompt；损坏图片、过小图、纯色空图
   无确定性初筛，浪费外呼且污染评审语义。

## 决策

### D1 — 独立运行时包，不动既有 seam 的默认行为

新建生产包 `app/lib/harness/visual_judge/`：

- `contracts.py` — Pydantic 严格契约（`VisualCritiqueItem` / `VisualJudgeReport` /
  `VisualDimensionScore` / `VisualBBox`，全部 `extra="forbid"`）；
- `snapshot_extractor.py` — 截图解码/校验/哈希/灰度直方图初筛；
- `vlm_provider.py` — OpenAI 兼容 / Gemini 双通道结构化视觉请求组装器；
- `critic_engine.py` — 评审编排（fail-closed、记忆化、评分推导、白名单消毒）；
- `fake_vlm.py` + `golden_images.py` — 离线确定性 Mock 体系（10 种典型视觉缺陷
  黄金样本 + 确定性合成渲染图）。

`visual_evaluator.py` 的既有路径**逐字节保持**：默认（不开新开关）reason 码、
evidence 行形状、记忆化行为全部不变；新运行时通过显式 opt-in 开关挂接
（见 D7）。既有 `polish_completeness` 白名单不动——新维度的白名单属于
**新契约**（`VisualDimension`），两套白名单共存，映射边界在 D2 说明。

### D2 — 评审契约：5 维白名单 + 图面坐标定位 + 置信度

新契约维度白名单（严格枚举，白名单外整条丢弃）：

```
readability             可读性（标注/文字/符号是否可读）
color_discriminability  色彩可分辨性（相邻分类/图斑是否可分辨）
composition_balance     版面重心（构图是否失衡）
information_density     信息密度（过密不可辨 / 过疏无信息）
spatial_alignment       空间对齐（遥感底图与矢量叠加是否错位、指北/倾斜异常）
```

- **bbox 定位**：`VisualBBox = [ymin, xmin, ymax, xmax]`，全部为图面**百分比**
  坐标（0–100，左上原点），约束 `ymax > ymin ∧ xmax > xmin`；VLM 输出的 bbox
  经范围/次序校验，不合法整字段置空（不整条判废——定位是增值信息，非门禁）。
- **置信度**：每条批评必须携带 `confidence ∈ [0,1]`；缺失/非法按 0.0 处理并
  在评分推导中如实体现（低置信 ≠ 高分背书）。
- **severity 三态**：`info | warning | error`，与 ADR-0158 词表一致；`error`
  是唯一阻断级（映射 L5 fail）。
- **改图意图消毒**：沿用 ADR-0158 纪律——critique 携带 mutation/intent/spec/
  patch/layers 等改图意图字段 ⇒ 整条判废（v2 契约层 `extra="forbid"` 直接
  拒绝未知字段，消毒是双保险）。
- `polish_completeness` 不在 v2 白名单：整饰完整性由确定性规则（图例/比例尺/
  指北针存在性检查）覆盖，视觉裁判不重复计分。legacy seam 与 v2 的维度差异
  由各自白名单自治，互不越界。

### D3 — fail-closed 矩阵（绝不伪造 pass）

`VisualJudgeReport.status ∈ {evaluated, not_evaluated}`；`not_evaluated` 必携带
机器可读 `reason`。全矩阵：

| 失效点 | reason | 缓存 |
|---|---|---|
| 运行时开关未开 / 引擎未配置 client | `not_configured` / `disabled` | 否 |
| VLM 未配 key（含占位符） | `no_api_key` | 是* |
| 图片空/损坏/非图片魔数 | `image_empty` / `image_corrupt` | 是* |
| 图片超限（> 4 MiB）/ 边长过小 | `image_oversized` / `image_too_small` | 是* |
| VLM 超时 | `provider_timeout` | 是* |
| VLM HTTP/解析失败 / 输出非法 | `provider_error` / `invalid_output` | 是* |
| 成功评审 | `evaluated`（reason 空） | 是 |

\* 缓存语义与 ADR-0158 一致：`(session_id, mapspec_fingerprint, image_sha256)`
命中 ⇒ **单轮至多一次真实外呼**，重复评估直接回放缓存结论（含 not_evaluated）。
副作用：瞬态失败后同图重评需新世代截图或进程重启——这是「每份证据状态至多
一次外呼」限流纪律的既定代价（防评估面高频重入把 VLM 配额打穿），不是缺陷。

记录纪律（与 ADR-0061 对齐）：`not_evaluated` 落 `VISUAL_ORACLE` 证据行
（not_evaluated/info），**不产出 pass，也不因缺席而 fail**；L5 推导沿用
`derive_goal_satisfaction` 的 fail-closed 语义不变。

### D4 — 快照管线：解码 → 校验 → 哈希 → 确定性初筛

`snapshot_extractor.SnapshotExtractor`：

1. 输入接受 `bytes` 或 base64/data URL（前端 Canvas 上报、headless Playwright
   快照、`runtime_dir/map.png` 三源统一）；
2. 魔数嗅探（PNG/JPEG/WebP）→ Pillow `verify()` 结构校验 → 尺寸护栏
   （`[64, 8192]` 边长、≤ 4 MiB 字节）；
3. `sha256` 指纹（记忆化键第三元）；
4. **灰度直方图初筛**（确定性，不外呼）：降采样至 ≤512px 长边后计算平均亮度、
   亮度标准差、p5/p95 对比跨度、直方图熵，输出 hints（`near_blank` /
   `low_contrast` / `extreme_dark` / `extreme_bright`）。

初筛 hints **只作为诊断证据随报告披露，永不替代/预判 VLM 结论**——深色主题
地图可以低亮度但完全合格；确定性信号越权视觉判断会重新打开「规则冒充裁判」
的后门。

### D5 — Provider 抽象：OpenAI 兼容 / Gemini 双通道 + 严密 JSON-schema

`vlm_provider.py` 统一 `VLMClient` 协议（`async critique(request) -> str`）：

- **openai_compatible**（默认）：`POST {base}/chat/completions`，
  `response_format = {type: "json_schema", strict: true}`，图片走
  `image_url` data URL；base_url/key/model 复用
  `CARTO_VISUAL_JUDGE_{BASE_URL,API_KEY,MODEL}`（回落 settings `LLM_*`，
  与 legacy judge 同源，不新增配置家族）。
- **gemini**：`POST {base}/v1beta/models/{model}:generateContent`，
  `generationConfig.response_mime_type = "application/json"` +
  `responseSchema`（同 schema 的 Gemini 方言），图片走 `inline_data`；
  key 走 `x-goog-api-key` 头。
- **单一 schema 事实源**：critiques 数组的 JSON Schema（维度枚举、severity
  枚举、confidence 数值域、bbox 四元数组、字段数上限 12）由一个 Python 常量
  生成两个 provider 方言——schema 漂移不可能发生。
- **限流纪律**：单次调用、无重试、显式超时（默认 20s，`CARTO_VISUAL_JUDGE_TIMEOUT_S`）。
  超时/HTTP 错误/解析失败向上抛类型化异常，由 engine 归因为 D3 的 reason。
- `provider`/`model` 写入报告供证据行披露；key 永不进日志与报告。

### D6 — 评分推导：确定性、可审计、抗自评 gaming

`VisualDimensionScore` **由引擎从 critiques 确定性推导**，不采信 VLM 自报
维度分（防自评膨胀；VLM 只描述看见了什么，评分权在引擎）：

- 维度分 = `max(0, 10 − Σ penalty)`，penalty：error=4.0 / warning=1.5 / info=0.5；
- 维度置信度 = 该维度各批评 confidence 的最大值；无批评维度 = 分 10 / 置信 0
  （诚实语义：「未观察到」≠「观察到良好」）；
- `overall_score` = 置信度加权平均（置信 0 的维度不入权重）；全部维度未观察
  ⇒ overall = 10 / 置信 0；
- `has_blocking_defects` = 存在 error 级批评（L5 fail 的唯一视觉触发条件）。

### D7 — harness 接线：opt-in、优先级明确、record-only 不变

`visual_evaluator.attach_visual_judgement` 的 judge 解析顺序：

1. `CARTO_VISUAL_JUDGE="module:callable"` 注入 judge（显式运维/测试覆盖，
   最高优先，行为不变）；
2. `CARTO_VISUAL_CRITIC_RUNTIME=1` 且注入缺席 ⇒ **新 Critic 运行时**接管本次
   评审：截图发现/字节读取复用既有 helper，结论经
   `_apply_critic_report` 落证据（行形状与 legacy 逐键对齐 + 增列
   bbox/defect_type/dimension_scores/provider 字段）；
3. 以上皆否 ⇒ legacy 内置 VLM judge（`CARTO_VISUAL_JUDGE_VLM=1`），行为不变。

- 开关默认关闭：不开 ⇒ 全路径逐字节等价改造前（存量测试零感知）。
- **record-only 语义不变**：视觉结论只追加 `evidence_class: visual` 证据行与
  `visual_evidence` 摘要，不改写既有三态 verdict；阻断模式仍是 ADR-0158
  预留的显式切换。`derive_goal_satisfaction` / replay replayer /
  GisTraceChain 的 L5 消费面零改动（摘要 `source` 保持 `visual_judge`，
  运行时身份以新增 `runtime: "visual_critic_v2"` 键披露）。
- 记忆化分工：legacy seam 记忆化只服务 legacy 路径；v2 引擎自带
  `(session_id, mapspec_fingerprint, image_sha256)` LRU 记忆化（容量 64），
  两套互不污染。

### D8 — 离线确定性 Mock 体系（测试与回放的公共事实源）

`fake_vlm.GOLDEN_SAMPLES`：10 种典型视觉缺陷黄金样本（ canned 响应 + 期望
维度/严重度断言元数据）——重叠标注、深色低对比、相邻色板混淆、符号过密、
画布过疏、版面下坠、叠加错位、极端倾斜、字号过小、良图对照。
`golden_images.py` 用 Pillow 确定性渲染同名录入图（无随机源，字节级可复现，
支撑 sha256 幂等断言）。`FakeVLMClient` 实现 `VLMClient` 协议：按样本名路由
canned 响应、计数外呼、可注入超时/坏输出故障。该 Mock 体系是**产品代码**
（回放基准/离线演示/契约测试共用），不是测试私有夹具。

## 后果

- 正面：Agent 首次具备结构化「看图」能力——缺陷带图面坐标、置信度与维度
  量化分，可回灌自愈动作空间（rotate_palette 等既有 AUTO_SAFE 动作语义不变）；
  双 provider 消除单供应商绑定；确定性 Mock 让视觉评审进单测/回放门禁成为
  可能（此前只能 mock 整个 judge）。
- 风险与缓解：VLM 幻觉 → schema 约束 + 白名单消毒 + 置信度披露 + record-only
  （视觉结论永不单独改写 verdict）；外呼成本 → 单轮单呼 + 记忆化 + 初筛不外呼；
  两套白名单共存的心智负担 → v2 契约自文档 + 规格书明确映射边界。
- 非目标（本 ADR 不做）：阻断模式切换（仍待 10 线 ratchet 基线稳定）、
  spatial_alignment 的确定性互证（底图/矢量像素级配准属后续方向）、
  维度级自愈动作扩展（change_palette 之外的动作注册）。
