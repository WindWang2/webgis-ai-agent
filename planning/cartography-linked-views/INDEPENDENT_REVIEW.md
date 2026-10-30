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

## R3 cartography parity & UX（进行中）

## R4 adversarial E2E & branch dedup（进行中）
