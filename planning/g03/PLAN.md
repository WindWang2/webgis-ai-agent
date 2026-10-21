# G03 PLAN — 工具能力契约对账(W0 设计定稿)

## 使命

单一方向:让 GIS 工具/算法/技能的元数据**真实、可对账、可解释**——工具可发现、可选、可运行、失败可解释。不新做 ABI、不建第二 registry、不从 Pi 分叉执行宿主。

## 核心交付物

### D1. `app/lib/gis/tool_capability_conformance.py`(W1,核心引擎)

纯函数、确定性、有界、零 I/O 的 **per-tool × 8 面对账引擎**(风格对齐 `capability_conformance.py`):

- 面(faces):`registration` / `search` / `planner` / `pi_native` / `proxy` / `dispatch` / `result` / `science`
- 每面裁决 `pass | warning | fail | not_evaluated` + 稳定 reason code + 有界 detail + 来源面(source authority)
- overall = fail > warning > (全 not_evaluated → not_evaluated) > pass
- 报告指纹(canonical JSON SHA-256 短摘要)+ 来源锚 registry generation
- 输入全部依赖注入(registry / capability_ids / algorithms / parameter_contracts / native_tool_names / domain_keywords / spec_compiler),不读全局单例 → 可测、可离线、跨进程确定
- 便捷入口 `audit_live_registry()`:从既有单例收集 facts(只读)

**红线**:只是既有 registry 的有界投影;绝不反写;不新建可写 registry;不替代 runtime_manifest 启动闸(报告是披露面,fail 不进 fail-fast——启动闸归属不变)。

### D2. `scripts/check_tool_capability_conformance.py`(W2)

只读离线 CLI:`--tool NAME`(单工具)/ 默认全量;`--json`(机器可读)/ 人读表格;退出码:任何 fail → 1(默认),`--fail-on warning` 收紧;无网络、无副作用。

### D3. `catalog_conformance` tier-2 工具(W2,catalog_discovery_tools.py 增量)

与 catalog_discover/catalog_lookup 同款模式的第 4 个只读工具:agent 可经真实调用链(descriptor→registration→Pi 投影→dispatch→receipt)取得单工具有界对账报告 → 满足「conformance evidence 接到真实一次工具调用」。

### D4. typed fallback 码表交叉引用(W2)

`FALLBACK_CODE_XWALK` 冻结映射:credential_absent/offline/unsupported_method/stale_surface/data_requirement_missing → 既有权威码(pi_surface_receipt DROP 码、dispatch_reason_codes、parameter_contract_violation 前缀)。**不新增 dispatch 码**(无新生产者不造码;映射表钉测试)。详见 DECISIONS.md D3。

### D5. `tests/unit/gis_tool_conformance/`(W1/W3)

- `test_engine_faces.py` — 每面 × 每 reason code 的合成 registry 红→绿测试
- `test_engine_determinism.py` — 指纹/排序/有界性(WP-D.12)
- `test_live_registry_audit.py` — 活 registry 全量审计基线(零 fail 门槛,warning 如实计数)
- `test_catalog_conformance_tool.py` — 新工具经 registry.dispatch 真实调用
- `test_fallback_code_xwalk.py` — 码表 parity(对照 dispatch_reason_codes/pi_surface_receipt 词表)
- `test_scale_synthetic.py` — 1000 合成工具规模/上界(WP-D.12)
- `test_e2e_poi_scenario.py` / `test_e2e_raster_ndvi_scenario.py` — 两个离线端到端场景(WP-D.13,经真实 ToolDispatchService,fake provider;子代理 B 承担)

### D6. 贡献者指南(W6)

`docs/dev/tool-conformance.md`:descriptor→registration→Pi projection→dispatch→receipt→MapSpec 结果的最小真实工具路径 + CLI 用法 + conformance 纳入流程。

## Wave 计划

| Wave | 内容 | 完成判据 |
|---|---|---|
| W0 | RECON/PLAN/矩阵/测试清单(本文档组) | 文件落盘,commit |
| W1 | D1 引擎 + D5 引擎测试(红→绿) | 新测试全绿,既有相关测试不红 |
| W2 | D2 CLI + D3 工具 + D4 码表 + 接线测试 | CLI 实跑退出码记录;dispatch 真实调用测试绿 |
| W3 | D5 E2E 两场景 + 规模/并发可靠性(子代理 B) | 场景断言真实链路(ref-first、错误码、无泄漏) |
| W4 | 性能/字节预算对照 + 活 registry 审计发现清偿 | 报告有证据;发现逐条 done/deferred |
| W5 | 独立 review → fix → 复审(子代理 C,两轮) | 无 P0/P1 遗留 |
| W6 | D6 文档 + 终报 + PR(不合并) | PR URL 落盘 PR.md |

## 工作包对照(doneness 台账)

- WP-A(对账矩阵):D1 覆盖 8 面;D5 live audit 落地 → **done**
- WP-B(契约守护/准入):D1 各面检查 + D4 码表;Pi native/proxy 分工如实投影(qualification 是运行时权威,报告只披露不裁决) → **done**
- WP-C(生产接线/开发体验):D2/D3/D6 → **done**;「单一工具更新触发精准影响面测试」= pytest 按 -k tool 名选择 + CLI --tool → **done**
- WP-D(可靠/性能/兼容):D5 determinism/scale/E2E → **done**;「上千次 surface 激活」离线合成 = test_scale_synthetic 做 1000 工具审计 + 1000 次单工具审计 → **done**;「参数在 adapter 被忽略」静态检查 → **deferred**(无插桩点,离线对账不可诚实判定;DECISIONS D5)

## 明确非目标(与 prompt §4 一致)

新 Agent 宿主 / 新地图引擎 / 新可写 registry / 全仓盲扫修 bug / 包揽 #1758 backlog / 未经确认的算法替换 / 线上 CI 依赖 / 虚构测试通过。
