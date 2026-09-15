# Proactive Spatial Memory Engine — 工程规格（ADR-0190）

状态：与 ADR-0190 同步演进。本文是实现规格（接口/算法/常量/测试矩阵），
ADR 记决策与理由。

## 1. 组件与数据形状

### 1.1 `associative_index.py`

```python
@dataclass(frozen=True)
class MemoryIndexEntry:
    memory_id: str          # gis_spatial_memories.id
    org_id: str
    scope: str              # session | project | user
    scope_id: str
    kind: str               # contract 10-kind
    family: str             # 四类视图族（CARD_FAMILIES 映射）
    subject: str
    name: str               # value.name 或 subject（展示名）
    tokens: tuple[str, ...] # 索引词元（CJK bigram + ascii 词）
    bbox: tuple | None      # (minx, miny, maxx, maxy)
    confidence: float
    pinned: bool            # explicit_user_* 来源 = True（半衰期∞、不淘汰）
    last_validated_at: float  # epoch 秒
    hit_count: int = 0      # 被检索命中次数（retriever 回写）

class AssociativeIndex:     # 线程安全（RLock）
    def upsert(entry) -> None
    def remove(memory_id) -> None
    def search(query_text, *, bbox=None, kinds=(), limit=8,
               now=None) -> list[IndexedHit]
    def touch(memory_ids) -> None     # 命中计数
    def entries() -> int; def orgs() -> int
```

### 1.2 评分算法

```
score = 0.45·sem + 0.25·geo + 0.18·decay + 0.12·prior
sem   = bm25(query_tokens, entry) 归一化到 [0,1]（除以该查询可达最大值；
        纯空间查询无词元命中时 sem=0）
geo   = bbox_proximity(query.bbox, entry.bbox)：交叠归一 IoO
        (inter/area_q)，无交叠时中心距衰减 1/(1+d_deg)；任一缺 bbox → 0.5
        （无空间信息不奖不罚）
decay = 2^(−age_days/half_life)；half_life 默认 14d；pinned → decay=1.0
prior = kind_base_weight(kind)/1.2 · confidence · scope_pri
        （session 1.0 / project 0.7 / user 0.55）
```

- BM25：k1=1.5，b=0.75；IDF = ln(1 + (N−df+0.5)/(df+0.5))，索引内计算。
- 词元化：CJK 字符切 bigram（单字 fallback 保留单字查询），ASCII 按词
  小写；`tokenize(text)` 是公共函数（检索与索引同源，不对称即 bug）。
- 排序确定性：`(-score, scope, subject, memory_id)` 字典序。

### 1.3 `proactive_retriever.py`

```python
@dataclass(frozen=True)
class MemoryContextCard:
    card_id: str            # memory_id
    family: str             # spatial_entity | crs_preference |
                            # field_semantic | strategy_heuristic
    title: str
    summary: str            # ≤160 字符有界摘要（含 bbox/CRS 等关键事实）
    resolved_place: dict | None   # {"name","level","bbox"} 仅空间实体族
    bbox: tuple | None
    scope: str; confidence: float; score: float
    reasons: list[str]; refs: list[str]; pinned: bool

@dataclass(frozen=True)
class AwakeResult:
    query: str; cards: list[MemoryContextCard]
    resolved_place: dict | None    # 唯一高置信空间实体卡才填充
    signals: list[str]             # vague_reference / pinned_preference …
    latency_ms: float; trace: dict

class ProactiveRetriever:      # 进程级单例 default_proactive_retriever
    def awake(query, *, org_id, user_id=None, session_id=None,
              project_id=None, bbox=None, now=None, limit=6,
              db=None) -> AwakeResult
    def resolve_place(query, *, org_id, …) -> dict | None
    def render_cards(cards, *, char_budget=900) -> str   # [GIS_MEMORY_PROACTIVE]
    def sync_org(org_id, db) -> int     # store → 索引（staleness 窗口）
```

- **消歧判据**：`resolved_place` 仅当 top1 为 spatial_entity 族、
  `score ≥ 0.62`、且 `score − runner_up ≥ 0.08`（唯一性余量）时返回；
  否则 None（交给澄清，绝不静默赌博）。
- **模糊指代词表**（`vague_reference_signals`）：上次/之前/刚才/那个/该/
  本市/本区/我区/我县/我司/我们/园区/同一/原/还是/继续/仍然/开发区。
  命中即 `signals += ["vague_reference:<词>"]` 并放宽 sem 权重不达时的
  geo 兜底（纯 geo 命中分数上限 0.75，防止纯位置撞车）。
- **fail-closed**：`org_id` 为空 → 空结果（绝不检索）。
- **staleness**：每 org 记 `last_sync`；`awake` 时超窗（默认 30s）先
  `sync_org`（db 缺省用 queries 的会话工厂；拿不到 db → 用现有索引，
  记 `trace["stale"]=True`）。

### 1.4 `memory_consolidator.py`

```python
async def consolidate_session(session_id, *, org_id, user_id=None,
                              project_id=None, db_factory=None) -> dict
# 返回 {"promoted": int, "pinned": int, "considered": int}
```

