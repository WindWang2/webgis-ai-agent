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

## 第二轮(复审)

(修复 commit 后由同一审查员复审,结论待填)
