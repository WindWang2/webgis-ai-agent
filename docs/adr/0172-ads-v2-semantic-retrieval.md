# ADR-0172: ads-v1 语义检索与候选排序（数据集卡片 + 混合检索 + 可解释排序）

- 状态: Accepted
- 日期: 2026-09-13
- 线: adaptive-data-supply/v1-master · DS2（自适应数据供给与接入 · 并行线 P1）
- 关联: ADR-0171（源注册表 = 卡片的事实源）、ADR-0170（D1 契约 = 卡片的输出面）、V11 intent_semantic（制图意图，本波不触碰）

## 1. 背景（缺口 A3）

「长三角近五年 PM2.5」这类自然语言取数请求无法被满足：`explorer/intent_detector.py`
只有静态关键词表（`EXPLORATION_TRIGGERS`/`SOURCE_HINTS`，非 LLM），没有
NL→候选数据集的检索层；目录（catalog）只支持按名字/ID 精确取。

## 2. 决策一：卡片（`retrieval/cards.py`）

检索的对象是**数据集卡片**（`DatasetCard`）——注册表声明（`config/sources/*.yaml`
的 `datasets:` + 源级 fallback 卡）与已知本地资产（`local_osm.THEME_SPECS` 四主题、
`local_yearbook` 乡镇统计表）的诚实投影。卡片携带：标题/描述/关键词/字段/数据类型/
bbox/时间覆盖/粒度/许可/新鲜度/verified/local/quota，`searchable_text()` 是确定性
拼接的检索文本；`to_d1()` 把卡片升级为冻结的 D1 契约（下游只消费 D1）。
本波随卡片扩充了 12 源的 datasets 声明至 36 个（全部为真实已知集合 ID / 门户检索
主题，声明式元数据，非伪造测量），连同本地资产卡共 44 张。

## 3. 决策二：混合检索——关键词为基，向量为可选增强（`keyword.py` / `embeddings.py`）

- **BM25 永远是基座**（任务书：禁止只做向量）：自实现 BM25（k1=1.5, b=0.75），
  分词 = ASCII 词 + 中文二字组（零依赖）；确定性（同查询同序，平分按 card_id 稳定排序）；
  声明关键词命中加权 1.6×。
- **embedding 可用时**才叠加语义信号：`EmbeddingProvider` 缝 + `register_provider()`
  进程级注册；默认提供者**复用既有 RAG 基建**（`FaissVectorStore.embed_texts`）。
  探测策略有界：仅当 `RAG_EMBEDDING_OFFLINE=1`（local_files_only，秒级失败不挂起）
  才惰性探测；否则 None → **keyword-only + `degraded=True` 诚实降级**。
  cosine 相似与 BM25 归一分按 `max(rel, 0.5·rel+0.5·sim)` 融合。

## 4. 决策三：可解释排序（`ranker.py`）

score = 0.55·relevance + 0.15·coverage + 0.10·freshness + 0.10·cost + 0.10·trust
（权重 **provisional**，DS8 用实测校准转定稿）。各因子归一化：
- coverage：bbox 相交 + 时间包含（未声明 = 中性 0.5，不奖励不惩罚）；
- freshness：按声明频率映射（continuous=1.0 … irregular/manual=0.5）；
- cost：本地资产=1.0（自然胜出，对接 DS7 本地优先），未知配额=中性 0.5；
- trust：verified=1.0 / 未验证=0.4（对接注册表 verified 纪律）。

**每个 hit 必须带因子分解（reason）与 confidence**；confidence < 0.5 →
`clarification_needed=True` + 中文提示——**禁止静默取第一个候选**（任务书 §0.5）。

## 5. 决策四：意图缝（A3 升级）与评测

- `IntentDetector.retrieve_dataset_candidates()`：取数意图 → 数据集候选（带理由/
  置信度）；检索层任何故障静默回退关键词表，`detect()` 的 <100ms 决策路径不变；
  **不触碰 V11 的 `intent_semantic`（制图意图），两域通过各自意图面并行存在**（§8.1.4）。
- **评测集 278 条**（`tests/data/ads2_retrieval_eval.json`，由
  `scripts/ads_gen_retrieval_eval.py` 确定性生成）：44 张卡 × 双语查询/同义改写/
  错拼变体，标注 = 卡片自身声明的 card_id（无臆造真值）。首轮实测
  **Recall@5 = 0.9748 / MRR = 0.9194**（阈值 0.80 / 0.55，provisional）；
  闸测试锁死阈值，DS8 同集复测并提标。

## 6. 后果

- DS3 计划编译器消费检索 top-k（每 hit 附 D1 可升级卡片 + 结构化过滤）；
- DS8 排序校准直接落 `ranker.WEIGHTS`（单点定义）+ 评测集提标；
- 检索索引随注册表 mtime 热加载自动重建；卡片/索引纯内存，无迁移。
