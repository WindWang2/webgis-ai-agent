# Harness V6 Progress Log

## Wave 状态

| Wave | 内容 | 状态 |
|------|------|------|
| W1 | 只读审计 + baseline/架构文档 | ✅ commit 8c70a453 |
| W2-W4 | Semantic Retrieval V6（hybrid + 语料 358 + confidence/abstention + 生产接线） | ✅ 本提交 |
| W5 | Durable Context 三分层 + recovery_state + 锚点 additive | ✅ a2374b5a |
| W6 | Persistent Recovery Ledger（flock/write-through/成功回写/resume 续接） | ✅ a425136c |
| W7 | Trace Store V6（分段/gzip/增量读/汇聚接口/torn-tail 自愈） | ✅ ee53b02d |
| W8 | Long-horizon continuation 裁决点 + runtime_repair 接线 | ✅ a2374b5a |
| W9 | Subagent budget class（交集 + token 闸） | ✅ ce93cb37 |
| W10 | Observation 状态阶梯 + map_product.observation_health | ✅ 50618c2f |
| W11-W12 | chaos corpus 16 条 + kill -9 锁释放等不变量 | ✅ 38aff03f |
| W13-W14 | perf 结构预算 + ADR-0119 + CHANGELOG | ✅ 本提交 |

## W2-W4 实施记录（关键决策与测量）

### 信号层（app/services/chat/semantic_retrieval.py）
1. **双语扩展词表**（~150 键）：zh 子串 / ASCII 词边界匹配 → 扩展词与
   原查询分开跑词法再 0.5 降权融合（base 分不被扩展词污染）。
2. **capability 别名**（~90 短语 → 139 能力词表的精确 id）：进程内 memo
   （键 = registry 身份 + 算法数；all_ids 兼容 property/callable）。
3. **方法论证据**：query 与 12 方法族路由词命中 → 族内方法按声明
   priority 排序（首选 ×1.0 / 次选 ×0.6 / 其余 ×0.4）→ capability/
   algorithm → 工具加成 ≤3.0；只作检索信号，不建第二 planner。
4. **否定反证**：没有/无/不需要 X → X 域词（含否定专用扩展表，时间类
   只在否定通道展开——正向展开实测污染 EV-N02a/H09）→ 命中候选 -3.0
   （只降不剔）。
5. **embedding 检索器**：`TOOL_RETRIEVAL_SEMANTIC` 默认指向
   `semantic_retrieval:embedding_retriever`（sentence-transformers 复用
   RAG 配置，registry 全量 descriptor 指纹缓存索引，模型失败进程级
   memoization）；`TOOL_RETRIEVAL_EMBEDDING=0` 关停。评测钉死
   `_semantic=None` 保确定性。

### 词法打分判别力修复（tool_retrieval.py）
- **根因**：单字 CJK 命中（给/图/层…）造成 16+ 分噪声地板。
- 修复：封闭停用字表（~40 功能字）零权重；其余单字 token 命中
  ×0.25；anti 负证据只认多字 token。V3/V4-off/V4-on 同一循环 →
  一致性契约不变；金标门是 ≥ 阈值 → 改善方向天然兼容。

### 声明式负证据（13 个 descriptor 文件 +22 条 anti_examples）
- 易混淆兄弟工具对（fetch_sentinel↔cloud_qc_basic、buffer↔
  service_area、geocode 单/批量、resample↔reclassify、ripley↔
  cluster、快照↔检查点等）按 descriptor.anti_examples 既有机制声明
  「已知误用模式」—— 检索负证据的第一等机制，非 case 硬编码。

### 语料（retrieval_eval_corpus.py 66 → 358）
- 新增 direct 178（zh 150 + en 25 + mixed 8）/ 近重复 20 对 /
  hard_negative 40 / ambiguous 20 / out_of_scope 14。
- 金标修订（诚实记录在 `_build_v6_additions` docstring）：V5 部分案例
  valid 集过窄 → 放宽到语义等价工具（isochrone_analysis /
  multi_ring_buffer / detect_vegetation_change / webgis_view_set 等），
  must_not 陷阱语义不变。
- 撤下 3 个 CORE 结构性不可达案例（开环口径剥离 CORE 常驻工具）。
- 修正 2 个语义错位查询（rate_smoothing=经验贝叶斯率平滑非时序平滑；
  mad_change=两期影像 MAD 非传感器序列）。

### 置信度/弃权（D2）
- confidence = 0.5·level + 0.35·margin + 0.15·coverage（各自 [0,1]）；
  ABSTAIN_THRESHOLD=0.35。
- 分布测量：hit 均值 0.687 > miss 0.614 > oos 0.510（单调可分但重叠
  大）；0.35 阈值下 oos 弃权 21.4%、误弃权 0%、ECE 0.31。
- **诚实边界**：全局阈值弃权只能捕获 oos 尾部 —— 防乱选的主力在
  dispatch 校验 + finalizer evidence（W8/W10 强化），弃权是披露面。

### 生产接线（pi_native_surface.compute_turn_active_tools）
- abstained → 不注入动态面（native 前门 + list_available_tools 保持）
+ logger.warning 披露 + TOOL_SURFACE 链事件带 abstained/confidence。

### 测量（同语料 358 条对照）
| 指标 | V5 基线（V6=0） | V6 hybrid | Δ |
|------|----------------|-----------|---|
| p@1 | 0.4944 | **0.5587** | +6.4pp |
| r@5 | 0.6731 | **0.7523** | +7.9pp |
| r@10 | 0.7289 | **0.8059** | +7.7pp |
| invalid | 0.2500 | 0.2500 | 持平 |
| oos 弃权率 | — | 0.2143 | 新指标 |
| 误弃权率 | — | 0.0 | 新指标 |
| ECE | — | 0.3095 | 新指标 |

（V5 原 66 条语料上对照：p@1 0.6515→0.7879、invalid 0.3333→0.25。）

### 验证
- tests/unit/test_retrieval_eval_v6.py（8 gates，全实测钉线）
- tests/unit/test_semantic_retrieval_v6.py（10 单测）
- 相邻回归：test_tool_retrieval_v4 / v4_corpus / gis_trace_v3 /
  pi_native_surface / pi_dynamic_surface / tool_surface_v2/v3 全过；
  descriptor/registry/retrieval 相关 456 passed。
- ruff 全绿。
