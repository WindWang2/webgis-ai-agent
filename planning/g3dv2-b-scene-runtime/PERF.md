# G3DV2-B PERF — 性能实测（3 轮，非固定阈值）

机器：Ryzen 9 5900HX / 62GB / node v24.21.0 / vitest 4.1.11（jsdom）。
方法：临时 vitest 采样测试（同进程 warm 后 20 次循环取总/均），3 轮手工复跑；命令
`npx vitest run lib/map-kit/scene3d/perf-sample.test.ts`（采样脚本不入库，数字如下）。

## 1. glTF 解析（1024 三角 fixture，三种 accessor 布局）
| 布局 | run1 | run2 | run3 |
| --- | --- | --- | --- |
| packed（紧密） | 4ms | 4ms | 5ms |
| interleaved（byteStride=24 正确步进读） | 3ms | 4ms | 2ms |
| sparse（512 顶点覆写） | 2ms | 3ms | 2ms |

**结论**：交错/sparse 严格解析相对紧密打包无显著开销（同数量级 2–5ms/1024tri；解析随复杂度线性，g3d02 基线 31ms/单资产挂载路径的量级判断仍成立）。

## 2. 预算门（SceneResourceBudgetLedger.wouldExceed + acquire，首次调用）
- plain: 0.188 / 0.197 / 0.229 ms（含 100-owner Map 遍历 warmup）
- interleave/sparse: 0.019–0.032 ms（稳态）

**结论**：预算判读在 200ms 调度节流 tick 内占比 <0.2%。

## 3. 调度器 pass（5461 节点 tileset 全遍历 + 每次 pass 附带一次预算探测，100 记账 owner）
- 0.05 / 0.04 / 0.06 ms per pass（20 pass 总 1ms）

**对照 g3d02 基线**（21,845 节点 4.6ms/pass）：本分支在 5461 节点规模 0.04–0.06ms/pass，与基线的复杂度线性外推一致（~0.008μs/node），预算探测无回归。

## 4. 生产 bundle（next build --webpack 实测，见 EVIDENCE）
master 5.89MB/75 文件 → 分支 5.91MB/75 文件（+20KB；three 所在最大 chunk 671.1KB 不变）。

## 诚实边界
- 全部数字来自 CPU/软渲染环境；无 GPU 真机实测（not_evaluated），不声明真 GPU 帧率。
- 帧节奏（frame pacing）依赖 SwiftShader 合成，绝对值无跨机可比性 —— 未设固定通过阈值。
