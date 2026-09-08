# 05 Scale & Backend Gaps（规模/后端缺口汇总）

> 逐域详表见 domains/*.md §5。Wave 1 Backend SDK（ADR-0117）提供声明位；
> 存量算法的窗口/envelope 上收按域推进。

## Backend 选择层现状

- `select_backend`（backend_selection.py）：声明序偏好 + 规模窗口命中 +
  出窗降级披露 + ResourceEnvelope 估算（字节/对预算/硬上限预警）+
  BackendEvidence 结构化证据。
- 基线状态：域内 backend_variants 声明率极低（地形 26 个=0、光学 RS=0、
  点格局=0、网络部分、地统计 2 变体无窗口→本分支已补窗口）。

## 已处置（本分支）

| 项 | 状态 |
|---|---|
| kriging numpy_batched/scipy_linalg 窗口 + exact 分类 | ✅ |
| interpolation 全域 complexity 声明 | ✅（14 descriptor） |
| openness/horizon/SVF 内存重构 + 联合包络 | ✅ |
| viewshed 像元护栏 | ✅ |
| indicator 阈值预算守卫语义修正 | ✅ |
| fill→下游水文链路（persist_filled） | ✅ |

## 存量缺口（按域，⏳ = 记录待办）

### 地形/水文
- fill 堆与 accumulation 纯 Python 循环（10M+ 像元分钟级）；numba 在
  依赖中零使用；GDAL DEMProcessing 是现成第二变体（词表已含 gdal/numba）。
- windowed.py（带 halo 分块 + 取消 token）地形域零消费；局部算子
  （slope/TPI/GLCM 类）天然可迁；全局拓扑类需分块 Priority-Flood。
- 长循环无取消点（CancellationToken 未下沉 lib 层签名）——
  新增算法默认在 chunk 边界检查；存量按域逐步补。

### 地统计
- approximate/streaming kriging 变体缺失（coarse-to-fine/tapering/
  自适应 k）——descriptor 声明位已就绪（Wave 1），实现为 P2。
- 1.5M 目标格走 ref_offload 通道（求解内存有界，streaming 非急需）。

### 光学
- GLCM 是唯一真分块实现；rs_v3 批次 16M 上限实际被内联 4M 工具通道
  钉死；文件路径窗口化通道仅 ndvi 族有（其余 V3 批次算法只吃内联数组）
  ——「栅格工件路径」hint 与实现不一致（已记录契约缺口）。

### 网络
- heuristic 族（LA/accessibility/gravity/huff）无 n×m OD 代价矩阵规模闸
  （MILP 族有 25000/500 闸）——不对称（第二批 agent 补齐）。
- OD matrix/service area scalable backend（分块 + ref 通道）为 P1。

### 点格局
- K 族 pair 预算靠行步幅（无偏但方差增大）；backend_variants=0。

## Benchmark Manifest（Wave 10）

现状：tests/benchmarks/test_backend_scale_decisions.py（16 用例）+
test_spatial_science_benchmarks.py（13 用例）——类型化拒绝 + count/bytes
结构门，无机器可读 manifest。计划：descriptor（ResourceEnvelope +
complexity + 变体窗口）→ 生成器投影 benchmark manifest（沿用
gen_science_catalog 模式，防手工漂移）。
