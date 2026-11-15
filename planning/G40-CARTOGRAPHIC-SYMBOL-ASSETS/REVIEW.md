# G40 REVIEW — 独立评审 / 修复 / 复审记录

状态：**Round 1 = GO-WITH-FIXES（3×P1 + 7×P2 + 11×P3）→ 全部 P1/P2 + 4×P3 修复 → Round 2 复审待填**。

## 协议

- Reviewer = 第 3 个 subagent 槽位，**未参与核心实现**（槽 1/2 为只读探索）。
- 输入：最新 base SHA（1276692737）、完整 diff（9abcd60bb）、用户场景、兼容边界、
  真实测试证据、DEDUP_MATRIX、INTERFACES、TEST_MATRIX。
- **不告知「预期通过」**；reviewer 自行复跑关键用例（G40 三套件 35✓、回读 5✓、
  Pi journey 23s✓、前端 13✓、共享回归 128✓、ruff/tsc✓ 均其亲跑）。
- Round 1 报告原文存档于本目录 `REVIEW_ROUND1_REPORT.md`。

## Round 1 findings 与修复（commit 83009c320）

| # | 级别 | 问题 | 修复 |
|---|---|---|---|
| F1 | P1 | DB 资产内容端点信封与前端消费不符 + blob 无 digest → live 整层消失 | 路由扁平返回 + read_version_blob 注回 digest；回归 test_f1 |
| F2 | P1 | SVG `M` 多坐标对被当连续 moveto（合法几何静默丢失）；尾随 Z 丢失 | 隐式 L 展开 + Z 即时处理；回归 test_f2 |
| F3 | P1 | live 纹理「tile 预旋转」与 patternTransform 语义分歧（45° 密 41%；30° 摩尔纹） | 有理角格点（tanθ=p/q 连分数逼近，角差 ≤0.3°；S=d·√(p²+q²)，间距亚像素保真；构造性无缝）；前端 lattice 几何 oracle 2 tests |
| F4 | P2 | fallback_asset 类别绑定写 schema 禁止字段 → 文档 invalid | schema 补 other_revision/other_content_digest/other_params + types 再生成；回归 test_f4 |
| F5 | P2 | Pi apply 幂等键缺 layer_id → 同 plan 绑第二层被 dedup 吞 | 键加 layer_id |
| F6 | P2 | 纹理类别子层无级联注册 → 幽灵层 | 注册 repSublayersByLayer（removeLayerSafe 显式级联）+ 修注释 |
| F7 | P2 | G34 print_layout 导出匿名解析 → 属主 DB 资产丢弃 | 路由透传 asset_identity（get_current_user_optional） |
| F8 | P2 | MAX_COORD_ABS 未对未变换 path/circle/ellipse/rect 强制 | _flatten_path 全量 + 图元 extent 检查；回归 test_f8 |
| F9 | P2 | svg_tile 周期双端不一致 + tile 被 fg_color 覆涂 + swatch 无 tile 分支 | 周期统一 tile_viewbox×scale×dpi；tile 保留自身颜色；swatch 双端补 tile 分支；回归 test_f9 |
| F10 | P2 | 嵌套 `<svg>` 被接受（文档承诺拒绝） | SVG_ELEMENT_UNSUPPORTED；回归 test_f10 |
| F11 | P3 | 根元素 onload/style 静默忽略 | _reject_attrs/_style_dict 作用于根；回归 test_f11 |
| F12 | P3 | use x/y 偏移被忽略（连带发现 use-path 变换嵌套 bug） | 偏移进变换矩阵 + _shift_primitive 重写；回归 test_f12 |
| F13 | P3 | angle2_deg 死参数静默忽略 | 从词表与 builtin 默认移除（显式 UNKNOWN_PARAM） |
| F17 | P3 | 路由 docstring 腐化（未实现的 builtin 副本） | 修正 |
| F19 | P3 | 屏上图例全类别共用主绑定 swatch | AssetSwatch 按 c.key 解析规则资产 |

## Round 1 明确不修（记录为已知限制，随 PR 披露）

- F15（unbind 无条件恢复 prev_paint_snapshot —— docstring 已披露；绑定期 paint
  修改会被回滚）：与「恢复绑定前表达」语义一致性取舍，保持现状并披露。
