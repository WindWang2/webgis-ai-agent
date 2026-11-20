# G27 PLAN — 工作包状态与验收映射

分支 `zcode/g27-scale-aware-portrayal-20261111T144000Z` · BASE `8a9a79f76875eb95d31bd448baad971edb2e61d3` · worktree `webgis-wt-g27-scale-aware-20261111T144000Z`

状态词：`not_started` → `in_progress` → `implemented` → `verified` → `blocked`。**implemented ≠ verified**。

## 迭代纪律

每轮：读真实路径 → 一个可证伪目标 → 失败用例/契约 → 实现 → 本地回归 → 自审 → 更新 `LOOP_LEDGER.md`。已有 golden 不批量刷新；改动须写语义理由并经独立 reviewer。

## 工作包

| WP | 目标 | 状态 | 交付 | 主要证据 |
|---|---|---|---|---|
| P0 | 四类尺度分清 + 契约（用户隐藏 / zoom 门正交 / pin 锁优先 / unknown fail-safe） | in_progress | `RECON.md`、`INTERFACES.md`、失败用例 | 只读调用图报告；四类语义表；precedence 合同 |
| P1 | 单一表达解析入口（有版本、有限词表、typed 诊断、有界解释） | not_started | `app/lib/cartography/scale_portrayal.py`、前端镜像、共享 corpus | unit/contract 双侧 |
| P2 | 实时生产接线（zoomBands / maxFeatures / terrain hint 真消费；去抖幂等；camera 不落库） | not_started | converter 落笔补丁、runtime/compiler 消费、去抖 | 生产调用图 + runtime 测试 |
| P3 | 模板/组件同一解析结果；显示策略不写回 source/统计；热力图 px/米披露 | not_started | 组件档位状态、disclosure | parity / semantic 测试 |
| P4 | 按尺度表达面板 + Agent 读取/局部覆盖/回执 + 键盘无障碍 | not_started | UI 面板、ToolRegistry 工具 | UI 测试 / Pi 回执 |
| P5 | 出版等价视窗（frame extent/CRS/逻辑 viewport；DPI 不改可见层；多 frame 各自 viewport） | not_started | `publication_ir.py` 加性 scale viewport | parity 测试 |
| P6 | 有界 explanation + 性能/内存/状态更新测量（不靠删数据换基准） | not_started | explanation 结构、基准记录 | TEST_EVIDENCE.md |
| P7 | 契约语料 + Pi 全链 + 真实浏览器 + 独立 review→fix→review | not_started | corpus / 三层验证 / PR | ACCEPTANCE.md / REVIEW.md |

## 验收矩阵映射（A01–A28）

| 验收 | WP | 生产入口 / 测试落点 | 预期断言 |
|---|---|---|---|
| A01 旧项目默认渲染不变 | P1/P2 | 旧 spec 无 `scale_policy` → opt-in 关闭 | converter 输出与 BASE 逐位一致；golden 不刷新 |
| A02 实际 runtime 消费策略 | P2 | `label.zoomBands` → `normalizeLabelStrategy` → font-size/top-N | 真实 runtime 表达式含 band 系数，非仅纯函数 |
| A03 backend/frontend 档位边界一致 | P1/P7 | `scale_portrayal_cases.json` 双侧消费 | 同一 zoom 边界两侧同档 |
| A04 min/max 边界无闪烁/死带 | P1/P2 | 半开区间 + hysteresis | z=3/6/10/14/19 边界无空档、无双档 |
| A05 人工隐藏在所有 zoom 保持 | P1 | `layout.visibility=none` / `visible:false` / policy.hidden | 全 zoom 扫描恒 hidden_by_user |
| A06 锁定字号/pin 优先 | P1 | pinned_size_ratio / user_pin | 覆盖 LOD 值且不被 zoom 改写 |
| A07 未锁标注密度正确变化 | P1/P2 | lod_for_zoom + build_label_zoom_bands | 4 档 topRatio 单调不降 |
| A08 无效 policy typed 诊断 | P1 | `validate_scale_policy` | 结构化 code/field/detail，不静默当已应用 |
| A09 缩放不产生每帧 mutation | P2 | camera observed 路径 | zoomend 不调用 apply_mutation |
| A10 快速缩放取消旧计算不串状态 | P2 | 分档 + 幂等 + 单调 token | 旧结果被丢弃，无状态串档 |
| A11 渲染抽稀不改分析 count | P1/P3 | AggregationSemantics(visual_only) | source feature_count / 统计 count 不变 |
| A12 legend 维持完整数据域 | P3 | derive_legend_items | 档位切换前后 entries 相同 |
| A13 传输 LOD 不误作统计样本 | P3 | data-plane plan.ts display-only | 显式 disclose，不写入分析结果 |
| A14 热力图 px/距离语义披露 | P3 | heatmap_contract | radius_px / bandwidth_m 分别标注，不跨 zoom 伪称同量纲 |
| A15 DPI 改变不导致可见层变化 | P5 | `export_adjudicate_layer` + dpi 72/300/600 | 三种 DPI 裁决逐位相同 |
| A16 不同 CRS/纬度导出不套错公式 | P5 | `scale_denominator`(lat) + CRS 门 | 非 Web Mercator → `None` + 披露；纬度单调 |
| A17 模板选型与组件变体真实支持 | P3 | component_registry variants | 只选 registry 已声明 variant |
| A18 组件布局不因档位切换重叠 | P3 | composeMapLayout 输出 | 档位切换前后布局矩形不重叠 |
| A19 局部 policy 支持 CAS/undo | P4 | apply_gis_mutation + expected_revision | 过期 revision → superseded；undo 回退单笔 |
| A20 只改一个 layer 不全图重置 | P4 | 单 layer patch | 其余 layer 字节不变 |
| A21 多 frame 各自等价 viewport | P5 | FrameSlotIR / page_specs | 每 frame 独立 bounds/viewport |
| A22 Agent 读取真实 effective policy | P4 | ToolRegistry 工具 | 返回与 UI 同一解析结果（非 stub） |
| A23 真实 Pi → UI → export 闭环 | P7 | Pi 子进程 + stub LLM + 本地后端 | RPC/discovery/dispatch/receipt 全链 |
| A24 解释量有界 | P6 | explanation 序列化 | 字段/数组长度/字节数 ≤ 硬上界 |
| A25 大 label/点集无无限结构 | P6 | 规模测试 | 有界内存/耗时，不遍历全量属性上传 |
| A26 旧 schema/未知 schema 降级 | P1/P7 | version.ts 门 + policy 缺省 | 保留 last-good + typed 披露 |
| A27 数据刷新时 policy 引用无幽灵字段 | P1/P4 | 字段引用校验 | 引用已消失字段 → typed 诊断，不静默生效 |
| A28 无消费者的设置不显示"已生效" | P1/P4 | effective status 与 diagnostics | `applied=false` + 原因码 |

## 完成门槛 / 退出

- Must-have（P0/P1/P2 及承诺验收）阻断归零；独立 reviewer 复跑关键用例并留 findings→fix commit→复验结论。
- 本地三层验证：deterministic unit/contract / 真实 Pi 子进程+stub LLM+真实工具服务 / 本地真实浏览器+后端。无权限或缺依赖 → 如实 `blocked`，先完成可做部分，不以 mock 冒充。
- 最终：推送分支 + 创建 PR 到 master（**不合并**、不改 master、不删他人分支/worktree、不等线上 CI、不改门禁）。关键验证被外部阻断 → Draft PR 明确标记未完成。
- 退出时清理本方向自启 PID/容器/临时文件；不全局 killall / docker prune / 生产迁移。
