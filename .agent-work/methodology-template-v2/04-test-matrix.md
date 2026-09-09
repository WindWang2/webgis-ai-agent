# 04 — Test Matrix（测试矩阵与验证记录）

## 本 Epic 新增测试（81）

| 文件 | 数量 | 覆盖 |
|---|---|---|
| tests/unit/gis/methodology/test_taxonomy.py | 10 | 20 类词表/投影派生/匹配确定性/指纹 |
| tests/unit/gis/methodology/test_graph.py | 10 | 图构建/关系覆盖/指纹缓存/悬空 fail-closed/有界投影 |
| tests/unit/gis/methodology/test_central_validation.py | 1 | methodology_intel 收编断言 |
| tests/unit/gis/methodology/test_qualification.py | 14 | 硬错误五禁止项/维度语义/unknown 语义/V4 oracle parity |
| tests/unit/gis/methodology/test_ranking.py | 8 | 权重契约/语料覆盖/abstention/平行不变性/基准钉值 |
| tests/unit/gis/methodology/test_case_corpus.py | 32 | 22 案例端到端 + 科学判定 + 反硬编码 + DoD≥5 |
| tests/unit/gis/methodology/test_budgets.py | 7 | 图 build/lookup/qualification/ranking/语料评估预算 + 规模有界 |
| tests/unit/gis/methodology/test_feedback.py | 5 | 默认禁用/硬上限/封闭词表/无回灌路径 |
| tests/cartography/test_template_intelligence.py | 12 | bridge 校验/图例语义/槽位/义务组件/反硬编码/确定性 |
| tests/unit/gis_harness/test_knowledge_tools.py | 7 | 工具注册/tier1 只读/契约/不泄漏图结构 |
| tests/unit/gis_harness/test_methodology_v4.py（改） | — | family_count 12→13（唯一计数断言演进） |

## 回归门禁（本地；overpass env workaround for sandbox DNS）

- tests/unit/gis/ + tests/unit/gis_harness/ + tests/cartography/ 全量
- 既有 methodology/compiler/data_qualification/ontology 套件零回归
- ruff（app/lib/gis/methodology + cartography + 改动文件）全绿
- fcntl 限制：tests/cartography/test_cartographic_quality_review.py 及
  少数 explorer/spatial_decision 工具在 Windows 环境因 Unix-only `fcntl`
  无法收集——环境限制，与本次改动无关（基线同样失败）

## 中央校验

`validate_gis_library()` 零 issue；新前缀全部收编：
`methodology_intel:` / `taxonomy:` / `method_descriptors:` /
`viz_bridge:` / `template_spec:` / `knowledge_graph:` / `provenance`
