# PLAN — GeoAI Promptable Foundation Platform 11.0（ModelOps 之上的下一层）

基准：`origin/master` = `faa453a8`。scope 见 PARALLEL_OWNERSHIP §5。
缺口依据：CURRENT_ARCHITECTURE §6（G1–G9）。每步：现状证据 → 垂直切片 →
known-answer/negative test → failure/cancel/resource → 文档同步 → commit。

## Phase 1 — 契约层（WP-A：GeoPrompt artifact 与坐标契约）【G1/G2/G3】

1. `app/lib/modelops/geo_prompt.py`（新）：
   - `GEO_PROMPT_SCHEMA_VERSION = 1`；
   - `GeoPromptArtifact`：crs 身份（字符串，None=像素坐标）、geometry
     （points/boxes/polylines/polygons）、mask 引用（sidecar 路径 + sha256）、
     text、reference_layer（data_object/uri + band + 阈值策略）、time 语义
     （acquisition/valid window 可选）、band/model identity（目标 model_id/
     version/band_names 可选）、provenance（created_by/note/source）；
   - `artifact_id`：canonical payload 的 sha256（mask/引用以 digest 入身份）；
   - `to_prompt_spec(transform, raster_shape)`：地图 CRS → 像素（复用
     foundation 仿射口径）+ polyline/polygon/reference-layer → prior mask
     （rasterio.features 栅格化，确定性）→ PromptSpec + 转换审计
     （每几何的 roundtrip 误差，超容差 typed 拒绝）；
   - 容差契约：`PROMPT_ROUNDTRIP_TOL_PX = 1e-6`（仿射精确数学的浮点余量）
     + 半像素网格对齐语义文档；
   - fail-closed：crs 声明但 transform 缺失 → PlanningError；mask sidecar
     digest 不匹配 → typed 拒绝。
2. 词表 additive：`PROMPT_POLYLINE`/`PROMPT_POLYGON`/`PROMPT_REFERENCE_LAYER`
   进 PROMPT_MODES + DESCRIPTOR_V1_RULES 静态断言同步（closed-vocab 纪律）。
3. `SCHEMA.md`：artifact JSON schema + 字段语义 + 版本化规则。
4. 测试：`tests/unit/modelops/test_geo_prompt.py`（构造校验/身份稳定性/
   known-answer 往返容差/polyline/polygon 栅格化 known-answer/fail-closed）。

## Phase 2 — 执行能力 I（WP-C 深化 + WP-D）【G4/G5】

1. `app/lib/modelops/candidates.py`（新）：`MaskCandidateSet`（N 候选 +
   quality scores + 来源标注 + 选择策略 best|index|threshold）契约。
2. promptable_reference：确定性 3 候选（tight 区域生长 / relaxed 容差 /
   box-fit）+ 启发式质量分（显式声明为启发式）；engine `_run_promptable`
   消费候选集（默认 best；`return_candidates` 时发布全部候选 GeoJSON+分数）。
3. `app/services/modelops/embedding_cache.py`（新）：
   - key = sha256(model fingerprint, provider semantic_version, asset content
     sha, preprocess payload, grid identity, window, embedding 语义版本)；
   - 磁盘 LRU（entries+bytes 双上界，`MODELOPS_EMBED_CACHE_*` 旋钮，env/
     .env.example/conftest parity）；原子写（tmp+os.replace）；Windows
     unmap-before-delete 纪律；entry digest 校验（不匹配=miss+驱逐）；
   - `invalidate(prefix 条件)`（按 model/asset 部分失效）；`stats()`；
   - engine embedding 路径逐 window consult（hit=跳过 provider infer，
     resume 语义）；miss 回填。
4. 测试：候选契约/参考候选 known-answer；cache hit/miss/失效/resume/
   digest 篡改/上界驱逐/原子性（失败不留残件）/并发。

## Phase 3 — 执行能力 II（WP-E：多模态与文本提示）【G6】

1. `app/lib/modelops/multimodal.py`（新）：
   - `TextEncoder` 协议（text → 单位长度向量；semantic_version）；
   `SEMANTIC_MAP_STUB` 显式语义版本（哈希编码，仅测试/离线参考，manifest
   如实标注 stub）；
   - `ClassPrototypeIndex`：类名 → 原型向量；`zero_shot_map(chip_embedding,
     prototypes)` = 余弦 + top-k；**无 encoder wire = typed refusal
     （MultimodalUnsupported），绝不假成功**；
