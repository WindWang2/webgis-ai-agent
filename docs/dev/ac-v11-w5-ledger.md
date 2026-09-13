# AC-V11 W5 交付台账（版面与叙事自动化）

> 波次:W5 · ADR-0165 · 状态:已完成 · 回滚点:tag `ac-v11-w5`

## 交付清单（任务 → 文件 → 测试 → 证据）

| # | 任务 | 文件 | 测试 | 证据 |
|---|---|---|---|---|
| W5.1 | G1 备选版面接线 | `planner.py`（_composition_alternatives_evidence 投影缝 + 类目映射表 + template_selection 入账） | `test_layout_narrative.py`（生产接线 grep 断言 + 投影确定性 + fail-safe，3 例） | `select_composition_alternatives` 生产调用达成 |
| W5.2 | G2 自愈执行化 | `frontend/lib/layout/compose.ts`（四级链应用 + 安全网主动补全）、`export-chrome.ts`/`render-observation.ts`（`__autofill_*` 改名） | compose.test 22 例（executed 语义更新）+ layout 143 + mapspec-runtime 208 全绿 | `__fallback_*` 代码归零；渲染面真实改变 |
| W5.3 | 多图版面 | `app/lib/cartography/atlas_layout.py`（逐页 C2 IR + 共享 chrome + 截断披露） | 1 例（确定性/有界/fail-closed） | 每页 version=2 IR |
| W5.4 | StoryMap 大纲 | `app/lib/cartography/story_outline.py`（四幕确定性投影 + missing 诚实） | 1 例（完整/缺章） | 复用 /story 章节词汇 |
| W5.5 | 版面五维评分 | `app/lib/cartography/layout_score.py`（balance/density/whitespace/hierarchy/contrast 加权） | 2 例（方向锁定 + 确定性 + 空面诚实） | C4 观测行形态（ratchet 归 W8） |
| — | 优先级共享 | label-grid priority ↔ composition-repair hide_lowest_priority（importance 语义一致） | 既有测试覆盖 | W4/W5 交叉验证 |

## 验收对照（任务书 W5 验收项）

| 验收项 | 状态 |
|---|---|
| select_composition_alternatives 有生产调用（grep 断言） | ✅ |
| 自愈四级在 live 生效（非 planned） | ✅ |
| __fallback_* 归零 | ✅（老工件兼容读保留） |
| 多图版面与 StoryMap 各有端到端用例 | ✅（编排/大纲层；渲染端到端随 W6） |
| 版面评分入 ratchet | ⚠️ 评分器交付；ratchet 基线归 W8（任务书 W8 职责） |

## 数值（与 W4 对照）

| 指标 | W4 | W5 |
|---|---|---|
| 备选版面 | 无（纸面算法） | ≥3 候选 + 评分入 plan 证据 |
| 版面自愈 | planned 记录 | executed（渲染面真实改变） |
| 安全网 | `__fallback_*` 特批直插 | autofill 主动补全（id 规范统一） |
| 多页产物 | 无 | 逐页 C2 IR（≤20 页） |
| 版面质量 | 无度量 | 五维确定性评分 |

## 遗留与移交

- C2 IR / atlas / story 的前端渲染消费 → W6（三渲染器收敛）。
- 版面评分入 C4 事实库 + ratchet → W8 批次（含成本治理同批）。
- feedback_signal 的 chat 路由采集 → W7（自愈策略学习共用存储时同批接线）。