- F16（content_digest 含 name —— 无 rename API，当前不可触发；改名入口出现时
  需把 name 移出 digest 基）。
- F18（纹理类别子层不继承 fill-opacity —— 首版类别纹理与透明度组合的边界，
  图例/披露不承诺该组合）。
- F20（TS g40Defs 模块级 Map —— 同步编译无重入；并发消费者出现时改调用域）。
- F21（builtin-documents.ts 生成脚本未入库 —— 本次人工核对同步；漂移锁
  随脚本一并补）。
- F14（reinstallAll 无调用方 —— 保留为 API 面；S15 实际由 reconcile 重投影覆盖）。

## 修复后验证（全部重跑，2026-11-16）

- 后端 G40：**48 passed**（含 F1-F13 逐项回归）
- cartography 全量：**3655 passed**, 3 skipped, 3 xfailed（deselect 1 既有基线）
- Pi 真实旅程：**1 passed**（28s，10/10 verdict）
- 前端：**15 passed**（+lattice 几何 oracle）+ `tsc --noEmit` 0 error
- unit 针对集（触及模块全部引用方）：**831 passed**；5 failed = 干净 base
  逐条复现的既有基线
- ruff（G40 触及文件）：All checks passed
- types.generated.ts 再生成（F4 后）+ 投影契约测试绿

## Round 2（复审，reviewer 槽位复用）—— GO-WITH-FIXES（报告存档 REVIEW_ROUND2_REPORT.md）

复跑全绿（后端 48/G34 34/Pi journey/前端 15/tsc/ruff 均其亲跑）。Round-1
修复判定：F1/F2/F3/F4/F5/F7/F10/F11/F13/F17/F19 FIXED（F3 无缝性由其独立
Python 复刻 oracle 验证「全参数域 seam=0、间距 ≤0.22px」）；F6/F8/F9/F12
PARTIAL（残留见 N3/N8/N5）。**新发现**（两轮共同盲区，pre-existing 于首
commit）：

| # | 级别 | 问题 | 修复（e459baf22） |
|---|---|---|---|
| N1 | P1 | svg_tile 导入归一单位方，渲染链按 viewBox 域 → 导入纹理全表面渲染为角落微点 | 导入保持 viewBox 域（root_matrix=None）+ 跨面 oracle 测试 |
| N2 | P1 | 图元 transform 被双重应用 + 嵌套组合顺序颠倒（own∘parent） | _emit 不再二次解析；组合改 parent∘own；scale/translate 非交换 oracle |
| N7 | P2 | OpenAPI 快照/api-docs 未随新路径刷新（2 仓库测试红） | API_SNAPSHOT_UPDATE 刷新 + gen_api_docs 再生成（15 passed） |
| N3 | P3 | line 坐标帽缺失 | 端点 _check_coord + 测试 |
| N4 | P3 | lattice docstring ≤0.3° 不实；兜底 size 无 256 帽 | docstring 诚实化 + 兜底帽 |
| N5 | P3 | svg_tile 导出误加 patternTransform；bg_rect 只盖局部 | 双端 tile 不旋转 + bg 全周期 |
| N6 | P3 | builtin TS 孪生漂移（F13 后未再生成） | 再生成 + Python↔TS digest 漂移锁测试（test_n6） |
| N8 | P3 | rep 子层 `.set` 整组替换抹掉 texcat 注册（组合场景幽灵层） | 合并追加 |

另据其微备注顺手修：use x/y 非数值 typed 拒绝（不再静默归零）。
其明确不修（维持披露）：F14/F15/F16/F18/F20/F21 中未列项与
_shift_primitive line w/h 近似说明。

## Round-2 修复后验证（2026-11-16，commit e459baf22）

- cartography 全量 3659 passed；Pi 真实旅程 1 passed（29s）
- 前端 15 passed + tsc 0；API 快照 + 文档漂移 15 passed；ruff 干净
- 新增 oracle：N1 跨面 / N2 非交换变换 / N3 line / N6 孪生漂移锁

## Round 3（终审，reviewer 槽位复用）

待填。
