# INTERACTION_REPLAY_GOLDENS — 交互回放金标（G05-SITUATED-WORKBENCH）

> 版本：1.0（2026-11-08）。金标语料位于
> `tests/unit/gis_harness/interaction_ir/replay_corpus/`，由
> `tests/unit/gis_harness/interaction_ir/test_replay_corpus.py` 执行：
> **与生产摄入共用 `apply_event`/`project_interaction_snapshot`**（非平行
> 实现），每个场景双跑逐位一致（确定性：无 wall-clock/随机/LLM）。

## 金标清单（17 场景）

### 范围正确性族（本 PR 新增，SW-02/#2004）

| 语料 | 场景 | 关键断言 |
|---|---|---|
| `box_select_20_range_faithful` | **J1 核心**：框选 20 所学校（成都视口） | 投影 selection 保留全部 20 id + `matched_count=20` + `ids_truncated=false` + `bbox` —— UI 反馈与 Agent 事实一致 |
| `box_select_80_truncated_disclosed` | 框选 80 要素 | 50 样本 + 服务端派生 `ids_truncated=true` + `matched_count=80` 全量披露（截断可披露而非静默） |
| `chart_categories_20_aligned` | 图表 24 类别选择 | 投影 chart_selection.categories = 前 20（与 selection-store ≤20 对齐；修复前截 8） |
| `no_id_layer_box_count_only` | 无稳定 id 图层框选 | count-only feature_selected 被接受且 selection 携带 matched_count=23 + bbox（修复前零上行=模型失明） |

### 既有基线场景（#1974 落地，本 PR 回归通过）

| 语料 | 场景 |
|---|---|
| `pan_zoom_storm` | 500 次 pan/zoom 风暴 → 单一 settled 事实 |
| `select_hide_agent_continues` | 用户选择+隐藏，Agent 续作（user-wins 共存） |
| `two_tabs_reorder_visibility` | 双标签页 reorder+visibility（水印隔离） |
| `session_switch_fresh_instance` | 切会话新实例（J7 隔离面） |
| `old_event_after_new_generation` | 新代后旧事件迟到（stale 丢弃） |
| `reconnect_duplicate_batch` | 重连重复批次（幂等 duplicate） |
| `gap_disclosed` | 批量跳号（accepted_with_gap + gap_from） |
| `mutation_rejected_superseded` | mutation 被拒/superseded → attempted_not_applied（不冒充成功） |
| `privacy_payload_stripped` | 白名单外键剥离 |
| `draw_aoi_edit_stale` | AOI 画→析→编（active_aoi 最新；correlation 链保留） |
| （ring_bounded_128/dropped_372/single_viewport_recent 于 test_replay_corpus 不变量） | 环淘汰/dropped 披露/视口合并 |

## 链路级金标（范围披露贯通，非语料形式）

`tests/unit/gis_harness/interaction_ir/test_selection_range_disclosure.py`：

- normalize：20 保真 / 60→50+truncated / 服务端覆写客户端申报 /
  宽预算承载 50×32 id 集 / 通用 8 纪律回归 / matched_count 有界收敛。
- 全链：ingest→投影→`derive_claimed_scope` 20 id 保真；60→50+披露；
  bbox 谓词进声称面。
- `tests/test_gis_situation_selection_history.py`：史代 12 完整保留
  （修复前截 8）；52→50+matched_count=52+ids_truncated。

## 回放方式

```bash
.venv/bin/python -m pytest tests/unit/gis_harness/interaction_ir/test_replay_corpus.py -q
# 29 passed（17 场景 × 双跑确定性 + corpus_present）
```

新增金标纪律（ADR-0227）：每个新交互语义必须先加语料（events + expect
部分匹配）再动实现；双闸（backend pytest ≡ manifest JSON ≡ frontend
contract-manifest.ts）锁词表/上限漂移。