- 晋升条件（全部满足）：session 记忆 `hit_count ≥ 2`；证据 ∈
  {review_passed, tool_result, explicit_user_decision,
  explicit_user_correction, intent_resolution}；目标作用域身份可用
  （user 作用域必须 user_id）。
- 去向：习惯类（crs_resolution / provider_failure / successful_strategy /
  user_cartographic_preference）→ user；实体类（resolved_place /
  boundary_ref / dataset_semantics / field_role）→ project（无 project
  则不晋升，不造孤儿作用域）。
- 固化：`evidence.source ∈ explicit_*` 的 active 记忆 `expires_at → NULL`
  （pin），`invalidation_rule → manual`。
- 晋升 = 以新 `MemoryWriteRequest`（scope=user/project）走
  `store.record_memory`——过写入门，弱证据照样拒；`value.consolidated_from`
  记来源审计。原 session 行不动（到 TTL 自然失效）。
- 全程 fail-safe：异常记日志返回 `{"promoted": 0, ...}`。

## 2. 挂载点

### 2.1 `gis_situation/turn_context.py`

- `build_situation_turn_context(..., query_text="")` 增加 query_text；
  投影后 best-effort 追加 `render_cards(...)`：
  身份 `read_memory_identity(session_id)` → 无 org 无切片；
  `default_proactive_retriever.awake(...)`；任何异常 → 只返回 situation
  投影（fail-open）。
- 单一注入通道：chat.py 既有 `[GIS_MEMORY]` passive 块不动；主动切片
  块名 `[GIS_MEMORY_PROACTIVE]`，互不重复渲染同一 memory_id
  （切片侧按块头声明为主动先验）。

### 2.2 `gis_harness/intent.py`

```python
def apply_proactive_memory_hints(intent, cards, *, query="") -> MapRequestIntent
# 纯函数：cards 给定则输出确定。回填 scope + 审计 + 0.9 折扣。

# resolve_intent_adaptive 增参：
#   org_id: str = "", user_id: str = "", use_memory: bool = True
# use_memory 且 org_id 非空时：best-effort awake（session_id 已在签名）→
# apply_proactive_memory_hints；任何异常静默跳过（fail-open）。
```

回填规则（确定性）：
1. `intent.scope.name` 非空（fresh 已解析）→ 不覆盖，仅记 evidence；
2. 存在 `resolved_place`（AwakeResult 判据通过的空间实体卡）→
   `scope = ScopeIntent(name, level)`（level 从 value.level 映射，未知 →
   "unknown"），`matched_rules += "memory_proactive_scope:<subject>"`，
   `assumptions += "依据历史记忆将范围解析为 <name>（可纠正）"`，
   `confidence ×0.9`；
3. CRS/字段语义/策略卡不改动 intent 结构，仅写
   `intent_evidence["proactive_memory"]["families_present"]` 供 harness
   消费（避免 intent 承载非其职责的记忆内容）。

## 3. 测试矩阵（TDD 验收）

| 组 | 用例 | 锁定红线 |
|---|---|---|
| A 索引 | tokenize 对称；BM25 相关性序；bbox 邻近序；半衰期单调；pinned 不衰减；确定性排序；O(postings) 亚毫秒（1000 条 < 1ms 热查询，宽松 CI 上限 50ms） | 打分可解释（每条 hit 带 reasons） |
| B 租户隔离 | org A 写、org B 查 → 空；A 的 AOI 绝不进 B 的 awake/resolved_place/卡片/索引遍历；query 含 A 地名 + org B → 零命中 | **100% 阻断，三通道**（awake / resolve_place / sync_org） |
| C 消歧 | 「上次看的那个地块」+ session 记忆 → resolved_place 命中；「本市开发区」+ user 记忆 → 命中；两个同分候选 → None（不赌博）；fresh scope → 不覆盖 | 唯一性余量 0.08 |
| D 生命周期 | hit_count≥2 + 强证据 → 晋升 user/project；显式偏好 → pin（expires_at 清空）；过期 + 零命中 → 失效不召回；弱证据晋升被写入门拒绝 | 晋升非旁路 |
| E 挂载 | turn_context 切片追加/无身份无切片/异常 fail-open；intent hints 回填 scope + 审计 + 折扣；resolve_intent_adaptive(use_memory) 端到端；纯函数 resolve_map_request_intent 行为不变 | 既有调用零破坏 |
| F 渲染 | 卡片块字符预算、xml_fence、sensitive 不出现 | 纵深防御 |

## 4. 性能与有界性

- 索引条目/org ≤1024（LRU；store 预算最坏同步 ≈680 行，留余量）；
  卡片 ≤6/次；切片 ≤900 字符；
- 实测（2026-09-15，Windows/Py3.13）：每候选 ≈3µs——候选 ≤300 时单次
  search <1ms；退化语料（1000 条共享前缀全命中）≈3.5ms；CI 宽松上限
  50ms（`test_hot_query_latency_loose_ci_bound`）；
- 数字串 token 任意长度保留（「地块编号1」vs「488」的判别信号），
  1000 条共享前缀语料下 top-1 判别有测试锁定；
- 所有常量模块级可调（测试可注入 now/db_factory，禁 freezegun 依赖）。
