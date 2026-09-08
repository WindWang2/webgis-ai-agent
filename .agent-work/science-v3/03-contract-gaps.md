# 03 Contract Gaps（契约缺口汇总）

> 来源：8 份域审计 §2/§3 + 基建审计（domains/08-registry-contracts.md §2）。
> 状态标记：✅ 本分支已修 · 🔧 进行中（实现 agent）· ⏳ 记录待办。

## 中央契约（registry/validate/manifest）

| 缺口 | 状态 |
|---|---|
| G1 declared uncertainty → producer test 无机器校验 | ✅ uncertainty_producer_tests + validate()（Wave 8） |
| G2 approximate 布尔无分类学 | ✅ ApproximationClass 词表 + 一致性校验（Wave 1） |
| G3 numerical_tolerance 自由文本不可消费 | ✅ NumericalTolerance 结构化字段（Wave 1） |
| G4 资源护栏散落实现层、无声明位 | ✅ ResourceEnvelope 声明位 + backend_selection 消费（Wave 1）；存量算法逐个上收为 ⏳ |
| G5 CancellationProfile 无声明位 | ✅ 词表字段（Wave 1）；lib 重循环逐步加取消点为 ⏳ |
| G6 complexity 自由文本 | 部分：插值域 14 descriptor 已补；其余域 ⏳ |
| manifest 指纹：approximation_class 进投影 | ✅（缺省值投影不变） |

## 域级契约缺口

| 域 | 缺口 | 状态 |
|---|---|---|
| 地统计 | kriging 契约 matern_smoothness 死参数（工具不可达） | ⏳（需词表扩 matern + 工具补参，或契约回收） |
| 地统计 | kriging/idw 工具不走 apply_contract（min/max 不生效） | ⏳ |
| 地统计 | idw 契约 power type=integer vs 实现 float (0,5] | ⏳ |
| 地统计 | indicator 契约枚举与 auto 实际选型语义不对称 | ⏳（已披露） |
| 地形 | slope/aspect/hillshade 无参数契约 | ⏳ |
| 地形 | dinf_analysis 契约无 flat_routing（与 flow_analysis v2 不对称） | 部分：fill→下游链路已通（persist_filled） |
| 地形 | fill epsilon 累计抬升量未披露 | ⏳ |
| 光学 | NDWI 同名异式（McFeeters vs Gao 跨路径） | 🔧 NDWI 拆名进行中 |
| 光学 | 输入反射率值域校验（valid_range 无消费者） | ⏳ |
| 网络 | heuristic 族无 n×m 规模闸（与 MILP 25000/500 不对称） | ⏳（第二批 agent） |
| 统计 | join_count descriptor free sampling 措辞与实现 non-free 不符 | 🔧 进行中 |
| 统计 | h3_hotspot tool_candidates 指向不产出 Gi* 的工具 | 🔧 进行中 |
| SAR | vh_ratio 文案宣称不存在的 dB 路径 | ⏳（第三批 agent） |
| SAR | 工具面 4M 与 lib 16M 双闸不一致；「栅格工件路径」hint 无实现 | ⏳ |
| 点格局 | temporal.hotspot descriptor 语义失配（宣称分箱计数，实为 ST-DBSCAN） | ⏳（第二批 agent，随 EHA 修复） |
| 点格局 | temporal.aggregate / stats.geodetector descriptor 元数据裸奔 | ⏳ |
| 通用 | 域内 uncertainty_outputs 声明不产出（hotspot 族/indicator 等） | 🔧/⏳ 分域处理 |
| 通用 | fallback_semantics 只表达等价类、不表达规模窗口差异 | ⏳（设计讨论级） |

## 契约体系健康度结论

validate()（capability/artifact/tool/fallback/seed/词表/成熟度/节点 AST）
+ 参数 parity 门 + catalog 字节级 parity + manifest 指纹构成四层防漂移；
本分支全部 additive 扩展通过同一套门。剩余缺口多为「声明-执行弱耦合」类，
不阻塞解析正确性，已按优先级入 02-planned-algorithms.md。
