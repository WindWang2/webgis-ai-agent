# G03 PR 记录

- 分支: `feat/g03-gis-tool-conformance-vnext`(rebase 到 ee8c14f8 后 9 commits)
- 基线: 初始 b1b2ec23 → rebase 后 ee8c14f8(#1758 已合并)
- PR URL: https://github.com/WindWang2/webgis-ai-agent/pull/1761

## Why / Now

工具能力元数据有 8 个消费面(注册/搜索/规划/Pi native/proxy/dispatch/结果/
科学语义),各面各有校验器但**没有 per-tool 跨面对账投影**;schema ×
parameter_contract 此前零交叉校验。#1620/#1756 已立 ABI 与错误码权威,
本 PR 补上「元数据真实性」的机器对账层,并在活 registry 上清偿了真实发现。

## 与 #1758 的关系(不重复证据)

- 复用其 dispatch_reason_codes 权威码表(FALLBACK_CODE_XWALK 只引用不新造)
- 文件交叠 3 处已 rebase 消解:pyproject(其 scripts/** T20 通配覆盖本 PR
  逐文件条目,采纳通配)、.gitignore(白名单各行独立)、spatial_stats.py
  (hunk 不重叠:其 #1735 在 186-331,本 PR 在 363+)
- 方向正交:其清偿 issue backlog,本 PR 建对账基建 + 清偿对账发现的契约漂移

## DoD 对照

✅ 单一权威契约生成;违规元数据机器可识别(97 测试 + CLI + 棘轮)
✅ Pi native/proxy/dispatch 对账 + 2 个离线 E2E 场景(真实 ToolDispatchService)
✅ 兼容原有 API;无第二 ToolRegistry/Agent;357 工具审计 0.16s
✅ 贡献者指南 docs/dev/tool-conformance.md;影响面验证(ripley dispatch 回归)
✅ 本地测试+lint 实际退出码(见 TEST_MATRIX);既有失败如实披露(#1753 全量套件不跑)
✅ planning/g03/ 8 文件 + REVIEW 两轮台账
✅ 独立 review:第一轮(P0×1+P1×2)→ 修复 → 第二轮机械复验(SendMessage
   工具缺席+3 子代理上限,如实披露于 REVIEW.md,非伪造独立复审)

## 审计发现清偿台账

| 发现 | 处置 |
|---|---|
| ripley 契约 envelopes 默认 99 ≠ 实现默认 0(文档说模拟了实际没跑) | 契约 v3 默认 0 + 工具暴露透传 |
| ripley 工具对 GeoAnalysisResult 下标访问(**master 上 dispatch 即全坏**,clean master worktree 实证) | 改 .summary/.data + dispatch 回归测试 |
| cloud_qc 契约参数名 brightness_threshold ≠ 工具参数 brightness_thresholds | 契约改名对齐 + 桥接更新 |
| kriging 契约 matern_smoothness 工具面永不可达(死参数) | 契约 v4 修剪 |
| 41 工具 ref_offload 无 produced_refs | 38 个 geojson_fc 为结构性 pass(dispatch 无条件铸 ref);4 个非 geojson 族保留披露 |
| temporal/network 家族并集契约「缺席」误报 | 引擎家族并集豁免(兄弟暴露即豁免,全族无人暴露才报) |
| query_local_poi 消费 AMAP_API_KEY 未声明 requires_credentials | PR 披露为后续项(离线优先工具,声明语义需维护者决策) |

## 测试(全部本地,实际退出码)

- `tests/unit/gis_tool_conformance/`:**97 passed**(引擎 46+确定性 9+码表 3+
  工具接线 7+规模 7+E2E 17+活审计棘轮 6+ripley dispatch 3 — 计数见各文件)
- 回归:docs_drift/parity/spatial_stats×2/point_pattern×2/scientific_contracts
  /tool_catalog/surface_v2/pi_tool_spec/surface_receipt/reason_code_parity/
  qualification = **194 passed**(rebase 前后各跑一轮)
- ruff 全过;coverage 门 100%;CLI 退出码 0;docs 防漂移绿
- 未跑:全量后端套件(#1753 确定性挂起,只跑目标目录——如实披露)

## 风险与回退

- 回退:整 PR 可 revert(新增文件+少量元数据修复,无 schema 迁移);
  ripley envelopes 新参数默认 0=历史行为;契约 v3/v4 升版只影响指纹/文档
- 剩余 P2/P3:4 个 ref_offload 含糊项、query_local_poi 凭据声明、
  metadata_incomplete 158 长尾(见棘轮上限,只降不升)
