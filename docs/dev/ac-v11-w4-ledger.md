# AC-V11 W4 交付台账（标注深化）

> 波次:W4 · ADR-0164 · 状态:已完成 · 回滚点:tag `ac-v11-w4`

## 交付清单（任务 → 文件 → 测试 → 证据）

| # | 任务 | 文件 | 测试 | 证据 |
|---|---|---|---|---|
| W4.1 | C3 LabelPlan 定稿 | `label_plan.py`（+LabelCollisionConfig/LabelTypography/LabelStrategy 扩展/build_label_spec 追加两 key） | `test_label_c3.py`（spec 形状 + 模型缺省 + 回滚开关，2 例） | V10 序列化形状不变（默认 None） |
| W4.2 | 交互侧网格碰撞 | `frontend/lib/mapspec-runtime/label-grid.ts`（新：solveGridCollision/盒估算/格网索引） | `label-grid.test.ts`（7 例：**重叠率下降 ≥40% 硬断言**/确定性/序无关/空文本/盒口径） | MapLibre 兜底保留（叠加层） |
| W4.3 | 专业排版 | `label_typography.py`（+wrap_label_multilingual/polygon_label_point） | `test_label_c3.py`（5 例：按字/按词/不丢词/混排/内接圆） | 引线=engine callout、沿线=等弧长站点（V10 既有，映射登记） |
| W4.5 | 前端字段兜底 | `label-grid.ts`（pickLabelField + FALLBACK_FIELD_VOCAB） | 3 例（degraded 标记/满值优先/null 诚实） | 后端缺省时 C3 策略自选 |
| W4.6 | 性能预算 | 后端 `solve_labels` 计时 | `test_label_c3.py` 参数化（10k<10s / 50k<60s，实测 ~0.5s/~3s） | 浏览器端到端预算归 W8 |
| — | C3 spec 消费接线 | `normalizeLabelStrategy` 读取 collision/typography | — | W6 渲染收敛时接线（台账登记） |

## 验收对照（任务书 W4 验收项）

| 验收项 | 状态 |
|---|---|
| 交互侧网格碰撞生效，重叠率下降 ≥40% | ✅ |
| 三类专业排版各有 golden | ⚠️ 单测全覆盖；跨语言 golden fixture 待 W6（诚实登记） |
| 多语言断行中英混排用例 | ✅ |
| 前端兜底路径有测试 | ✅ |
| 50k 标注性能达标 | ✅ |

## 数值（与 W3 对照）

| 指标 | W3 | W4 |
|---|---|---|
| 交互侧避让 | MapLibre 内置贪心 | +确定性网格（重叠率 -≥40%） |
| 断行 | 宽度贪心（无词边界） | +拉丁按词/不丢词/auto |
| 面内标注 | 质心/representative_point | +最大内接圆圆心 |
| 50k 标注求解 | 无预算 | 实测 ~3s（预算 60s） |

## 遗留与移交

- `normalizeLabelStrategy`/runtime 消费 C3 新 key（应用 text-offset/断行指令）
  → W6（渲染收敛）。
- 三类专业排版的跨语言 golden fixture → W6。
- 交互侧碰撞与 W5 版面自愈的优先级模型共享（importance×class×area）→ W5。
