# Spatial Science & GeoAI Algorithm Platform — 架构总览（ADR-0099）

## 职责

本平台是 `webgis-ai-agent` 的**科学方法层**。它回答：

> 给定一个空间分析能力与合法的地理证据，哪种科学上适当的算法可执行、
> 在什么假设下、用什么参数、哪个实现、产出什么 artifact、什么不确定性、
> 什么诊断、什么科学质量结论。

它**不负责**：数据传输、分布式查询优化、Map 产品 UI、会话规划、制图排版。

```text
User / GIS Product Intent
        ↓
Agent/Product Plane（Harness 规划、决策工作台、MapSpec）
        ↓  Scientific Capability Request
┌──────────────────────────────────────────────────┐
│ Spatial Science & GeoAI Algorithm Platform       │
│  Capability Registry → Algorithm Registry        │
│  Scientific Preconditions → Parameter Contracts  │
│  Algorithm Resolver（科学门 + 成本模型）           │
│  Uncertainty Framework → Scientific Evidence     │
│  Method References → Domain Packs                │
└──────────────────────────────────────────────────┘
        ↓  Execution Request（ToolRegistry 契约）
GeoCompute / Data Plane（ToolDispatchService、Data Fabric、栅格 runtime）
        ↓
Artifact + Scientific Evidence
        ↓
Agent / Map Product
```

## 核心不变式

```text
Capability ≠ Algorithm ≠ Implementation/Tool ≠ Execution Runtime ≠ Visualization
```

- **Capability**（`app/lib/gis/capabilities/`）：需要什么能力 —— 稳定词汇。
- **Algorithm**（`app/lib/gis/algorithms/`）：哪种科学方法 —— 假设、参数
  契约、CRS 类、前置条件、不确定性、出处、回退语义、成熟度。
- **Tool**（`app/tools/`）：谁来实现 —— 薄包装（validate → 调科学实现 →
  挂证据），不是第二算法实现。
- **Runtime**：在哪执行 —— ToolDispatchService / Data Fabric，本层不碰。

## 契约模块

| 模块 | 职责 |
|---|---|
| `parameter_contracts.py` | 类型化参数契约（default/min/max/enum/unit/数据依赖规则）；工具侧严格入口 `apply_contract`；契约随域包聚合 |
| `scientific_preconditions.py` | 命名前置条件 + 五值判定（PASS / PASS_WITH_WARNINGS / REQUIRES_TRANSFORM / INSUFFICIENT_DATA / INVALID_METHOD）；事实未知 → PASS（诚实缺省） |
| `crs_safety.py` | CRS 分类（EPSG:3857 是投影但**不是**局部度量）+ 纯计算 UTM/极方位推荐 |
| `scientific_errors.py` | 科学失败分类学（全部 subclass ValueError，兼容既有 dispatch 错误映射） |
| `uncertainty.py` | 类型化不确定性（scalar/field/raster/significance/sensitivity/validation/Monte-Carlo），封闭词表 |
| `scientific_evidence.py` | 有界科学证据块构造器（方法/版本/参数/假设/变换/回退分类/警告/诊断/不确定性/验证） |
| `method_references.py` | 规范方法出处（concise id → 引用；validate() 强制存在性） |

## 域包（ADR-0099 §34）

算法/能力/契约按域注册，中央文件不膨胀：

```text
app/lib/gis/algorithms/{data_access,geometry,aggregation,density,
  statistics,point_pattern,interpolation,network,terrain,raster,
  remote_sensing,temporal,decision}.py
app/lib/gis/capabilities/{...同构}.py
```

新算法 = 在自己的域模块注册 descriptor（+ 可选 `PARAMETER_CONTRACTS`）
→ 中央 registry 聚合与校验（`validate_gis_library` / parity 门 / manifest
指纹全部自动覆盖）。

## Resolver 科学门（ADR-0099 §10）

在既有硬门（native → 工具 → 几何 → artifact → 样本量 → 字段）之后：

1. **CRS 类门**：`crs_class` 声明与数据 CRS 分类冲突 → 拒绝 +
   `;transform=reproject to EPSG:xxxx` 建议（汇入
   `required_transformations`）。
2. **前置条件门**：声明条件逐一评估；PASS_WITH_WARNINGS 随 resolution
   上报（不阻断），其余判决拒绝并保留证据。
3. **回退语义**：每步 fallback 携带 `equivalent/approximation/proxy/
   degraded` 分类；显式点名的算法被硬门拒绝而由低优先候选顶上时，
   替补同样携带分类进 fallback_trail —— 「approximation 顶替显式请求」
   绝不静默。

## 成熟度模型（§33）

`runtime_status`（能否执行）与 `scientific_status`（验证强度）正交：

- `PRODUCTION`：native + 参数契约 + 方法出处 + conformance 测试（静态强制）。
- `VALIDATED`：conformance 测试存在（节点级校验到真实测试函数）。
- `EXPERIMENTAL`：有实现但覆盖薄。
- `DEPRECATED`：必须声明 fallback。

诚实降级示例：`network.service_area.simple`（速度表欧氏缓冲）对真实
路网等时圈是 **proxy** —— 注册表、结果证据、文档三处一致声明。

## 已知限制（诚实清单）

- 计划态 SAR 能力（speckle 过滤、辐射定标）如实登记为 `planned`，
  无 fake-native 实现。
- 批式克里金求解的浮点条件数限制记录在 descriptor `numerical_tolerance`。
- Web Mercator（EPSG:3857）作为工作 CRS 被接受但尺度失真在 limitations
  披露；局部度量分析建议 UTM（resolver 自动建议）。
- manifest 指纹覆盖契约 id/version/参数名集；默认值/边界值变化需手动
  提升 contract version 才会反映到指纹。
- MK/季节 MK 的 p 值假设序列独立（lag-1 秩自相关警告）；
  seasonal 版无预白化。
- Ripley K 的显著性需要自备固定种子 CSR 包络（实现返回 K/L 序列，
  不给 p 值）。

## 文档索引

- [CONTRACT_BACKBONE.md](CONTRACT_BACKBONE.md) — 域包实现者契约参考
- [ALGORITHM_CATALOG.md](ALGORITHM_CATALOG.md) — 注册表生成的算法目录
- [../adr/0099-spatial-science-geoai-platform-vnext.md](../adr/0099-spatial-science-geoai-platform-vnext.md)
