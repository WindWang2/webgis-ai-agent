# ADR-0165: V11 W5 — 版面与叙事自动化（G1 备选版面接线、G2 自愈执行化、多图版面、StoryMap、版面评分）

- 状态: Accepted
- 日期: 2026-09-13
- 线: adaptive-cartography/v11-master（W5）
- 关联: ADR-0156（版面自动装配/自愈）、ADR-0147（StoryMap chapters）、ADR-0160（C2 IR）、ADR-0161（配方亲和）

## 1. 背景与靶心

G1：`select_composition_alternatives` 零生产调用（07 线核心算法纸面）。
G2：版面自愈只 planned 不执行（`compose.ts` 明示不应用；`__fallback_*`
三处特批直插）。W5 把两者落到生产路径。

## 2. 决策一：G1 备选版面生产接线

- `planner.py` 新增投影缝 `_composition_alternatives_evidence(plan, profile)`：
  harness 事实（intent task → 类目静态映射表、几何类型 → geometry_kind、
  data_kind → variable_kind、artifactTypes）→ `TaskCartographyContext` →
  **W0.3 定稿的调用契约** `composition_alternatives_payload`（≥3 候选 + 评分，
  有界可序列化）。fail-safe：投影/求解失败 → 空 dict（证据缺席 ≠ 规划失败）。
- 组合成功后证据入 `template_selection.composition_alternatives` ——
  plan_ready 事件/审计/产品组装可消费「同数据的多合理组合」。
- grep 断言（`test_composition_alternatives_production_wiring`）锁定生产
  调用形态 —— W5 验收「select_composition_alternatives 有生产调用」的
  机器可查形态。

## 3. 决策二：G2 自愈从 planned → executed

- `compose.ts` 四级策略链（改锚 → 折叠 → 隐藏；shrink 由 placement 尺寸
  承载）**应用到渲染面**：`anchorOverrides` → `placement{mode:'anchor',
  anchor}`、`collapseIds` → `placement.collapsed`、`hideIds` →
  `enabled:false`（组件保留可审计）。决策记 `status='executed'`（V10 的
  planned 语义测试更新为 executed 语义 —— 行为变更即本波交付）。
- `__fallback_*` 三处归零：compose.ts 安全网并入 **autofill 候选流**
  （同 id 规范 `__autofill_*`、同注入路径，决策 kind='fallback_hit' 保留
  审计语义）；export-chrome.ts / render-observation.ts 的槽位预约键机械
  改名同键。老工件 id 在 origin 映射中兼容读（compose.ts:260 保留）。

## 4. 决策三：多图版面（W5.3）

- `atlas_layout.py::plan_atlas_pages`：场景列表 → 逐页 **C2 IR**
  （`build_layout_ir`；主图 + 共享 chrome：标题族/比例尺/署名，锚点每页
  一致 —— 图册视觉连续性）。页数上界 20（截断如实记 degradation）；
  非法画布 fail-closed；确定性（页序/id/几何全由输入决定）。

## 5. 决策四：StoryMap 大纲（W5.4）

- `story_outline.py::story_outline_from_session`：会话事实（问题 → 数据 →
  分析 → 成图）确定性投影为四幕章节大纲（复用 ADR-0147 `/story` 章节
  词汇）；缺事实章节标 `missing`（不虚构叙事）；要点有界（≤8/章）。
  UI 消费（/story 页面渲染大纲）为既有能力接入，不做新 UI。

## 6. 决策五：版面五维评分（W5.5）

- `layout_score.py::score_layout`：平衡性（象限分布）/ 密度（槽位占用
  舒适带）/ 留白（上下留白 presence）/ 层级（主件不堆叠）/ 对比（对角
  分布），加权总分。纯函数确定性；C4 事实库观测行形态（ratchet 消费归
  W8 批次）。实测：均衡分布 > 单角聚集（测试锁定方向）。

## 7. 验收对照

| 任务书 W5 验收 | 状态 |
|---|---|
| `select_composition_alternatives` 有生产调用（grep 断言） | ✅ planner 缝 + 断言测试 |
| 自愈四级在 live 生效（非 planned） | ✅ compose.ts executed + 测试更新 |
| `__fallback_*` 归零 | ✅ 三处替换（老工件兼容读保留） |
| 多图版面与 StoryMap 各有端到端用例 | ✅ atlas 逐页 IR / story 大纲（渲染端到端归 W6 消费） |
| 版面评分入 ratchet | ⚠️ 评分器 + C4 观测行形态交付；ratchet 基线随 W8 批次（任务书 W8 职责） |
| 与 W4 优先级模型共享 | ✅ 网格碰撞 priority + 修复链 hide_lowest_priority 同为 importance 语义（label-grid priority / composition-repair 既有裁决） |

## 8. 风险与回滚

- G2 是行为变更（自愈真的改渲染面）：compose.test 22 例更新后全绿 +
  layout/map-components 143 全绿；回滚 = 恢复 planned 注释路径或
  `collision.strategy="maplibre"` 同级开关（W4）。
- 回滚点：tag `ac-v11-w5`。
