# GIS 工具能力对账(conformance)贡献者指南(G03)

> 面向给本仓库新增/修改 GIS 工具的贡献者:如何让你的工具元数据
> **真实、可对账、可解释**,以及如何用对账工具自查。

## 一条工具的全生命周期(单一真相链)

```
register()/@tool(app/tools/registry.py)     ← 唯一注册权威(_metadata 单一存储)
  → ToolDescriptor(app/tools/descriptor.py)  ← 只读投影 + 指纹
  → execution_catalog(app/lib/gis/)          ← 聚合投影(指纹+版本)
  → ToolCatalog / ToolSurfaceProjector       ← tier/关键词选择与 surface 投影
  → PiToolSpecV1(app/services/chat/)         ← Pi native ABI(#1620)
  → registry.dispatch() chokepoint           ← 执行闸(5 机器码)
  → ToolDispatchService                      ← ref-first 结果信封
```

关键纪律:**不要平行发明第二份元数据**。新工具只在 `register()` 的
kwargs 声明契约字段(未知 kwarg 注册期即报错);其余一切面都是投影。

## 8 面对账:你的工具会被这样检查

`app/lib/gis/tool_capability_conformance.py` 对每个工具产出
`ToolConformanceReport`,8 个消费面各给
`pass / warning / fail / not_evaluated` + 稳定 reason code:

| 面 | 检查什么 | 常见红点 |
|---|---|---|
| registration | descriptor 结构完整 + 悬空引用 | `fallback_tool` 指向未注册工具(fail);side_effect/network/deterministic/result_size_policy 未声明(warning) |
| search | ToolCatalog 激活语义 | tier-2 无 domains、或 domain 无 DOMAIN_KEYWORDS 覆盖(漏激活风险) |
| planner | capability/algorithm 链可解析 | 声明的 capability 不在词表(fail);声明无算法链支撑(warning,编排工具合法) |
| pi_native | 仅 7 前门:spec 可编译 + 字节预算 | schema 超 16KB 预算逐出(fail) |
| proxy | webgis_execute 可达 | status=planned(fail);hidden/external_unavailable(warning) |
| dispatch | 闸输入结构 | 凭证 id 非字符串/空(fail) |
| result | 输出契约声明 | result_size_policy=unknown、ref_offload 无产出面(warning;geojson_fc 族因 dispatch 结构性铸 ref 天然 pass) |
| science | args schema × ParameterContract | required/enum/range 两侧分叉、契约参数全家族无人暴露(warning) |

**not_evaluated ≠ pass**:没有证据就如实留白,不虚构覆盖。

## 自查命令(本地、离线、只读)

```bash
# 单工具对账(退出码 0=无 fail)
python scripts/check_tool_capability_conformance.py --tool <你的工具名>

# 全量摘要 + 机器可读
python scripts/check_tool_capability_conformance.py
python scripts/check_tool_capability_conformance.py --json

# warning 也视为红
python scripts/check_tool_capability_conformance.py --fail-on warning
```

测试内等价物:`tests/unit/gis_tool_conformance/`(引擎单测、活 registry
棘轮 `test_live_registry_audit.py`、离线 E2E 两场景、ripley 执行面回归)。
agent 侧:`catalog_conformance` 工具(tier-2,经真实 dispatch 链可达)。

## 新工具最小真实路径(端到端示例)

以 `ripley_k_analysis` 的修复为例(W5 审查清偿):

1. **descriptor**:`@tool(...)` 声明 side_effect/network/deterministic/
   result_size_policy/output_semantic_type(fail 面全绿的前提);
2. **参数契约**:若算法有 `parameter_contract_ref`,契约参数名/默认值
   **逐位对齐**工具签名(契约章程:与工具签名逐位对齐;家族共用契约时
   兄弟工具暴露即可豁免缺席);
3. **包装层透传**:改 `SpatialAnalyzer.*` 形参时,**工具调用点与包装层
   两处都要改**——W5 的 P0 就是只改了一处,静态对账全绿而 dispatch 必炸;
4. **消费惯例**:`spatial_operator` 包装的结果用 `.summary`/`.data`
   属性,不要下标访问;
5. **执行面测试**:至少一条经 `registry.dispatch()` 的调用测试
   (test_ripley_dispatch.py 是模板);
6. **生成物**:重跑 `python -m app.lib.gis.execution_catalog_docs`
   (docs/catalog 防漂移测试会拦)。

## 回退语义的既有码(不要新造)

回退/失败路径一律复用既有权威码(交叉引用表
`FALLBACK_CODE_XWALK`,parity 测试钉住):

| 语义 | 权威码 |
|---|---|
| 凭据缺席 | `credential_absent`(面逐出)/ `CREDENTIALS_REQUIRED`(dispatch) |
| 离线不可用 | `offline_network_tool` / `offline_network_required` |
| 方法不支持/未实现 | `TOOL_NOT_EXECUTABLE` |
| surface 过期 | `surface_stale` + `SURFACE_FINGERPRINT_MISMATCH` |
| 数据/参数不满足 | `SCHEMA_VALIDATION_REJECTED` / `parameter_contract_violation:<id>` |

确实没有生产者的新语义:先在 `dispatch_reason_codes.py` 登记并带真实
生产者,禁止空挂码位。

## 设计红线(为什么这样而不是那样)

- 对账引擎是**只读投影**,不反写、不新建可写 registry、不接管裁决:
  turn 级资格(pi_surface_receipt)与执行闸(registry.dispatch)权威不变;
- fail 不进启动 fail-fast(启动闸归属 runtime_manifest/ADR-0204);
  G03 的 fail 由 CLI 退出码、活 registry 棘轮测试与 catalog_conformance
  工具三处强制;
- 全量产出有界(≤1024 报告/≤512 工具清单)、排序确定、指纹跨进程稳定。
