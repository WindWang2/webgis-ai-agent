# ads-v1 · DS2 交付台账（任务 → 文件 → 测试 → 证据）

> 波次：DS2 · 语义检索与候选排序 · ADR-0172 · 里程碑 M2（与 DS3 同车）
> 状态：**完成** · 2026-09-13

| 任务 | 文件 | 测试 | 证据 |
|---|---|---|---|
| 数据集卡片 | `app/services/data_fabric/retrieval/cards.py`（`DatasetCard` + `build_cards` + `to_d1`） | `tests/unit/test_data_fabric_retrieval.py::test_cards_build_*` | 44 张卡 = 12 源 36 个声明数据集 + 本地资产卡（local_osm 四主题/yearbook）；全部来自注册表声明与本地模块事实，零臆造 |
| 混合检索（关键词基座） | `retrieval/keyword.py`（BM25 k1=1.5 b=0.75 + 中文二字组分词，零依赖确定性） | `test_tokenizer_*` / `test_bm25_deterministic_*` | 同查询同序；声明关键词 1.6× 加权 |
| 混合检索（向量可选） | `retrieval/embeddings.py`（`EmbeddingProvider` 缝；默认复用 `FaissVectorStore.embed_texts`；探测仅限 `RAG_EMBEDDING_OFFLINE=1` local-only 模式，绝不发起网络） | `test_embedding_provider_cosine_helper` + degraded 断言 | 无 provider → `degraded=True` 诚实降级 |
| 结构化过滤 | `retrieval/service.py::RetrievalFilters`（bbox/时间/粒度/许可/类型/local_only/verified_only） | `test_structured_filters_exclude` | 粒度双声明才硬排；未知保持中性 |
| 排序模型（provisional 权重单点） | `retrieval/ranker.py`（`WEIGHTS` rel.55/cov.15/fresh.10/cost.10/trust.10） | `test_rank_card_breakdown_and_top_factor` / `test_rank_card_unverified_downweights_trust` / `test_coverage_bbox_*` | 因子分解进 reason；本地资产 cost=1.0 自然胜出；未验证源 trust=0.4 |
| top-k + 理由 + 置信度 + 澄清路径 | `retrieval/service.py`（`clarification_needed` < 0.5） | `test_retrieve_returns_reason_and_confidence` / `test_retrieve_never_silently_returns_weak_first_hit` | 弱命中必标澄清，禁止静默取第一 |
| A3 意图升级 | `app/services/explorer/intent_detector.py::retrieve_dataset_candidates`（关键词表保留为兜底） | `test_intent_detector_candidates_via_retrieval` / `test_intent_detector_detect_path_still_works` | 检索层故障静默回退，detect() <100ms 路径不变；不触碰 V11 intent_semantic |
| 评测集 ≥200 | `tests/data/ads2_retrieval_eval.json`（278 条）+ `scripts/ads_gen_retrieval_eval.py`（确定性生成，已 gitignore 白名单） | `tests/data/test_ads2_retrieval_eval.py`（3 闸测试） | **Recall@5 = 0.9748 / MRR = 0.9194**（阈值 0.80/0.55，provisional；44 卡双语/同义/错拼） |
| 卡片语料扩充 | `config/sources/*.yaml` datasets 增至 36 声明（真实集合 ID/门户检索主题） | registry lint OK | `verified:false` 纪律维持；英文别名随卡声明 |

## 波次验收对照（§6 DS2 行）

- [x] 200 条评测 Recall@5 ≥ 0.80 / MRR ≥ 0.55（0.9748 / 0.9194，首轮 provisional）
- [x] 每条结果带理由与置信度（因子分解 reason + confidence 字段，闸测试）
- [x] 低置信度澄清路径有测试（`clarification_needed` 闸）
- [x] 关键词兜底在无 embedding 时全绿（KeywordOnlyProvider 强制降级模式下 16 测全绿）

## 备注

- `retrieval/service.py` 的候选池 = BM25 top-30 → 排序 → top-k；索引随注册表 mtime 热重建。
- 英文查询对中文卡的漏检由「卡片声明英文别名」缓解（非靠臆造翻译层）；embedding 可用时进一步增强。
