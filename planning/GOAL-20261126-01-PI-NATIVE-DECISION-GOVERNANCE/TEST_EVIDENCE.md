# TEST_EVIDENCE — GOAL-20261126-01

> 命令 + 原始结果 + 通过/失败分列。skip/xfail 不冒充 pass。

## R0 干净基线（worktree @ 59eb3f527，改动前）

| 命令 | 结果 | 状态 |
|---|---|---|
| `pytest tests/unit/test_pi_vendor_capability_snapshot.py -q`（build 前） | 1 failed (`test_this_checkout_is_gitlink_at_v161`: entry_probe 缺 dist) + 22 passed | 环境性失败；build 后复验 passed |
| 同上（build 后单测复验） | 1 passed | ✅ |
| `pytest tests/unit/test_pi_event_mapper.py tests/unit/test_pi_v16x_diagnostics.py tests/unit/test_pi_native_surface.py -q` | **49 passed, 1 xfailed**（xfail= `query_local_poi`/`webgis_component_update` strict 前门钉） | ✅ |
| `python scripts/pi_doctor.py` | 六域 PASS：vendor(v1.6.1 entry_probe, manifest consistent, 14/14)/build/extension(2 tools)/surface(587 tools, native 7/7, deferred 580)/rpc(live)/env | ✅ |
| `ruff check app/services/chat/ app/agent_pi_bridge.py` | All checks passed | ✅ |
| `pytest tests/integration/test_pi_vendor_contract.py tests/integration/test_pi_schema_coercion_golden.py -q` | **18 passed**（build 后） | ✅ |

### R0 小结

- 干净基线 Pi 相关闸全绿：单测 73 passed + 1 xfailed；集成契约 18 passed；
  doctor 六域 PASS；Ruff（本线范围）零诊断。
- 唯一历史失败 = vendor 未 build 环境性失败（build 后复验绿），非代码缺陷。
- #2281/#2282 披露的历史诊断未复现于本线范围；全仓 Ruff 待 WP6 终验时按需跑。

## 披露项核对

- #2281 描述 29 项 Ruff 诊断 / #2282 描述 5 项既有诊断：**尚未本地复现归类**；
  本线范围 Ruff（app/services/chat/ + bridge）当前零诊断。后续若全仓跑出
  历史诊断，只记录不修复（非本线）。

## WP 期间证据

（每 WP 完成后追加：红→绿失败用例、旅程命令、正负例结果）

## WP5/终验证据（2026-11-27）

| 命令 | 结果 | 状态 |
|---|---|---|
| `pytest tests/unit/test_pi_decision.py tests/unit/test_pi_decision_wiring.py tests/unit/test_pi_schema_poi_typed.py -q`（review 修复后） | **74 passed** | ✅ |
| `pytest tests/cartography/test_map_plan_tools_v1.py -q` | 6 passed | ✅ |
| `pytest tests/unit/test_pi_native_surface.py tests/unit/test_local_stats_data.py tests/unit/test_pi_tool_spec.py tests/unit/test_pi_surface_parity.py -q` | 62 passed, 1 xfailed | ✅ |
| `pytest tests/test_tool_argument_aliases.py tests/test_tool_meta_contract.py -q` | 10 passed, 2 failed（meta_contract 2 项 = **base 同样失败**，预存） | ⚠️ 预存 |
| `ruff check`（本线全部触及文件） | All checks passed | ✅ |
| `PI_JOURNEY_E2E=1 pytest tests/integration/test_pi_stub_llm_journeys.py -q` | 本分支 FAIL（J6b nearest_facility 未激活）；**干净 base detach worktree + 同一 vendor dist 复跑同样 FAIL** → 预存/环境性，非本线回归 | ⚠️ 预存（精确命令与对照见 REVIEW.md） |

## 未实测/诚实披露

- 真实 classifier provider（typesafe/openai decisions 等）无凭据 → 未实测；
  传输层注入点已留（`set_decision_assist_transport`），生产 classify 传输
  未接线。stub 传输覆盖编排逻辑（单测），vendor `ctx.modelRegistry.classify`
  的 wire 级消费未走通（journey 车道预存红，未阻塞记录）。
- 模板候选域 tie 探测在 recipe 选择后（plan_from_intent 内部）→ 本期未接线
  （SCOPE 已记录）；澄清 bool 域以「默认项翻转」机制覆盖（单测）。
- Windows/POSIX 停机矩阵、多 worker recycle：本线未新增 spawn 面改动，
  沿用既有契约测试；未重测全 OS 矩阵（如实披露）。
