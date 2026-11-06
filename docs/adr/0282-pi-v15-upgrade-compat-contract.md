# 0282. Pi v1.5.0 升级与 WebGIS Agent Host 兼容契约

**Date:** 2026-11-06
**Status:** Proposed
**Branch:** `zcode/goal-pi-native-host-vnext-20261105`
**Baselines:** ADR-0231（wrap vendored Pi）· ADR-0103（动态工具表面）· ADR-0224（PiToolSpecV1/SurfaceReceipt）· ADR-0200 系列（结果 ABI）

## Context

`vendor/pi` 自 2026-07-21 固定在 `dd6bea41`（v0.81.1+1）。上游至 v1.5.0（2026-11-02
stable tag，2304 commits）引入两处影响本仓的 breaking change：工具参数校验迁移到
`@earendil-works/pi-schema`（plain JSON Schema 亦做类型归一化 coercion）；扩展加载
改为两阶段（加载期禁用 `setActiveTools` 等 action 方法）。同时旧 pin 在当前工具链下
**已无法干净构建**（`packages/ai` tsgo 类型错误，根目录无 lockfile 时传递依赖漂移），
且生产镜像从未携带构建产物 —— `USE_NEW_AGENT=true` 时 bridge 静默回退旧引擎（#400
遗留根因）。完整兼容性矩阵（活体实证）见
`planning/pi-native-host-vnext/compatibility-matrix.md`。

## Decision

1. **pin 升级到 v1.5.0**（`f9a263655`）。不追踪 main；每次移动 pin 必须附契约车道
   （`tests/integration/test_pi_vendor_contract.py`）的通过证据，`app/services/chat/
   pi_compat.py` 的 `PI_MIN_VENDOR_TAG` 是协议下限的唯一声明点，调整需 ADR。
2. **pi-schema coercion 边界政策**：Pi 侧的类型归一化（数字串↔数字、标量→单元素
   数组、布尔/浮点字符串、数组元素类型）被接受为**单一事实归一化层**；服务端
   `pi_input_gate` 的严格校验保持不变（归一化后最终门，纵深防御不变）。
   `additionalProperties` / 数值边界 / `required` / `enum` 在 Pi 侧先行拒绝。已披露
   的投机转换风险（整数字段收浮点字符串会**截断**：`"3.5"`→3；union 按首个无需
   转换即满足的分支收下）以 golden corpus
   （`tests/fixtures/pi_schema_coercion_golden.json`）锁定；GIS 侧关键整数字段在
   服务端 schema 保持 `integer` 并服务端复核，不依赖 Pi 截断语义。
3. **扩展适配**（`app/extensions/webgis-tools/index.mjs`）：默认表面投影延迟到
   `session_start`（两阶段加载），首轮 `before_agent_start` 兜底；上游 preamble
   改写为双串匹配（v0.81 原串 + v1.5.0 新串），未识别时前缀兜底行为不变。
4. **构建可复现**：`scripts/build-vendor-pi.sh`（幂等 npm install+build）；RPC 入口
   解析顺序 **bundle → dist**。v1.5.0 的 `dist/bundle/rpc-entry.js` 加载 WebGIS
   扩展还需锁定的 `jiti@2.7.0`；Dockerfile/Dockerfile.prod 的 `pi-builder`
   构建后同时复制 bundle 和该包，Compose backend-builder 目标也携带 Node。
5. **身份门与诊断**：`pi_compat.pi_vendor_identity()` 在启动时披露实际 pin/
   describe/入口形态/node 版本/协议下限（WARN 不阻断，spawn 的 fail-closed 不变）；
   `/health` 新增 `pi_vendor`（None=未构建 —— 与 agent_runtime 徽章可对账）；
   `scripts/pi_doctor.py` 分域诊断（vendor/build/extension/surface/rpc/env），
   安全相关 FAIL、观测相关 WARN，退出码可编排。
6. **disposition 防挂**：Pi ≥0.99 的 `prompt` 响应携带 `disposition`；`handled`
   （被 input handler 消费、无 agent run）时桥立即以 `pi_prompt_handled` 真实结算
   （task_error+done exactly-once），不再等满 stall/total 预算后误报超时。

## Consequences

- 兼容面以三层测试锁定：vendor 契约车道（真实 rpc-entry + stub LLM，免凭据）、
  pi-schema coercion golden（活体比对）、A5 旅程车道（`PI_JOURNEY_E2E=1` 门控 ——
  全栈真实回调，环境方差大故 opt-in，与 `PI_REAL_E2E` 同纪律）。
- 上游再改 preamble 文案只降级为前缀注入（行为兜底已在测试中锁定）；
  `message_update` 语义（delta-only）与我方 mapper 审计 #816 的实现一致，升级即免疫。
- 回退路径：`git -C vendor/pi checkout dd6bea41 && git checkout -- index.mjs 相关
  commit`；`USE_NEW_AGENT=false` 是引擎级应急开关（原有）。旧 pin 无法干净构建是
  回退的现实代价，已在 doctor 与部署文档标注。
- bundle 约 9.2MiB（82 文件），镜像另携带锁定的 jiti 扩展加载器；运行形态测试
  在不含 builder node_modules 的隔离目录验证真实扩展加载与 RPC 握手。

## Alternatives considered

- 追踪上游 main：否 —— 不可复现，违背 pin 纪律。
- 保留 v0.81 pin 修补构建：否 —— 无 lockfile 的传递依赖漂移不可修（上游根因），
  且持续落后安全修复。
- 服务端二次反归一化（拒绝被 coercion 的实参）：否 —— Pi 侧归一化后原始形态不可
  恢复；以 golden 锁定行为 + 服务端严格性作为最终门更诚实。
