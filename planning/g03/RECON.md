# G03 RECON — Pi × GIS Harness 工具能力治理/契约一致性/真实可调用性

- 基准: `origin/master` = `b1b2ec23f10f251ddf8cf4d9fc0baabd03117013`(2026-10-21 经 gh API 复核,本地 refs 一致)
- 详尽 file:line 证据: [RECON_DATA.md](RECON_DATA.md)(勘察子代理 A 产出,~300 行)
- worktree: `/home/kevin/project/webgis-g03-tool-conformance` / 分支 `feat/g03-gis-tool-conformance-vnext`

## 1. 真实入口调用链(现有)

```
app/tools/registry.py register()/@tool        ← 唯一注册权威(_metadata 单一存储)
  → app/tools/descriptor.py ToolDescriptor     ← 只读投影 + 指纹(schema/descriptor/manifest)
  → app/lib/gis/execution_catalog.py           ← CatalogEntry 聚合投影(指纹+版本)
  → app/services/tool_catalog.py ToolCatalog   ← tier/关键词选择面(DOMAIN_KEYWORDS 手写副本)
  → app/services/tool_surface_v2.py            ← surface 投影(reasons/dropped/fingerprint)
  → app/services/chat/pi_tool_spec.py          ← PiToolSpecV1(#1620 ABI V2,fail-honest 投影)
  → app/services/chat/pi_native_surface.py     ← 7 native 前门 + webgis_execute 长尾代理
  → app/services/chat/pi_surface_receipt.py    ← per-turn qualification(8 DROP 码)
  → app/tools/registry.py dispatch()           ← 执行闸权威(5 机器码 chokepoint)
  → app/services/tool_dispatch_service.py      ← ToolDispatchResult(ref-first geojson_ref)
  → app/services/chat/pi_result_abi.py         ← ≤4KB result envelope(refs/error class+code)
```

## 2. 现状结论(去重后)

**已存在且直接复用(不重做)**:
- Pi Native GIS Tool ABI V2 全链(#1620):typed spec / qualification / stale receipt / result ABI
- H01(#1756):`dispatch_reason_codes.py` 机器码单一事实源 + parity 测试
- 声明面 conformance:`capability_conformance.py`(capability↔tool 绑定)、`execution_catalog_conformance.py`(目录链)
- 参数契约注册表:`parameter_contracts.py`(契约 id=parameter_contract_ref,单位封闭词表)
- 发现工具面:`catalog_discovery_tools.py`(catalog_discover/catalog_lookup/capability_runtime_status,tier-2 只读)
- 覆盖率闸:`scripts/check_tool_descriptor_coverage.py`(95% 阈值)

**真实缺口(G03 填补,均有 RECON_DATA 证据)**:
1. **无跨面 per-tool 对账报告**——8 个消费面(注册/搜索/规划/Pi native/proxy/dispatch/结果/科学语义)没有统一的 pass/warning/fail/not_evaluated 机器可读投影;现有校验器各查一段
2. **无 schema↔parameter_contract 离线交叉校验**(required/enum/范围/单位两侧一致性零对账)
3. **无 per-tool Pi 可达性断言**(proxy 构造性可达但无断言;native 面逐出只留 reason)
4. **side_effect 未声明只受 95% 覆盖率软闸**,长尾工具无逐具名披露
5. **descriptor fallback_tool/deprecation_of 悬空引用无 per-tool 检查**(catalog 条目级有,descriptor 级无)
6. **`tests/unit/gis_tool_conformance/` 不存在**(本方向自有测试目录)
7. **无 catalog/Pi-surface 对账 CLI**(conformance 均为纯函数,无 scripts 入口)
8. **手写副本漂移面**:tool_catalog DOMAIN_KEYWORDS / index.mjs FALLBACK_NATIVE / 两套 slim 预算口径

## 3. 与 open PR 的交叠(详矩阵见 PR_OVERLAP_MATRIX.md)

- **#1758**(OPEN,~429 文件):点名交叠 4 文件,其中 3 个(evidence.py/geocompute_tools.py/cartography_tools.py)本方向**不触碰**;`execution_catalog_staleness.py` 的 #1758 改动是容错加固,本方向不改该文件,语义兼容。另有 2 处 hunk 级交叠(spatial_stats.py、pyproject.toml)在 PR_OVERLAP_MATRIX 修正披露(W5 review 发现)
- **#1701/#1698**(dependabot):requirements 上界,零交叠
- 已 merge 的 #1620/#1755/#1756/#1757:是本方向的**依赖基线**(复用其 ABI 与错误码表),非竞争

## 4. 审计项定位(WP-A.2 → 检查面映射)

| 审计项 | 现状 | G03 落点 |
|---|---|---|
| 已声明未注册 | 已覆盖(capability_id_dangling fatal + parity 测试) | 复用,进报告 planner face |
| 已注册不可经 Pi 触达 | **无检查** | proxy face 断言(registered + 非 planned = proxy 可达) |
| schema required/enum/单位不一致 | **无离线交叉校验** | science face:args schema × parameter_contract |
| 结果 envelope 遗失错误/ref | 无离线检查(运行时各有闸) | result face 结构检查 |
| side effect 未声明 | 仅 95% 软闸 | registration face 逐工具披露 |
| 需要凭据却暴露可用 | 仅运行时 qualification | dispatch face 结构检查 + 披露 |
| 参数在 adapter 被忽略 | 无静态检查 | deferred(需运行时插桩,超出离线对账诚实边界;在 DECISIONS 记录) |

## 5. 风险清单

1. 活 registry 全量审计可能暴露既有 fail(悬空 fallback 等)→ 是真实发现,须逐条定位/修复或如实披露,不得放松检查掩盖
2. 新增 tier-2 工具(catalog_conformance)影响 surface 字节预算/覆盖率分母 → 需全量本地相关测试对照
3. #1758 合并时段落冲突 → 本方向改动集中在无交叠文件;若须改 geocompute_tools.py 等,PR 明确披露依赖
4. 后端全量套件 ~80% 处确定性挂起(#1753)→ 只跑目标目录测试,跑前对照 clean master 基线
5. 大 registry 审计性能 → 报告有界(≤512 工具/条)、纯函数、无 I/O
