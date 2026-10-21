# H01 CONTRACT_MATRIX — 文件归属与冲突边界

## H01 拥有（本 PR 可改）

| 文件 | 改动类型 |
|---|---|
| `app/services/chat/pi_result_abi.py` | 修复 evidence 投影键 + error class 词表（additive） |
| `app/services/chat/pi_tool_spec.py` | additive `required_permission` 字段投影 |
| `app/services/chat/pi_native_surface.py` | disclosure 投影（additive，有界+开关） |
| `app/services/chat/pi_turn_context.py` | 披露块消费（additive） |
| `app/services/chat/pi_surface_receipt.py` | 新 drop code 常量（additive） |
| `app/agent_pi_bridge.py` | not-found/tier-3 拒绝机器码（additive，仅拒绝路径） |
| `app/services/gis_harness/completion/contracts.py` | additive 消费新 workflow_contract 键 + 新 F_ 码 |
| `app/services/gis_harness/planner.py` | finalize additive 写入（新键，不改既有行为） |
| `app/services/gis_harness/workflow_compiler.py` | additive 透传（若 finalize 需要） |
| `app/services/gis_harness/`（H01 新子模块，如 reason 对照常量） | 新文件 |
| `tests/unit/gis_harness/test_h01_*.py`、`tests/unit/test_pi_h01_*.py` | 新测试 |
| `tests/…/golden/h01/`（如需） | 新 golden |
| `docs/adr/0224-*.md`（增补 note）、`docs/gis-harness.md`、`CHANGELOG.md` | 文档 |
| `planning/h01-harness-capability-foundation/**` | 本目录 |

## H02 独占（禁改）

`app/services/gis_harness/template_selector.py`、`template_catalog.py`、
`product_completeness.py`、`product_shapes.py`、`app/lib/cartography/**`、
`frontend/lib/carto-ir/`、`frontend/lib/mapspec-runtime/`、
`frontend/lib/render-protocol/`、`frontend/components/`（制图画布）、
`app/services/mapspec/`（表现/渲染）、制图导出与金样例目录。

## 共享只读（可 import，不改结构）

`app/tools/registry.py`、`app/tools/descriptor.py`、
`app/services/tool_dispatch_service.py`、`app/services/gis_harness/data_qualification.py`、
`fallback_v3.py`、`capability_bind.py`、`capability_reasons.py`、
`runtime_situation.py`、MapSpec/RenderWork/Interaction IR/Requirement IR 全链。

## INTERFACE_REQUESTS（跨方向需求登记处）

- （暂无；如 finalize 需要产品组合侧新字段，先在此登记再走薄适配。）

## 在途 PR 冲突复核

- 2026-10-20 fetch：开放 PR 仅 #1695–#1701（dependabot），零交叠。
- PR 创建前将再次 `git fetch` + `gh pr list` 复核并更新此节。
