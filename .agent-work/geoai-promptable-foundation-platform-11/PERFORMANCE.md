# PERFORMANCE — 资源模型与逻辑规模（Platform 11）

原则（GOAL）：不把 wall-clock 当 correctness gate；规模证据 = 内存上限、
操作数/队列上限、逻辑规模与可复现 invariant。

## 资源模型（新增路径）

| 组件 | 界 | 证据 |
|---|---|---|
| GeoPrompt 编译（栅格化先验） | `MAX_COMPILED_MASK_PIXELS = 256M px`（≈256MB bool）；sidecar/reference 满幅读取前像素上限检查（services 层）+ 编译后终检（lib 层）双门 | tests/unit/modelops/test_geo_prompt.py |
| promptable 掩膜画布 | 既有 256M px 界（canvas 路径）不变；候选只发 GeoJSON（每窗几何），不作物化 K 份画布 | engine._run_promptable |
| mask 候选 | K ≤ 4（MAX_MASK_CANDIDATES）；分数 float32；provider 侧 validate_for 预算门 | candidates.py + base.py |
| embedding cache | 条目/字节双上界 LRU（MODELOPS_EMBED_CACHE_*；默认 256 条/1GiB/单条 64MiB）；sidecar JSON ≤ 数百字节/条 | embedding_cache.py + tests |
| 语义映射 | 类别 ≤ 512（MAX_SEMANTIC_CLASSES）；top_k ≤ 16；向量 L2 归一 | multimodal.py |
| refine 组合 | 候选 GeoJSON → 单张 uint8 mask（H×W）→ sidecar；空掩膜 typed 拒绝 | service.run_prompt_refine |
| HTTP preview | 最长边 ≤ 512px（分位拉伸 → RGB PNG）；GDAL IO 全部 to_thread | routes/geoai.py |
| UI 面板 | SVG 覆盖层（无 canvas 位图缓存）；候选多边形 = 服务端 GeoJSON 原样投影 | geoai-panel.tsx |

## 逻辑规模（bounded logical scale）

- prompt 数：每类 ≤ 64（artifact 与 PromptSpec 同口径）；候选请求批内逐窗
  传递（窗口数受既有 MAX_TILES_PER_RUN=65536 与 prompt 窗口策略约束）。
- embedding：tile 循环逐批；cache 命中跳过读取+推理（IO 与计算同时有界）；
  100k 逻辑规模测试见 Phase 5 测试矩阵（mock 计数 provider + 上界断言，
  env opt-in）。
- 并发：EmbeddingCache 进程锁 + 原子发布；并发 put/get 测试（4 线程 × 20）。

## 实测观测点（manifest/perf）

- `performance.embed_cache_hits/misses`（逐窗）；
- `performance.cache_hits/misses`（整 run reuse）；
- `postprocess.prompt_prior_digest/prompt_artifact_id`（复用身份）；
- `GET /api/v1/geoai/status`（embedding cache entries/bytes/hits/evictions/
  digest_failures + encoder 能力）。

## 本机基线（Windows，本地 venv；非 gate，仅参考）

- `tests/unit/modelops + tests/integration/modelops`：322 passed, 9 skipped（~2.5min）；
- `frontend vitest run`：全量通过（chat-tab.render-scope 在全量负载下偶发
  超时，单跑稳定通过——pre-existing，与本 diff 无关）。
