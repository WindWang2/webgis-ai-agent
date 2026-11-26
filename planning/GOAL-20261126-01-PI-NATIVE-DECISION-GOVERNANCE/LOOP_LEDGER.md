# LOOP_LEDGER — GOAL-20261126-01

> 每轮：事实 / 验证 / 通过？/ 下一步。上下文恢复时先读本文件 + git diff。

## R1（2026-11-27）B 段复核 + E1 基线证据

**事实**
- origin/master = `59eb3f527`（#2282 后），fetch 曾 TLS 抖动、重试成功。
- vendor/pi gitlink @ master = `ef4a1cfc` = v1.6.1；上游最新 stable = v1.6.1
  （2026-11-25，无 prerelease/draft/新 tag）→ 保留 pin。
- PR 去重：开放 0；#2265–#2282 全 merged；本方向无他人在做。
- worktree `webgis-pi-native-decision-governance-20261126T172144Z`，
  分支 `zcode/goal-pi-native-decision-governance-20261126-172144`。
- 独立 venv（uv, py3.12.15, requirements.lock + requirements-dev.txt + ruff==0.17.6 对齐主仓）。
- vendor build 完成（build-vendor-pi.sh exit 0）。
- 三探查报告完成（Pi surface / schema 链 / 澄清上下文），产出
  `WP1_CAPABILITY_LEDGER.md`（14 能力 × 消费状态 × 处置）。

**验证（R0 基线）**
- `pytest tests/unit/test_pi_vendor_capability_snapshot.py`：首轮 1 failed
  （`test_this_checkout_is_gitlink_at_v161`）＝ vendor 未 build 的环境性失败；
  build 后单测复验 **passed**（该文件其余 22 项首轮已过）。
- `pytest tests/unit/test_pi_event_mapper.py test_pi_v16x_diagnostics.py
  test_pi_native_surface.py`：**49 passed, 1 xfailed**（xfail = C20 前门钉子）。
- `pi_doctor`：六域全 PASS（v1.6.1 entry_probe；manifest consistent；14/14；
  surface 587 tools native 7/7 deferred 580；rpc live handshake/turn/settled ok）。
- Ruff `app/services/chat/ app/agent_pi_bridge.py`：All checks passed。

**通过？** B 段 ✅；R0 部分（vendor contract/golden 车道后台运行中）。

**下一步**
1. 等 vendor contract + golden 车道结果 → 回填 TEST_EVIDENCE.md。
2. WP2 失败用例先行（decision 越权拒绝），随后实现 `pi_decision.py`。
3. WP3 单一来源修复 `query_local_poi` args_model（先红后绿）。

## 资源与环境记录

- 主机 16 核，load≈10（五线并发）；本线 pytest 单进程；vendor build 独立
  worktree 目录；无共享可写 node_modules。
- 单测全文件约 2.5–4.5 min（含 entry_probe spawn），集成车道待实测。

## R2（2026-11-27）WP2 + WP3 实现

**事实**
- WP2 核心提交 6f60d2c35（pi_decision.py + 46 用例）；接线提交 f65abd221
  （pi_decision_wiring.py + map_plan_tools 接线 + 8 用例 + 既有回归 6 passed）。
- WP3 提交 6c58d83bc（query_local_poi typed args_model，bare 桶清零；
  xfail reason 同步；15 用例；回归 62 passed + 1 xfailed）。
- 架构裁定：classify 不适合 dispatch 内同步往返（扩展被占用）→ opt-in
  协议 + 传输注入点留白，如实披露；docs/PI_UPSTREAM_COMPAT_MATRIX.md §6 已记。

**验证**
- tests/unit/test_pi_decision.py 46 passed；test_pi_decision_wiring.py 8 passed；
  test_pi_schema_poi_typed.py 15 passed；test_pi_native_surface.py 14 passed +
  1 xfailed；test_local_stats_data/test_pi_tool_spec/test_pi_surface_parity 62 passed；
  cartography/test_map_plan_tools_v1.py 6 passed。Ruff 全绿。
- stub journey 旗舰车道后台运行中（PI_JOURNEY_E2E=1）。

**通过？** WP2/WP3 本地闸 ✅（journey 待回填）。

**下一步**：WP4 审计结论落账 → 独立 review 子代理 → 修复 → PR。
