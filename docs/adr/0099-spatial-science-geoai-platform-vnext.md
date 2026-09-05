# ADR-0099: Spatial Science & GeoAI Algorithm Platform VNext

## 状态

Accepted（2026-09-04，feat/spatial-science-geoai-vnext）

## 背景

webgis-ai-agent 已有三层雏形：Product/Agent Plane（Harness 规划）、
`app/lib/gis`（capability/algorithm/artifact 注册表 + resolver + runtime
manifest）、Data/Execution Plane（ToolRegistry / ToolDispatchService /
Data Fabric）。但「科学方法层」仍是残缺的：

- `parameter_contract_ref` 是前向声明 —— 全库**零消费方**，参数默认值散落
  在各工具签名里（magic parameter）；
- `crs_requirements` / `unit_requirements` 只有词表校验，resolver 从不
  读取 —— 度/米混淆只能靠各算法内部自律；
- 没有可复用的科学前置条件库（样本量/方差/投影 CRS/时序观测数/波段语义
  只能在实现内各自硬编码）；
- 不确定性没有类型化契约（克里金方差、置换 p 值、MC 分布各自为政）；
- 方法出处（Moran 1950、Horn 1981……）无处登记，科学权威性无从审计；
- fallback 只有 from/to，没有科学等价性分类（网络可达性不可用时静默退到
  欧氏缓冲是被禁止但不被表达的）。

本 ADR 定义 Spatial Science Platform 的契约骨架。它与 ADR-0080（统一
runtime）、ADR-0083（成本感知解析）、ADR-0089（栅格 runtime v3）正交：
那些管「能不能执行」，本 ADR 管「科学上该不该、以什么假设执行」。

## 决策

### 1. 职责边界（三层不变式）

```text
Capability（做什么） ≠ Algorithm（哪种方法） ≠ Implementation/Tool
（谁来实现） ≠ Execution Runtime（在哪执行） ≠ Visualization（怎么画）
```

Science Platform 只持有**方法语义**（前提、参数、单位、不确定性、出处、
fallback 科学等价性），不执行、不调度、不建第二 artifact store、不写
第二 MapSpec。执行请求继续走 ToolRegistry → GeoCompute。

### 2. 六个新契约模块（app/lib/gis/）

| 模块 | 职责 |
|---|---|
| `parameter_contracts.py` | 类型化参数契约注册表：default/min/max/enum/unit/数据依赖默认；`apply_contract` 供工具收敛参数；注册表校验 |
| `scientific_preconditions.py` | 可复用前置条件库 + 判定引擎：PASS / PASS_WITH_WARNINGS / REQUIRES_TRANSFORM / INSUFFICIENT_DATA / INVALID_METHOD |
| `scientific_errors.py` | 科学错误分类学（InsufficientSamples / InvalidCRS / DegenerateData…，均 subclass ValueError 以兼容既有 dispatch 错误映射） |
| `uncertainty.py` | 类型化不确定性（ScalarUncertainty / FieldUncertainty / RasterUncertainty / StatisticalSignificance / SensitivityEnvelope / ValidationMetrics） |
| `scientific_evidence.py` | 统一科学证据块构造器（算法/版本/参数/假设/变换/fallback 分类/警告/诊断/不确定性/验证） |
| `method_references.py` | 规范方法出处登记（concise id → 引用；全文放 docs/science/） |
| `crs_safety.py` | CRS 分类（geographic / projected / projected_local_metric / unknown）+ 度量 CRS 推荐（UTM 解析纯计算，pyproj 兜底） |

### 3. AlgorithmDescriptor VNext（全部 additive、有默认值）

新增字段（每个都必须有消费方或校验器，杜绝学术百科式元数据）：

```text
algorithm_family            # 文档生成/分组
method_references[]         # 出处 id，校验存在性
assumptions[]/limitations[] # 有界；进证据块
crs_class                   # CRS_AGNOSTIC/GEOGRAPHIC_OK/PROJECTED_REQUIRED/
                            # LOCAL_METRIC_REQUIRED/GEODESIC/RASTER_GRID
scientific_preconditions[]  # 前置条件 id（可参数化），resolver 执行门
uncertainty_outputs[]       # 不确定性类型词表，校验成员
random_seed_policy          # deterministic/fixed_seed/caller_seeded/unseeded
numerical_tolerance         # 容差声明（自由文本，有界）
scientific_status           # EXPERIMENTAL/VALIDATED/PRODUCTION/DEPRECATED
conformance_tests[]         # 测试节点 id；仓库内文件存在性校验
backend_variants[]          # 同算法多实现（backend 词表封闭）
fallback_semantics{}        # target → equivalent/approximation/proxy/
                            # degraded/not_allowed
```

`scientific_status` 与 `runtime_status` 正交：后者答「能否执行」，前者答
「验证强度」。PRODUCTION 的静态必要条件：native + 参数契约 + 方法出处 +
conformance tests（由 validate() 强制）。

### 4. Resolver 科学门

在既有硬门（native/工具/几何/artifact/样本量/字段）之后追加：

- **CRS 类门**：`crs_class` 与画像 CRS 分类冲突 → 拒绝
  （`crs_class_mismatch`），地理 CRS + PROJECTED_REQUIRED 场景在
  `required_transformations` 里给出重投影建议；
- **前置条件门**：descriptor 声明的条件逐一评估，PASS_WITH_WARNINGS 进
  `scientific_warnings`（不阻断），REQUIRES_TRANSFORM/INSUFFICIENT_DATA/
  INVALID_METHOD 拒绝并保留证据；
- **fallback 语义**：FallbackStep 携带 `semantics` 分类，proxy/degraded
  必须显式出现在证据里（「接近性代理，非可达性」类诚实降级）。

未声明新字段的存量算法行为逐位不变（门只在声明时激活）。

### 5. 指纹（manifest v3）

`_project_algorithm` 追加 crs_class / scientific_status /
parameter_contract_ref / scientific_preconditions / fallback_semantics；
新增 `parameter_contracts` 投影（id → version + 参数名集）。契约或科学
门语义变化 ⇒ 指纹变化 ⇒ 旧 plan 标记 stale（#1084 既有守卫语义不变）。

### 6. 算法/工具参数一致性门（§43 parity）

`validate_gis_library` 追加：每个声明参数契约的算法，其候选工具的
OpenAI schema 必须包含契约的全部 required 参数名 —— 参数错配是死契约的
最强信号。

## 后果

- 新字段的默认值保证存量 52 算法、42 能力行为与指纹演进一次到位；
- 域算法包（`app/lib/gis/algorithms/`）逐步落地，中央 registry 只做聚合
  与校验（避免单文件十万行注册表）；
- 工具层的新职责边界：validate → resolve refs → 调科学实现 → 挂证据块；
  科学逻辑回收到 lib，工具不再各自当第二个算法实现。

## 反目标（重申）

不做 LLM resolver、不把 planned 伪装 native、不把视觉热力叫分析 KDE、
不把 count 当 rate/density、不在 band math 里执行任意 Python、不把度当米。
