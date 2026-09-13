# PR 描述草稿（ac-03）

## 目标

自适应符号化引擎 V10（AC-03）：把 ADR-0073 的分布驱动分类裁决从单点接线扩展为
**method × k × palette × 离群值策略的唯一裁决入口**，五个出口（build_thematic_style /
create_thematic_map / h3_binning / heatmap_data / apply_template）全部改从
`resolve_symbology` 取符号化决策——同一份数据走不同入口产出**同一** SymbologyDecision
（P0 勘察实测改造前四入口四种答案）。同时补齐 CVD（红绿/红蓝/蓝黄色盲）与 print
两个硬约束上下文、离群值裁剪的强制图例披露、legend_spec v2（加字段，schema 冻结）。

## 《复核纪要》（§0.2 防重复）

- PR 检索（symbology/classification/palette/legend_spec/colorblind，200 条）：相关历史为
  #337（MapSpec style/legend 合一）、#1015（Cartographic Planner）、#1093/#1094/#1145（模板/组件库）、
  #1151（Design System V4）、#1195（Cartography V7）——**均未做统一符号化裁决/CVD/上下文**，无重复。
- Issue 检索（300 条）：#679（热力图 adapter 硬编码调色板，前端侧已关）为同一痛点的前端切片；
  本线是后端裁决面的正解，无重叠实现。
- 分支检索：仅本线分支命中。
- grep 销项（"quantiles"/"YlOrRd"/k=5）：116 站点入账（`docs/dev/ac-03-hardcode-ledger.csv`）。
- ADR-0073（choose_classification）**延续复用不重写**；ADR-0078（legend_spec 单一真相）延续，
  v2 为其 additive 演进；ADR-0118（渲染 V5）不冲突。ADR watermark 实测 0147，按契约占用 0152。

## 变更摘要（+3100/-90，2 commits）

1. **P1 引擎**：`app/lib/cartography/symbology.py`（新，纯函数无 IO）——
   `resolve_symbology(profile, intent, constraints) -> SymbologyDecision`；
   `rejected[]`（kind/value/reason）即 09 线自愈可替换动作清单。
2. **P2 k 裁决**：n<8→3、唯一值上限、屏幕要素密度下修、**色带可分辨上限**
   （min_adjacent_delta_e 反推最大可分级数），夹逼 [3,7]，每步 rejected 留痕。
3. **P3 色带上下文**：`PaletteContext` 六值；`simulate_cvd`（Machado 2009 线性 RGB 矩阵，
   任务书允许的简化实现）；print 降饱和（作用于输出色）+ 灰度 ΔL≥0.06 门限；
   CVD ΔE00≥10（screen 下 18 条带全过=零默认行为变化；CVD 模拟下 ColorBrewer
   sequential 自然落选、感知均匀族入选——「CVD 优先」由阈值涌现而非特判）；
   暗底感知均匀族前置；全部颜色走 `resolve_thematic_colors` 单点解析。
4. **P4 离群值**：`clip_policy = none|clip_p99|head_tail|log`；裁剪分类前应用；
   **裁剪禁止静默**——legend 必带 `out_of_range` {color,label,count,upper}。
5. **P5 接线**：五入口 + 次级站点（extrusion、composite ThematicSlot——旧白名单把
   std_dev/head_tail/lisa 静默降级 quantiles 的缺陷一并消除；lisa 槽位改走语义五色）。
   62 个 SEED 模板 payload 在消费侧降为**声明偏好**（recommended）：重尾证据可推翻
   method 偏好、CVD/print 硬约束可换 palette、k 受边界封顶，全部留痕；lisa/categorical
   作为结构模式显式保留。`heatmap_data` 经 family↔规范色带 id 映射接引擎，
   screen 恒等映射=**默认渲染零变化**，新增显式 `palette_context` 参数。
6. **P6 legend_spec v2**：加字段 `k/palette_id/clip_policy/why/nodata_label/
   out_of_range_label/out_of_range/context`；v1 语义零变化（normalize 不动，
   升级走 `upgrade_legend_spec_v2`）；JSON Schema 快照已冻结（下附）。
