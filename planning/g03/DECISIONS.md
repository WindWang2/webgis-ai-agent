# G03 DECISIONS(设计决策台账)

## D1. 引擎位置:`app/lib/gis/tool_capability_conformance.py`(新建,不改 capability_conformance.py)

理由:capability_conformance.py 是 ADR-0204 的声明面校验器,被 runtime_manifest 消费,行为契约钉死;G03 的 8 面对账是新增投影面,独立文件避免语义混合。两文件同风格(纯函数/有界/确定性),后者可复用前者的 gap 语义(`_metadata_gaps` 逻辑重新实现为只读投影,不 import 私有函数)。

## D2. fail 不进启动 fail-fast 闸

runtime_manifest 的 fatal 语义 = 「引用破损永远不可解析」,归属 ADR-0204/0201。G03 报告的 fail(如 fallback_tool 悬空、capability 悬空)按同一哲学**应该**是硬事实,但把新 fatal 码接进 compile_runtime_manifest 会改变启动契约、扩大爆炸半径,且与 #1758 的 runtime 区域潜在冲突。决策:G03 fail 由 (a) CLI 退出码 (b) `test_live_registry_audit.py` 零 fail 门槛 (c) catalog_conformance 工具披露 三处强制;启动闸不动。capability 悬空等既有 fatal 语义在 planner face 镜像为 fail(与 manifest 同源同判,不产生第二真相——判定条件逐位复用词表成员检查)。

## D3. 不新增 dispatch 机器码;建 FALLBACK_CODE_XWALK 冻结映射

`unsupported_method`/`data_requirement_missing` 全仓无生产者(RECON_DATA §B)。无生产者造码 = 平行发明词表,违反 SSOT。既有码已覆盖语义:
- credential_absent → DROP 码(pi_surface_receipt)/ CREDENTIALS_REQUIRED(dispatch)
- offline → offline_network_tool(DROP)/ offline_network_required(capability_reasons)
- unsupported_method → TOOL_NOT_EXECUTABLE(planned/不可执行面)
- stale_surface → surface_stale + SURFACE_FINGERPRINT_MISMATCH(pi_bridge_surface_gate)
- data_requirement_missing → SCHEMA_VALIDATION_REJECTED(pi_input_gate) + parameter_contract_violation:<id> 前缀(apply_contract)
映射以冻结 dict + parity 测试钉住(目标码缺席即测试红)。

## D4. per-tool Pi 可达性断言的构造性口径

proxy 面:webgis_execute 对任何「已注册且 status≠planned」工具构造性可达(经 dispatch 同一 chokepoint),故断言 = registered ∧ status 可执行;planned → fail(不可达);hidden/external_unavailable → warning(可达但生命周期投影会逐出模型面)。native 面:仅 7 前门评估 spec 可编译性与预算(spec_over_budget → fail,与 pi_native_surface 逐出语义一致)。

## D5. 「参数在 adapter 被忽略」静态检查 deferred

离线对账只能比较声明面(schema vs contract);「adapter 是否消费参数」是运行时行为,静态不可诚实判定。已有 ad hoc 测试(test_pi_dispatch_adapters.py)。deferred,不在本 PR 伪造覆盖。

## D6. spec_compiler 依赖注入而非直连

引擎的 pi_native face 需要 compile_tool_spec 结果;为保持纯函数与可测,注入 callable(registry, name) → spec|None,默认绑定真实编译器。测试可注入假编译器构造 over-budget/no-schema 分支,不依赖真实 schema 尺寸。

## D7. search face 只查静态可判定面

tier-2 无 domains / domain 无关键词覆盖 → warning(漏激活风险);本地 OSM 抑制(_SUPPRESS_WHEN_LOCAL_OSM)是运行时环境事实,不进离线判定(诚实边界)。

## D8. 测试环境:共享主 worktree venv

G03 worktree 无 .venv;主 worktree `/home/kevin/project/webgis-ai-agent/.venv`(Python 3.14.7,pytest 9.1.1,ruff 0.16.8)可直接运行 G03 代码(site-packages 解析依赖,cwd 解析 app 包)。所有测试命令以 `cd /home/kevin/project/webgis-g03-tool-conformance && /home/kevin/project/webgis-ai-agent/.venv/bin/python -m pytest ...` 形态执行并记录退出码。

## D9. git fetch 传输不可达时的真相核对通道

2026-10-21 实测 `git fetch` 直连 github.com:443 超时,但 gh API 可用且远端 master SHA(b1b2ec23)与本地 origin/master 一致、PR #1758 head(4c7fc6d4)与本地分支一致——本地 refs 即最新。后续每次「以最新为准」的核对:先试 fetch,失败则 gh API 复核 master SHA + 相关 PR head,记录于 LEDGER。不虚构 fetch 成功。

## D10. 新工具 catalog_conformance 的字节预算纪律

输出有界:faces ≤8 × (status/reason ≤64 chars/detail ≤160 chars),总载荷预算对齐 catalog_lookup(≤4KB);错误返回与 catalog_lookup 同款(error 字段 + 稳定码),不发明新错误信封。
