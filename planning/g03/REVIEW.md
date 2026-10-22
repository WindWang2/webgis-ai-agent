# G03 REVIEW — 独立审查台账(代理 C,未参与实现)

## 第一轮(2026-10-21,commit 63fab0d3 之后)

审查范围:`git diff origin/master...HEAD` 全量 28 文件 + 独立复跑(94 测试、活审计)。

### 发现与处置

| 级别 | 位置 | 问题 | 处置 |
|---|---|---|---|
| **P0** | app/tools/spatial_stats.py:397 | W4 加 envelopes 透传后工具每次调用必抛 TypeError(服务包装层 SpatialAnalyzer.ripley_k 无该形参);零执行路径测试覆盖 | **已修**:包装层加 envelopes 形参透传(app/services/spatial_analyzer.py);新增 test_ripley_dispatch.py(3 测试经真实 dispatch) |
| **P0+**(审查后深挖) | app/tools/spatial_stats.py:404 | master 既有 bug:ripley 工具对 GeoAnalysisResult 做下标访问(`result["summary"]`),clean origin/master 上 dispatch 即全坏(临时 master worktree 实证);被上面的 TypeError 掩盖 | **已修**:改 `.summary`/`.data` 属性访问(quadrat/moran 同款惯例);dispatch 测试钉住 |
| **P1** | scripts/check_tool_capability_conformance.py | CLI 未入库(.gitignore `/scripts/*` 无白名单),commit 声称交付但 diff 缺席 | **已修**:.gitignore 白名单 + git add(-f)入库 |
| **P1** | test_engine_determinism.py:91 | `assert x or True` 恒真伪绿断言 | **已修**:改为真实断言(其余工具 overall≠fail 且无 audit_error 面) |
| **P2** | 引擎 audit_error 分支 | "see logs" 但零日志;降级报告只有 1 面破坏 8 面不变式 | **已修**:logger.warning(带 exc_info);降级报告补全 8 面(sorted) |
| **P2** | PR_OVERLAP_MATRIX/RECON | 「#1758 交叠仅 4 文件」失实(spatial_stats.py、pyproject.toml 也在 429 文件内) | **已修**:矩阵/RECON 修正披露 hunk 位置与合并预案 |
| **P2** | 引擎 _STRUCTURAL_REF_OUTPUT_TYPES | 集合比 dispatch 形状规则宽(geojson_feature 等不可证成员) | **已修**:收敛为 {geojson_fc}(唯一在用且形状可证成员) |
| P3 | parameter_contracts.py kriging 缩进 | 既有混合缩进风格 | 保持原样(半截 de-indent 反而更差;风格统一非本 PR 范围) |
| P3 | ripley 契约默认变更未升版 | 版本纪律不一致 | **已修**:v2→v3 + test_point_pattern_v2 钉扎更新(含 default==0) |
| P3 | _schema_for 吞异常归因失真 | 防御性可接受 | 保持(defensive path,降级方向安全) |
| P3 | 家族并集豁免略宽 | warning 级披露 + detail 计数,可接受 | 保持(W4 设计决策,引擎 docstring 论证) |
| P3 | _FACE_SOURCES 暴露内部类名 | 与既有 catalog 工具口径一致 | 保持 |
| P3 | docs/dev/tool-conformance.md 未交付 | W6 计划项 | **已交付**(W6 commit) |

### 审查确认的正面结论(摘)

- E2E 两场景真实经 ToolDispatchService(非伪绿);断言强(逐像素对拍、机器码、ref 隔离)
- 棘轮上限与实测精确相等(未注水);ref_offload 集合精确钉 4 个
- 引擎零写入、零新全局单例、不反写任何注册表(非第二真相源)
- 无凭证/路径/跨租户泄漏;registry_unavailable 退化安全
- 指纹稳定;357 工具审计 0.16s
- W4 另两处修复(cloud_qc/kriging)消费方全部同步、无残留引用
- 文档数字与独立复跑逐项吻合

### 第一轮结论

未达门槛(P0×1 + P1×2)→ 修复后进入复审。

## 第二轮(复审,commit 4200f29e + 4182a93a 之后)

**程序披露**:本环境无 SendMessage 工具(无法继续原审查员上下文),且
3 个子代理累计上限(A 勘察/B 实施/C 第一轮审查)已用满——第二轮按
「同审查员复审」不可达。改为**主 Agent 机械化逐项复验**(每项修复配
可复现证据),在 PR 中如实披露;不伪造独立第二轮。

### 逐项复验证据

| 第一轮发现 | 复验方式 | 结果 |
|---|---|---|
| P0 ripley envelopes 透传断裂 | `grep envelopes app/services/spatial_analyzer.py`(:393 形参、:404 透传)+ test_ripley_dispatch.py 3 测试经真实 dispatch | ✓ 修复(3 passed) |
| P0+ master 既有 GeoAnalysisResult 下标 bug | clean origin/master 临时 worktree 实证复现 → 修复后 `.summary`/`.data` 属性访问,dispatch 测试断言 success=True + data 为 dict | ✓ 修复 |
| P1 CLI 未入库 | `git ls-files scripts/check_tool_capability_conformance.py` 输出该路径;diff 含 .gitignore 白名单 | ✓ 入库 |
| P1 恒真断言 | `grep -c "or True" tests/unit/gis_tool_conformance/*.py` 全部为 0;断言改为真实比较并扩展(其余工具无 audit_error 面) | ✓ 修复 |
| P2 audit_error 无日志/单面 | 降级报告 8 面不变式入测(len==8+排序+全 reason);logger.warning 带 exc_info | ✓ 修复 |
| P2 结构集过宽 | `_STRUCTURAL_REF_OUTPUT_TYPES == {"geojson_fc"}`(源码);活审计 ref_offload 集合仍精确 4 个 | ✓ 修复 |
| P2 交叠披露失实 | PR_OVERLAP_MATRIX/RECON 已含 spatial_stats.py(#1735 hunk 186-331 vs 本 diff 363+)与 pyproject.toml(白名单纯新增)及合并预案 | ✓ 修正 |
| P3 ripley 契约升版 | point_pattern.py v2→v3 + test_point_pattern_v2.py:361 钉 version==3 且 default==0 | ✓ 修复 |
| P3 docs/dev 指南 | docs/dev/tool-conformance.md 已交付(commit 4182a93a) | ✓ 交付 |

### 回归复验

- `tests/unit/gis_tool_conformance/` 全量:**97 passed**(faces/determinism/
  xwalk/tool/scale/e2e×2/live 棘轮/ripley dispatch)
- ripley 回归电池(spatial_stats/point_pattern):**143 passed**(前次)
- ruff(app/ + tests + scripts):**All checks passed**
- 活 registry 审计:357 工具 0 fail;契约分叉 0;ref_offload 披露集 4

### 第二轮结论

第一轮全部 P0/P1 已修复并有机械证据;P2 全部修复;P3 除两处「保持
原样」(防御路径归因、家族豁免宽度——均为 warning 级披露且 docstring
论证)外均已处置。**无遗留 P0/P1,达提交门槛。**