7. **P7 回归**：6 数据集 × 5 入口同一决策（4 入口全字典相等 + heatmap 语义字段相等，
   理由差异见 decisions #14）；CVD/print ΔE/灰度/WCAG 逐条断言；golden 数值锁定；
   62 模板偏好对照（28 thematic 端到端）。
8. **P8**：`scripts/symbology_audit.py` 硬编码回潮门禁（allowlist=C 级例外台账）；
   CHANGELOG、ADR-0152、决策日志（14 条）、勘察报告、两份矩阵 CSV。

## 一致性对照表（P7 摘要）

| 数据集 | 裁决（5 入口一致） |
|---|---|
| 重尾计数 | head_tail / k=5 / YlOrRd / clip=head_tail |
| 近均匀 | equal_interval / YlOrRd / none |
| 中等偏态 | natural_breaks / YlOrRd / none |
| 常量字段 | equal_interval / k=3 / low_confidence |
| 中等离群尖峰 | equal_interval / **clip_p99（out_of_range 条目）** |
| 小样本 n=5 | equal_interval / k=3 / low_confidence |

## 交付台账

`docs/dev/ac-03-delivery-ledger.md`（任务 → 文件 → 测试 → 证据逐项对照）。

## 本地门禁证据（无 CI，本机即门禁）

- `ruff check <变更 16 文件>` → `All checks passed!`
- `python scripts/symbology_audit.py` → `[OK] 硬编码销项扫描通过`
- AC-03 全部新套件：引擎 39 + golden 11 + legend v2 13 + 一致性 8 + 模板对照 59 = **130 passed**
- 全量 `pytest tests/unit -q -n 2 -m "not heavy and not real_services and not perf"`：
  **10804 passed / 111 skipped / 44 failed** —— 44 失败逐条归因为
  (a) 与纯净基线 worktree（09d839d3，同一 venv，单进程）**逐条一致复现**的环境预存失败
  （extensions_platform 沙箱/资源限制——Windows 无 bwrap、postgis 驱动、Redis/socket 类），
  (b) `-n 2` 并行抖动（geocompute_v5/v6 等在本分支单进程 38 passed）。
  **本线零新增失败**（比对证据：/tmp 比对清单 + worktree 复跑终端原文）。
- 终端原文：见下方评论（final gate log 全文摘录）。

## 风险与回滚

- 行为变化面：① create_thematic_map 缺省调用从 quantiles/5/YlOrRd 变为引擎裁决
  （重尾数据会看到 head_tail）——这是本线的目的；② 模板偏好可被强证据推翻（reasons/
  rejected 全程披露）；③ 近均匀+无推荐池现在按 choose_classification 文档承诺落
  equal_interval/quantiles（原掉 natural_breaks，一行修正，既有测试全绿）。
- 回滚：单 commit revert 即可（引擎为纯新增文件，接线点改动集中且向后兼容——
  显式传参路径行为不变，v1 legend_spec 消费方不受加字段影响）。
- 性能：裁决为 O(n) 统计 + 色带可分辨探测（≤5 次 ΔE 计算/候选）；6×5 矩阵端到端 2 分钟内。

## 协调点（§8）

- **legend_spec v2 schema 已冻结**（`docs/dev/ac-03-legend-spec-v2.schema.json`，置顶贴出）：
  06 线（paint 投影）、07 线（图例渲染）按此消费；print 上下文的 palette_colors 已降饱和，
  消费方**不得**二次变换。
- **09 线（自愈）**：可替换动作清单 = `SymbologyDecision.rejected[]`
  （kind ∈ method/k/palette/clip/context，{kind,value,reason} 形状）。
- 模板 payload 键位未改（62 个模板的 method/k/palette 键保留），语义在消费侧降为偏好——
  若 03 后续线需要 schema 级表达（如 `declared_preference` 标记），需与模板库线协调。

## 被否方案 / 披露（详见 decisions 日志）

- CVD 实现用 Machado 2009 矩阵（非 Brettel/Viénot 查表法）——任务书允许的简化，纯函数可 golden。
- 模板色带与数据类型族不匹配时**只披露不降序**（初版降序导致 RdBu 模板观感全变，超范围）。
- qualitative 色带 print 全不可分级：保留族内首选+披露「打印需形状/图案」，不虚构 Viridis 能力。
- cyclic 无专用色带：降级感知均匀族并披露。
