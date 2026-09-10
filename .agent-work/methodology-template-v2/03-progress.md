# 03 — Progress Log（执行证据）

分支：`feat/gis-methodology-template-intelligence-v2`
worktree：`../webgis-ai-agent-methodology-v2`
基线：`origin/master @ 8a33e3a5`

## 已完成（commit 顺序 = 执行顺序）

| # | commit | wave | 内容 |
|---|---|---|---|
| 1 | de70c310 | 1 | ontology 纯加法 5 任务 + 13 族 proximity + 7 候选方法 + family corpus |
| 2 | 8c29468b | 2-7 | provenance + taxonomy(20) + descriptors(51) + graph(421/883) + 中央校验 |
| 3 | 4866a586 | — | ruff 清理 |
| 4 | bd283e25 | 8-13 | qualification engine（8 维四态）+ 硬错误锁定 |
| 5 | (fix) | — | ruff 清理 |
| 6 | e97aef86 | 14-15 | 方法级双语语料冻结（25 案例；先于 ranker 调参） |
| 7 | c9f999ce | 13-18 | ranker 池化 + abstention + 基准钉值 + descriptor keywords + V4 知识补齐 |
| 8 | cd00b19c | 17-23 | viz bridge + TemplateSpecV2 + planner + 中央校验扩展 |
| 9 | 3456bbb3 | 24-28 | service 门面 + component registry V2 + feedback + case corpus(22) |
| 10 | 69b52a32 | — | ruff 清理 |
| 11 | 39731704 | 28-32 | 预算测试 + feedback 测试 + ADR-0120 + CHANGELOG |

## 实测指标（25 案例冻结语料）

- recall@1 = 0.96，recall@3 = 1.0，recall@5 = 1.0
- MRR = 0.98
- invalid_selection_rate = 0.04（m.dens.rate_denom：kernel_surface 在
  top-5 但 gold rank-1；canonical 事实下 KDE viable，非 oracle 拒绝）
- ambiguous valid-top1 = 1.0
- lexical_baseline_rescued 披露启用

## 端到端（DoD）

22 案例 × 全链（classify→qualify→rank→template→skeleton/render intent）；
≥5 场景 DoD 断言全绿；平行不变性 / 乱码弃权 / 反硬编码锁定。

## Registry gap（记录不改）

- `spatial.kde.surface` 未声明资源硬闸（max_features_hint/envelope）——
  大层资源裁决待算法层补声明（follow-up candidate）。
