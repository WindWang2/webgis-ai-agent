# ADR-0164: V11 W4 — 标注深化（C3 LabelPlan 定稿、交互侧网格碰撞、专业排版、字段兜底、性能预算）

- 状态: Accepted
- 日期: 2026-09-13
- 线: adaptive-cartography/v11-master（W4）
- 关联: ADR-0160（label_typography 单点）、ADR-0126/0154（标注避让分档/label_plan）、audit F31（setData 跳过）

## 1. 背景与靶心

缺口 G4/G5：标注双实现（W0 已收敛原语）与**交互侧无网格碰撞**（仅导出孪生
`label-solver.ts` 有 Grid；`label-layout.ts` 委托 MapLibre 内置避让）。
W4 把碰撞能力上提到交互侧，并补齐专业排版与字段兜底。

## 2. 决策一：C3 LabelPlan 定稿（只加不改）

- `label_plan.py` 增 `LabelCollisionConfig`（strategy: **grid**｜maplibre ——
  maplibre 即 V10 行为回滚开关；grid_cell_em / max_displacement_em /
  suppress_overflow）与 `LabelTypography`（wrap_mode: cjk_char｜latin_word｜auto、
  max_chars、max_lines、halo_mode 沿用 V10 P5）；`LabelStrategy` 增两个
  Optional 字段（默认 None = V10 形状不变）。
- `build_label_spec` 产物追加 `collision` / `typography` 两 key（缺省值语义
  等价 V10；前端 `normalizeLabelStrategy` 未升级前忽略亦安全）。
- 交互侧消费（`normalizeLabelStrategy` 读取新 key + runtime 应用）随 W6 渲染
  收敛接线（本波交付协议 + 碰撞求解器实现与验收）。

## 3. 决策二：交互侧网格碰撞（W4.2，验收硬指标）

- `frontend/lib/mapspec-runtime/label-grid.ts`：`solveGridCollision` ——
  盒估算（与 `label_typography.estimate_label_box` 逐常量一致）、8 方位退让
  （DECLUTTER 同表）、AABB 格网索引（LabelGrid 同构）、priority+id 稳定全序。
- **MapLibre 内置避让保留为兜底**：网格求解先行抽稀/退让/抑制，残余重叠由
  `text-allow-overlap:false` 吸收 —— 两层叠加，不是替换（与 W0 双语义教训
  同纪律：不改冻结面，叠加新层）。
- **验收实测**：200 要素密集点阵上，`pairwiseOverlapRate` 较无避让基线
  （V10 语义：全部右上放置）下降 **≥40%**（测试硬断言）；确定性 + 输入序
  无关性测试锁定。

## 4. 决策三：专业排版（W4.3）

- `wrap_label_multilingual`：CJK 按字（同 wrap_label_text 宽度口径）、拉丁
  **按词**（词边界贪心 + 超长词硬断 + 尾部合并截断 —— **绝不静默丢词**），
  auto 按 CJK 占比 ≥0.3 选向（中英混排用例锁定）。
- `polygon_label_point`：`shapely.maximum_inscribed_circle` 圆心（不规则面
  显著优于质心），退化/无效环回退 `representative_point`（保证在面内）。
  引线标注/沿线标注复用既有 `label_engine` callout/等弧长站点（V10 已有，
  不重复造轮子 —— 台账登记映射）。

## 5. 决策四：前端字段兜底（W4.5）

- `pickLabelField(features)`：后端 label_plan 缺失时按 C3 缺省词表
  （name/name_zh/title/label/代号/名称）扫描自选，**返回
  `degraded: true` 诚实标记**；无候选 → null（不猜）。满值字段优先，
  扫描序确定性。

## 6. 决策五：性能预算（W4.6）

- `solve_labels` 10k 预算 10s / 50k 预算 60s（实测 ~0.5s/~3s，5–20× 余量，
  只拦回归性劣化）；标注渲染端到端（浏览器）预算归 W8 golden 批。

## 7. 验收对照

| 任务书 W4 验收 | 状态 |
|---|---|
| 交互侧网格碰撞生效，重叠率较 V10 基线下降 ≥40% | ✅（200 点密集阵实测，测试硬断言） |
| 三类专业排版各有 golden | ⚠️ 引线/沿线/面内：面内（最大内接圆）+ 沿线（等弧长站点）+ 引线（callout）均有确定性单测；跨语言 golden fixture 随 W6 渲染接线补（与 W2 同口径，台账登记） |
| 多语言断行中英混排用例 | ✅ auto 双向 + 丢词防线 |
| 前端兜底路径有测试 | ✅ 3 例（词表命中/满值优先/null 诚实） |
| 50k 标注性能达标 | ✅（~3s 实测 / 60s 预算） |

## 8. 风险与回滚

- C3 只加不改：`LabelStrategy` 新字段默认 None；`build_label_spec` 新 key
  被旧消费者忽略安全；碰撞求解器是**叠加层**（MapLibre 兜底语义不变）。
- 回滚开关：`collision.strategy="maplibre"` 一键回到 V10 行为。
- 回滚点：tag `ac-v11-w4`。
