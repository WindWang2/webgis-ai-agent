# INDEPENDENT_REVIEW — Track B 四轮独立评审记录

评审纪律：reviewer 为独立子代理，逐文件审查 + 实际运行测试 + 临时探针脚本；
作者（主 Agent）不参与批准自己的改动；findings 以 file:line + 复现 + 证据为准。

## R1 correctness & security（2026-10-31）

实测：后端 64 passed / 前端 11+17 passed；探针（cursor 伪造/工具走私/注入面/线程锤击）。
结论：**无 P0；1×P1 + 6×P2**。核对维度：cursor 语义/谓词注入/跨租户/revision guard/位置 id/前端状态机/工具层 —— 除 findings 外"未发现"。

| 级别 | finding | 清偿 |
|---|---|---|
| P1-1 | loadMore 409/429 静默吞掉（status 仍 ready，横幅不渲染） | ✅ a8c6c61ff：error 态 + 横幅与已加载行并存 + 组件测试 |
| P2-1 | 聚合 __other__ 与真实组值撞名（双行同 key） | ✅ 递进哨兵 + is_overflow 标记 + 测试 |
| P2-2 | 工具缺取数后 revision 复查（与 REST 双查不对称） | ✅ 双查补齐 + advancing-descriptor 测试 |
| P2-3 | /feature positional 无 revision 披露/守卫 | ✅ v guard 409 + X-Content-Revision 头 + 前端带 v 重试 |
| P2-4 | _derived_cache 注释与线程事实不符 | ✅ threading.Lock + 真 LRU + 权重预算（并入 R2-P1-1 修复） |
| P2-5 | 409 后 reload 重发陈旧 v（死路按钮） | ✅ conflictRevision ref 锚点 + 测试（不自动重取，保留横幅契约） |
| P2-6 | cursor bool 索引放行/深度注释差一/若干小项 | ✅ bool 拒绝 + 注释对齐（其余记录不修：无安全增量） |

## R2 performance & numerical truth（2026-10-31）

实测：基准复跑 9 passed（数值与 EVIDENCE 同量级）；探针（缓存抖动/聚合守恒/长线）。
结论：**无 P0；2×P1 + 7×P2**。"未发现"维度：计数守恒/数值口径（bool/NaN/inf 排除）/truncated 语义/前端内存（无自动全翻）/MVT 字节不变/位置 id 无热点。

| 级别 | finding | 清偿 |
|---|---|---|
| P1-1 | 派生缓存 16 槽 FIFO 全局抖动（0% 命中）+ 百 MB 驻留 | ✅ 真 LRU（命中续期）+ Lock + Σ权重≤8M 帽 |
| P1-2 | 线 repeat 锚点无坐标上限（500×10k 坐标=9.6s；单线不可中断） | ✅ 20k 坐标帽（确定性跨步采样，双孪生同式）+ 100k 坐标 <300ms 测试 |
| P2-1 | __other__ 撞名（同 R1） | ✅ 同上 |
| P2-2 | null 键组可被并入 __other__（违反 docstring 承诺） | ✅ null 组恒单独披露 + 测试 |
| P2-3 | bbox-only 精确 count 不缓存（视口每页多付 ~100ms） | ✅ 缓存条件含 bbox-only + 测试 |
| P2-4 | order 路径无 n 帽（与 count/agg 有界纪律不一致） | ✅ ORDER_SCAN_LIMIT=500k typed 400 + 测试 |
| P2-5 | 基准 "p95" 标注不实（5 样本实为 max） | 记录不修：上界断言可抓 ~5x 回归，标签语义在注释说明 |
| P2-6 | in [null,a] 不命中缺席（与 eq null 不对称） | ✅ 对称语义 + 测试 |
| P2-7 | 前端聚合缺省向"已知/完整"方向 | ✅ total_matching→null / is_complete→false |

## R3 cartography parity & UX（2026-10-31）

