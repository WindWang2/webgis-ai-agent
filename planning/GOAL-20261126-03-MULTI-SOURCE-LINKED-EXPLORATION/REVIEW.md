# REVIEW — 03 多源空间联动探索（两轮独立审查 + 修复 + 复审）

Round 1（2026-11-27，三个只读子代理并行，逐行 + 机器复现）：
- A 科学语义/统计口径/独立 oracle（3,496k tokens 审查预算）
- B 快照/并发/授权/预算/竞态（3,045k）
- C UI/渲染/可复现验收/真实旅程（2,280k）

## Round 1 发现汇总与处置

| 级别 | 来源 | 发现 | 处置（commit abd98fc74） |
|---|---|---|---|
| P1 | B | 多源导出不可投影守卫死代码（复合键传播无声丢弃、图面过宽 fail-open） | `if not projected:` 独立判定；EXPORT_SCOPE_NOT_PROJECTABLE 可达；正路/拒绝两用例 |
| P1 | C | i18n 9 个扁平点号键 next-intl 不解析（渲染键路径原文，挂载即 P0） | 两 locale 改嵌套结构；断言收紧为精确文案 |
| P1 | C | multiSource.propUnfiltered 缺键 → key-completeness 守卫红（本线打红 CI） | 补 zh/en |
| P2 | A | 传递性 no-op 误判：全域无条件 A→B→C 链静默收窄 C（3→2 行） | 执行态判定 incoming_preds；TestTransitiveNoOp 手算 2/3/3 |
| P2 | A | 跨源 rate 端点错配 → 无关源静默假 0 率 | 计划校验源对 == link 两端 |
| P2 | A | own 无条件 + 复合键传播导出未过滤图面（同 B-P1 根因） | 同上（独立 projected 判定覆盖） |
| P2 | B | preview/export 核心扫描内联阻塞事件循环（250k 行秒级） | 打包 to_thread（_evaluate_propagation / _build_frozen_fc） |
| P2 | B | 同 ref 双源绑定导出误报 NO_TARGET_LAYER / 误标 not_on_map | 后缀剥离 + 计划级同 ref 拒绝（A-P3-9） |
| P2 | C | PlanPreviewResponse 缺 selection_ref_resolved | 已补（含 SelectionRefDisclosure） |
| P2 | C | 组件测试断言歧义掩盖 i18n 失效 | 精确文案断言 |
| P2 | C | 工具回执裁掉 invalid_weight_count 口径披露 | 补 key_label/count/invalid_weight_count |
| P3 | A/B/C | digest 公式锁定字面量 2 + 手构 oracle 测试；fsum 溢出 → unavailable；负分母 → denominator_non_positive；空键集复合传播 → 空 in-叶精确投影；docstring 对齐；跨源 rate grain 归位分母侧；open_view STALE 折算；drill 跨源父表 typed 拒绝；drill 精确 id 清理；selection 账本 503；METRIC_SCAN_LIMIT per-source；内部码 → 500；asyncio 标记；a11y uls/传播披露沿用本域约定 | 全部修复 |
| 额外 | 自查 | spatial_within + propagate → 空键谓词口径错配（三报告均未覆盖的边界） | LinkSpec typed 拒绝 + 2 用例 |

Round 1 结论：A「有条件可合并（先修 3 P2）」、B「建议先修 P1-1」、
C「后端可合并，前端两处低成本 P1 修完即可」。

## Round 2（复审）

- 复审范围：`git diff 78e090ef4..abd98fc74`（全部修复），由 A/B/C 原审查者
  独立重读 diff + 重跑关键用例（复核记录见下）。
- 复审结论：（执行期追加）

## 既有基线问题（本线不修，如实记录）

1. Runtime Manifest 11 项悬空 capability（#2274/#2275 已合并改动）→ Pi journey
   需 GIS_MANIFEST_STRICT=0（app 提供的降级开关）。
2. SQLite 托管水位 0103 落后迁移（messages.attachments 在 g36/0107+）→
   新库 transcript 持久化失败 → Pi journey view_save 起 5 gate 阻塞。
3. openapi 快照 master 本就过期（6 项既有 breaking）→ 本线按规则显式刷新。
4. i18n chat.chart.figureTopValues 占位符 zh/en 不一致（master 既有）。
5. G46 既有 7 条路由 HTTP 直接测试缺位（本线为新路由建网 + 修复
   conv.session_id 潜伏缺陷；旧路由测试网留后续）。

## Round 2 复审结论（2026-11-27）

- B'（并发/授权复审者）：**通过** —— P1 + 4×P3 全部修复带回归测试；同 ref
  双绑定"拒绝+后缀剥离"组合成立；`_validated_plan` 私有通道确认不可达于
  REST/工具面（grep 证据）；注入安全闭环（sel-ref 叶编译期过 _validate_ast）。
- C'（语义/验收复审者）：**通过** —— 13 项请求核验全部真实证据支持；
  i18n 两 P1 经运行时 createTranslator 探针转绿；无虚报。
- 复审尾巴（本轮随手清理，全部落实）：
  ① 单源导出 FC 构造 → _build_frozen_fc + to_thread（已改）
  ② _source_has_incoming 死代码删除（已删）
  ③ 新回归文件 asyncio 标记 → 类级（已改）
  ④ A-P3-5/6/11 回归锚 3 项（已补 TestReviewRegressionAnchors）
- 勘误：B' 提到的 raster_tile_service.py F401 经逐字节比对为 **master 既有
  基线**（与本线 diff 无关），记入基线清单不修。
- 遗留跟进（PR 说明）：inspect 抽样循环（20k 帽）量级数十 ms 可后续下线程；
  A-P3-12 scope 字符串矩阵（无功能消费者，建议后续契约测试钉住）。
