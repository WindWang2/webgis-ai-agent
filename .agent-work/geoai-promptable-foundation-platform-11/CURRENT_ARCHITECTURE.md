# CURRENT_ARCHITECTURE — 现状 authority/seam 图（Phase 0 冻结，基准 `faa453a8`）

## 1. 平面分层（master 现状，ADR-0119 + V3 §A–§H 已合入）

```text
Capability（做什么）   app/lib/gis/capabilities/modelops.py（model_* 词汇族，8 个 id）
Algorithm（方法语义）   app/lib/gis/algorithm_registry（不动）
Model（learned 推理）
  ├─ 纯契约层           app/lib/modelops/（descriptor/capabilities/compatibility/
  │                     planning/preprocess/stitching/fingerprint/promptable/
  │                     foundation/temporal/package_security/evaluation/metrics）
  └─ 运行时层           app/services/modelops/
        engine.py       InferenceEngine：resolve→qualify→reproject→tile plan→
                        device/VRAM→loaded cache→有界批循环→融合→产物→manifest→reuse
        service.py      ModelOpsService 门面（owner scope 一维；取消键=run_id）
        registry.py     ModelRegistryStore（持久化/revision/owner scope）
        providers/      base(Protocol+Registry) + promptable_reference + tiny_* +
                        onnx/torch/subprocess/remote/extension + mock_gpu
        loaded_cache.py 权重 LRU（refcount/TTL/negative cache）
        reuse.py        结果级 reuse（InferenceFingerprint 精确匹配）
        scheduling.py   VramLedger/WarmPool/multi-GPU affinity
        lineage.py      append-only 模型 lineage
Tools（agent 面）       app/tools/modelops_tools.py（12 个 modelops_* 工具，
                        含 modelops_run_promptable）
UI                     无 modelops/geoai 面（tools-only；无 API route）
```

关键不变式（沿自 ADR-0119 冻结稿）：core 进程不执行模型包内容；唯一栅格读通道
`RasterReader.read_window`；缓存全有界；失败全 typed（ModelOpsError 家族）；
reuse = fingerprint 全字段精确匹配；prompt 几何/坐标系进 fingerprint。

## 2. Prompt 数据流（现状）

```text
调用方（tool/service）
  └─ InferenceRequest(prompt=PromptSpec, prompt_crs=bool)
       ├─ qualifier：PromptSpec.required_prompt_modes ⊆ descriptor 语义
       ├─ engine 第二道门：modes ⊆ provider.prompt_modes（R2-M7）
       └─ _run_promptable：
            prompt_crs=True → foundation.prompts_to_pixel（仿射逆变换）
            → foundation.prompt_windows（单窗口/prompt 锚定多窗口，≤4096px）
            → 每窗口 preprocess + window_local_prompts 平移
            → provider.infer（promptable_reference：区域生长/box 相似度/
               prior∩；输出 (1,2,H,W) 概率）
            → argmax==1 掩膜 → 每窗口独立仿射地理参考 → GeoJSON
            → 可选整幅 canvas（≤256M px）→ prompt_mask.tif
```

PromptSpec（`app/lib/modelops/promptable.py`）：points/boxes(prior)mask/text +
combine(union|intersect) + labels；`MAX_PROMPTS_PER_KIND=64`；坐标语义 =
窗口像素坐标（地理坐标由 engine 换算，`prompt_crs: bool` 只声明"是地图坐标"，
不携带 CRS 身份）。prior mask 不经 JSON（engine 以数组入 extras）。

## 3. Embedding 现状

- TASK_EMBEDDING + OUTPUT_EMBEDDINGS 词表已存在；tiny_reference 提供确定性
  chip embedding；`stitching.SpatialEmbedding`（core_window 锚定）按 run 收集
  → `embeddings.json` → publish_json_artifact。
- **无跨 run 缓存**：每 run 重算；无 (model, asset, grid, window) 键控存储、
  无部分失效、无 resume、无磁盘 digest 校验。loaded_cache 只管权重；reuse 只管
  整 run 结果。

## 4. Text/多模态现状

- PROMPT_TEXT 词表 + ProviderCapabilities.text_prompt 存在；
  promptable_reference 的 text 是**显式 stub**（哈希→亮度带，manifest 如实记录）。
- 无 text/image embedding seam、无类别原型/检索/zero-shot 契约。

## 5. Tools/能力词汇/前端现状

- 工具：modelops_list/inspect/check_compatibility/estimate_resources/run_inference/
  run_promptable/evaluate/compare/inspect_provenance/record_metrics/model_history/
  publish_layers/cancel —— **无 prompt artifact 检视、无 embedding 工具、无
  candidate/refine 工具**；run_promptable 只收 points/boxes（无 text/mask/
  polygon/reference-layer/artifact 引用）。
- 能力词汇：8 个 `model_*` id（分割/检测/实例/变化/…）。
- API routes：无 modelops/geoai HTTP 面。前端 components 无 geoai 目录。

## 6. 缺口清单（本 track 的实施对象 = 下一层可验证缺口）

| # | 缺口 | 现状证据 | 对应 WP |
|---|---|---|---|
| G1 | prompt 无版本化 artifact/身份/CRS 身份/时间/band-model 绑定；`prompt_crs` 是 bool | engine.py:195 | A |
| G2 | 无 polyline/polygon/reference-layer prompt 词表与语义 | capabilities.py:90-96 | A |
| G3 | map CRS↔pixel 往返无显式容差契约 + known-answer | foundation.prompts_to_pixel 无容差声明 | A/H |
| G4 | promptable 单掩膜输出：无多候选/质量分/选择策略 | promptable_reference.infer 返回 (1,2,H,W) | C |
| G5 | embedding 无有界跨 run cache（键控/失效/resume/digest） | stitching.SpatialEmbedding 每 run 重算 | D |
| G6 | 无 text/image embedding seam、类别原型、zero-shot 语义映射 | 无对应模块 | E |
| G7 | 工具面缺 prompt artifact inspect/explain、embedding、prompt_refine | modelops_tools.py 全集如上 | F |
| G8 | 无独立 geoai UI 面板与 HTTP 面 | routes/components 检索为空 | G |
| G9 | tile seam/大 prompt 批/cancel 在 promptable 路径的回归矩阵不全 | 测试检索：georeference/zero-prior/reuse-invalidation 有，seam/scale 无 | H |

## 7. 既有真值（本 track 不得重建）

descriptor/registry/provider registry/planner/preprocess/stitching/fingerprint/
reuse/loaded_cache/VRAM ledger/lineage/capability registry —— 全部只在既有模块上
**additive 扩展**（新词表成员、新字段、新 provider、新工具、新 service 方法）。
