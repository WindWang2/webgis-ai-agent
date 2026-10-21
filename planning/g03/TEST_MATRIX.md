# G03 TEST MATRIX(命令与退出码台账;每 wave 更新)

环境:`cd /home/kevin/project/webgis-g03-tool-conformance && /home/kevin/project/webgis-ai-agent/.venv/bin/python -m pytest`(DECISIONS D8)

## 目标测试命令(G03 自有)

| 命令 | 首跑 | 最新 | 备注 |
|---|---|---|---|
| `-m pytest tests/unit/gis_tool_conformance/ -q --maxfail=3 -x` (串行,超时 300s) | TBD | | W1 起 |
| `-m pytest tests/unit/gis_tool_conformance/test_engine_determinism.py -q` | TBD | | |
| `-m pytest tests/unit/test_catalog_discovery_tools.py -q`(若存在,新增工具回归) | TBD | | W2 |
| `scripts/check_tool_capability_conformance.py`(全量 + --tool + --json) | TBD | | W2,记录退出码 |
| `-m ruff check app/lib/gis/tool_capability_conformance.py scripts/check_tool_capability_conformance.py app/tools/catalog_discovery_tools.py tests/unit/gis_tool_conformance/` | TBD | | |

## 受影响既有测试(回归面)

| 命令 | 首跑 | 最新 | 备注 |
|---|---|---|---|
| `-m pytest tests/unit/test_capability_registry_parity.py tests/unit/test_execution_catalog_docs_drift.py -q` | TBD | | registry/目录面 |
| `-m pytest tests/unit/test_tool_catalog.py tests/unit/test_tool_surface_v2.py -q` | TBD | | 选择/投影面(新工具入 registry 后) |
| `-m pytest tests/unit/test_pi_tool_spec.py tests/unit/test_pi_surface_receipt.py -q` | TBD | | Pi ABI 面 |
| `-m pytest tests/unit/test_pi_h01_reason_code_parity.py -q` | TBD | | 码表 parity(若动码表) |
| `-m pytest tests/unit/test_quality_manifest.py tests/unit/test_tool_descriptor_coverage* -q`(若存在) | TBD | | 覆盖率分母变化 |

## 基线纪律

- 跑新测试前先在 **clean master**(主 worktree zcode/swarm-review-fixes-v1 不算;用 `git stash`/独立临时 checkout 或直接对照 #1753 已知挂起清单)对照失败是否既有
- 全量后端套件 ~80% 处确定性挂起(#1753)→ **禁止无超时全量跑**;只跑上表目标目录
- 任何跳过/失败如实记录退出码与输出摘要,不粉饰

## 结果记录

| 日期 | 命令 | 退出码 | 结果 | 备注 |
|---|---|---|---|---|
| 10-21 | `-m pytest tests/unit/gis_tool_conformance/test_engine_faces.py -q --maxfail=3` | 0 | 44 passed | 首跑 33/36,修 3 处测试构造(签名自动推导 schema/deprecated 需 deprecation_of/enum 需 Literal) |
| 10-21 | `-m pytest tests/unit/gis_tool_conformance/ -q --maxfail=3 -p no:cacheprovider` | 0 | **55 passed**(faces 44 + determinism 8 + xwalk 3) | 引擎 bug 修复 1 个:_overall 聚合序(not_evaluated 不压过 pass) |
| 10-21 | `-m ruff check app/lib/gis/tool_capability_conformance.py tests/unit/gis_tool_conformance/` | 0 | All checks passed | |
| 10-21 | 活 registry 冒烟(bootstrap=True 全量审计) | 0 | 356 工具:0 fail / 215 warning / 141 not_evaluated | warning top:metadata_incomplete 158、result_size_unknown 76、binding_unbacked 60、ref_offload_without_ref_contract 41、contract_divergence 7 → W4 清偿 |
| 10-21 | CLI `--tool buffer_analysis` | 0 | 人读报告正常(overall=warning:ref_offload 无 produced_refs) | |
| 10-21 | `-m pytest tests/unit/gis_tool_conformance/ -q`(含 B 代理 3 新文件) | 0 | **86 passed**(faces 44+determinism 8+xwalk 3+tool 7+scale 7+poi 9+ndvi 8) | E2E 经真实 ToolDispatchService;零网络 |
| 10-21 | 回归:docs_drift+parity+tool_catalog+surface_v2 / pi_tool_spec+surface_receipt+reason_code_parity | 0 | 47 + 45 passed | 新工具入 registry 后 |
| 10-21 | `scripts/check_tool_descriptor_coverage.py` | 0 | 357 工具 5 字段全 100% PASS | |
| 10-21 | `python -m app.lib.gis.execution_catalog_docs`(docs 重生成) | 0 | 356→357 工具,manifest 更新 | |
| 10-21 | `-m pytest tests/unit/gis_tool_conformance/`(W4 后,含 live 棘轮) | 0 | **94 passed** | 引擎细化:geojson 结构性 ref pass(41→4)+ 家族并集豁免 |
| 10-21 | 修复面回归:spatial_stats/point_pattern/rs_v3/kriging_v4/contracts_vnext/qualification | 0 | 141 passed(1 处钉扎更新:kriging 契约 v3→v4 为设计内变更) | ripley/cloud_qc/kriging 三修复 |
| 10-21 | 活审计复跑 | 0 | 357 工具:0 fail / 176 warning / 181 pass;contract_divergence 0(原 7);ref_offload 4(原 41,4 个真含糊项披露) | |
