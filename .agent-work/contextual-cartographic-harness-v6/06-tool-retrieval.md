# 06 — Tool Retrieval V6 设计（Wave 12）

## 现状（证据见 00-baseline §F）

- `TOOL_RETRIEVAL_SEMANTIC` hook 存在但默认空串 → 生产 lexical-only（✅ 已验证 tool_surface_v3.py:37-40）。
- 语料：`retrieval_corpus.py`（生成器，≥2000 条，capability→工具投影完备性口径）+ `retrieval_eval_corpus.py`（66 条人工金标，开环口径）。
- 钉门：p@1=0.65 / r@5=0.81 / r@10=0.87 / invalid=0.33（诚实基线，ADR D7）；`tier3_leak` 期望 0。
- 目标（§24）：P@1≥0.85、R@5≥0.95、R@10≥0.98、tier3_leak==0；达不到须诚实记录，不得调指标。

## 多阶段管线（registry 仍是唯一工具事实源）

```text
User Query → Task Ontology → Methodology Family（复用 methodology.py 12 族）
→ Capability Requirements → Semantic Retrieval + Lexical Retrieval
→ Candidate Union → Contract Hard Filter → Context-aware Reranker → Active Tool Surface
```

1. **Semantic retrieval 生产接线**：实现项目内可控 retriever（`app/services/chat/` 内新模块），经既有 `TOOL_RETRIEVAL_SEMANTIC="module:callable"` hook 默认接线：
   - 离线 embedding index（sentence-transformers/faiss 已在依赖中），启动或首次用时构建，descriptor/capability 文本源自 registry（派生索引，非第二事实源）
   - 无模型/索引构建失败 → 确定性退化 lexical（现有 `rank_tools`），系统行为不变
   - 可离线测试：index 构建与检索零网络
2. **Hybrid union + Contract Hard Filter**：semantic top-k ∪ lexical top-k；硬过滤复用现有契约（输入 artifact 类型/tier/side effect），tier3 不得入面（tier3_leak 守恒）。
3. **Context-aware Reranker**（§21，不只基于 query string）：输入 workflow stage、当前 DAG node（W1 投影）、input artifact 类型、期望输出 artifact、geometry type、CRS、data scale、runtime budget、side effects、determinism、latency、memory、prior failures、recent successful tool、user intent、当前 MapSpec 状态。实现为确定性打分（特征加权，权重写死可测），LLM 不参与排序硬决策。
4. **语料扩展（§23）**：`retrieval_eval_corpus.py` 人工金标 66 → ≥300（目标 500），覆盖 zh/en/mixed、direct、near-duplicate、ambiguous、hard-negative、multi-intent、follow-up、pronoun/contextual、workflow-continuation、failure-recovery、map-edit、cartography、analysis、remote sensing、raster、vector、network、statistics。新条目必须人工设计语义（不是模板生成灌水），金标与生成语料分源保持。
5. **指标（§24）**：`retrieval_eval_report` 扩 MRR、fallback_rate、schema_byte_cost、wrong-domain-rate；报告 baseline（现钉门值实测）与 after，诚实记录；钉门值只在真实提升后上调。

## 验收

- 生产默认路径经 semantic/hybrid（hook 非空且有 fallback 测试）。
- 语料 ≥300 人工条目 + 评测报告（before/after）入库。
- `tier3_leak == 0` 保持；kill switch `GIS_TOOL_RETRIEVAL_V4=0` 与 semantic 禁用路径均 parity。
