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