实测：4 组 spec 双孪生差分（**整份 SVG 字节级相等**，含 21k 点线触发坐标帽）；单行标签与 master 逐字节一致；mutation parity corpus 58P；compiler 域 287P。
结论：**无 P0；2×P1 + 7×P2**。"未发现"维度：BMP 域双孪生逐分支等价/多行盒口径/既有测试回归/i18n 键对齐/thresholds 分级。

| 级别 | finding | 清偿 |
|---|---|---|
| P1-1 | TS 折行 UTF-16 code-unit 口径 → astral 字符孤立代理项（非法 XML）+ 孪生分歧 | ✅ wrapLabelText/wrapLabelMultilingual 全面 code point 化（Array.from）+ astral 回归测试 |
| P1-2 | check_export_label_diagnostics 的 exports 门在主管道 dict 形状（{fmt:...}）下永不命中（静默失活） | ✅ _declares_publication_export 共享 helper（兼容 str/dict fmt/format/type）；既有 check_publication_components 同病一并修 |
| P2-3 | 15-59 字名折行截尾静默丢字（无省略号无诊断） | ✅ wrapped-drop 口径补发 label_truncated（双孪生）；省略号渲染留后续（宽度记账风险） |
| P2-4 | 拉丁连字符硬断/CJK 孤字行（制图质量） | 记录不修：后续制图质量批次（孤立影响小、改动面大） |
| P2-5 | 坐标帽注释"首/尾点恒保留"失实 | ✅ 双侧注释按事实修正 |
| P2-6 | PagedTableBody row 无外层 table/grid 角色、行键盘不可达 | ✅ role=table/rowgroup 包裹 + 行 tabIndex/Enter/Space/focus-visible |
| P2-7 | __other__ 哨兵直出图表轴标签；agg.category/serverMode 死键 | ✅ 溢出行本地化（其他（组合并））+ 死键清理（zh/en 同步） |
| P2-8 | label_truncated 每要素一条挤掉 budget/suppressed（12 帽）；编译参数固定 96dpi 近似 | ✅ 同码去重；96dpi 保留（evidence 面已声明近似） |
| P2-9 | 证据链措辞：/tmp/master-check 命名误导 | ✅ EVIDENCE 措辞修正（origin/master tip 4b3629c90 + 本地 master 7b4e41667 双复核） |

## R4 adversarial E2E & branch dedup（2026-10-31）

实测：4 个 /tmp 对抗脚本（并发覆写 20 请求/畸形轰炸 ~70 变体/混合 id 瓦片逐要素解码/TOCTOU 补充）；前端 29 用例。
结论：**PASS —— 无 P0、无 P1；4×P2**。A1-A5 全场景按设计收敛（无 500/无挂死/无跨版混页/无数据错配）；分支与 7 个 open PR 零功能重复，master 零前进。

| 级别 | finding | 清偿 |
|---|---|---|
| P2-1 | 不带 v 的请求存在"响应 revision 标注与实际数据错版"窗口 | ✅ served_revision = 取数前快照（features/aggregate 双端点）；取数后重读仅作 409 guard |
| P2-2 | cursor 不内嵌 revision（跨版防护依赖客户端 v） | ✅ 模块 docstring 明确披露该边界（cursor 简单性 vs 内嵌 revision 的取舍已文档化） |
| P2-3 | bbox 无经纬 sanity（lat>90 静默当有效窗） | ✅ |lat|≤90 且 |lon|≤180 typed 400（含 lat/lon order 提示）+ 测试 |
| P2-4 | positional 重试无 descriptor 时不带 v；聚合 409 无恢复路径；会话切换测试缺口 | ✅ 聚合 hook 一次重锚（采纳 current_revision）+conflict 态；setSessionIdentity 会话切换 cancelled 测试；descriptor 缺席场景保持诚实回退（注释披露） |

## 终局

四轮独立评审（R1 正确性/安全、R2 性能/数值、R3 制图 parity/UX、R4 对抗 E2E/排重）合计：**0×P0；P1 全部清偿；P2 清偿 18 项、记录不修 3 项（均有明确理由与后续归属）**。最终回归：后端 143 + 前端 768（1 例 master 既有预期失败）全绿。
