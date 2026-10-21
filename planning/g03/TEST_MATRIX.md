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

(每 wave 追加:日期 / 命令 / 退出码 / 通过数 / 失败-既有 vs 新增判定)
