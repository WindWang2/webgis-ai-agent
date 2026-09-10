# 06 — PR Summary

## 标题

feat(methodology-v2): Epic 11 — GIS Methodology & Template Intelligence V2（方法知识图 + 制图组合智能 + Agent 规划知识面，ADR-0120）

## 摘要

在 Workflow Compiler V4 的方法论族基座上，建立 canonical registries 之上的
只读方法知识层：20 类任务分类学、typed 方法知识图（421 节点/883 边）、
51 方法增强描述、统一资格引擎（8 维四态）、混合排序（abstention）、
Viz Bridge（artifact→表达族→图例语义→槽位绑定）、TemplateSpecV2 +
组合规划器、KnowledgeService 门面 + 6 个只读 agent 工具、22 案例端到端
语料与离线反馈契约。

## 变更面

- 新增：`app/lib/gis/methodology/`（10 模块）、
  `app/lib/cartography/template_intelligence.py`、
  `app/services/gis_harness/knowledge_tools.py`、
  `tests/unit/gis/methodology/`（10 文件）、
  `tests/cartography/test_template_intelligence.py`、
  `tests/unit/gis_harness/test_knowledge_tools.py`、
  `docs/adr/0120-gis-methodology-template-intelligence-v2.md`
- 纯加法改动：gis_ontology（+5 任务）、workflow_v4/methodology（13 族 +
  7 方法 + 3 处知识补齐）、component_registry（semantic_role/examples +
  角色投影）、registry_validation（methodology_intel 对账段）、
  app/tools/__init__.py（+1 注册行）、methodology_corpus（+2 proximity
  案例）、CHANGELOG
- 断言演进（仅两处，架构 §4）：family coverage 集合断言、
  `family_count()==13`

## 关键数字

- 25 案例方法语料：recall@1 0.96 / recall@5 1.0 / MRR 0.98 /
  invalid 0.04 / ambiguous valid-top1 1.0（钉值取保守下限）
- 22 端到端案例全绿（Epic §9 全覆盖）；DoD ≥5 场景断言通过
- 图 build ≤150ms（投影）、lookup ≤5ms、ranking ≤10ms、
  语料评估 <3s；图 <600 节点/<2000 边
- 新增测试 81；中央校验零 issue（7 个新前缀收编）

## 已知限制（记录不改）

- `spatial.kde.surface` 未声明资源硬闸（registry gap，ADR-0120 §3）；
  大层资源裁决待算法层补声明后接入 qualification。
- 方法级检索为 lexical+结构化信号（embedding 语义检索归 harness-v6 范围，
  Epic Non-goal 禁无界 vector storage）。
- Windows 环境下少数测试文件因 Unix-only `fcntl` 顶层 import 无法收集
  （基线同样失败，与本 Epic 无关）。

## 验证

本地：ruff 全绿；tests/unit/gis + tests/unit/gis_harness + tests/cartography
回归（fcntl 收集错误除外，见上）；methodology/compiler/ontology/template
相邻套件零回归；中央校验 `validate_gis_library()` 零 issue。
未等待线上 CI；PR 不自动 merge。