2. service 面：`semantic_class_map()`（注册原型 + 映射查询）；
3. promptable text 路径接 seam：descriptor/provider 声明 text_prompt 时可
   携带 text（现状 stub 保留为参考 provider 行为）；
4. 测试：typed refusal（无 encoder）、stub encoder 确定性、原型映射
   known-answer（构造正交向量）、fingerprint 稳定性。

## Phase 4 — Surface（WP-F/G）【G7/G8】

1. `app/tools/geoai_tools.py`（新，注册进既有 ToolRegistry 挂载点）：
   - `geoai_prompt_artifact_inspect`（只读：身份/CRS/几何统计/校验结果）；
   - `geoai_run_promptable_v2`（接受 artifact JSON/引用 + return_candidates）；
   - `geoai_prompt_refine`（候选 index → 以该候选为先验的精化重跑）；
   - `geoai_embed`（embedding run + cache 命中统计）；
   - `geoai_semantic_class_map`（原型注册/查询；无 encoder = typed 拒绝透传）。
2. 能力词汇 append-only：核对 8 个 model_* id 后按需新增（如
   `model_promptable_segmentation` / `model_embedding_search` 不与既有撞义）。
3. `app/api/routes/geoai.py`（新，自包含）：POST /geoai/prompt-segment、
   GET /geoai/models、POST /geoai/embed、GET /geoai/prompt/{id}（只读面，
   复用 service；owner scope 从会话推导）。
4. `frontend/components/geoai/`（新）+ 路由挂载：点/框提示绘制、候选掩膜
   预览、接受/撤销（本地栈）、批次队列（提交状态机）；**不改
   `frontend/components/map/**`**；遵循 frontend 既有测试模式。
5. 文档：`docs/adr/0197-geoai-promptable-foundation-platform-v11.md` +
   `docs/geoai/prompt-artifact.md`（用户/agent 契约文档）；CHANGELOG
   append（integration commit）。

## Phase 5 — 硬化（WP-H 前半）【G9】

- tile seam 回归（prompt 锚定窗口跨 seam 的几何一致性）；
- 100k prompt 逻辑规模（mock provider 计数 + 上界断言，env opt-in）；
- cancel/OOM/超时在新路径逐点；cache 并发/崩溃残件清理；
- Unicode 路径 / read-only 源 / NoData 语义 / atomicity 全负路径。
- `PERFORMANCE.md` + `FAILURE_MATRIX.md` 落盘。

## Phase 6 — E2E 验证（WP-H 后半）

- 合成几何 known-answer 全矩阵（点/框/多边形/参考层 × NoData × 多窗口）；
- provenance replay（artifact → 重放 → 指纹/结果一致）；
- provider absence 全 typed-refusal 矩阵；
- 生成物 drift gate（新 sidecar 不入 git、cache 目录默认路径在 registry_dir
  下且测试用 tmp_path）。

## Phase 7 — 独立 adversarial review

- ≥3 个只读 reviewer subagent（架构/数值科学/并发生命周期 + 测试可信度），
  从 `origin/master...HEAD` 全 diff 开始；
- P0/P1 全修 + 重跑 targeted gate；P2 修或逐条 disposition；REVIEW_LOG.md。

## Phase 8 — 收口

- `git diff --check` / 冲突标记 / secret 扫描 / 生成物 zero-diff；
- 关键 targeted suites **连续两遍**通过；
- rebase origin/master → push → `gh pr create`（不 merge，不等 CI）；
- PR_BODY.md 完整化。

## 风险与对策

| 风险 | 对策 |
|---|---|
| 词表扩展破坏 closed-vocab 静态断言 | 同步 DESCRIPTOR_V1_RULES；先跑 modelops unit gate |
| Windows 文件句柄（cache .npy 删除） | memmap 显式 del+close 后再 replace/delete；单测覆盖 |
| UI 与 map 组件耦合 | 自包含面板 + 新 route；不 import map-panel 内部件 |
| 候选/质量分被误当真实模型质量 | 契约显式 `heuristic` 标注 + 文档 + manifest 字段 |
| 并发 PR #1335 合并 | 文件无交集；rebase 即可 |
