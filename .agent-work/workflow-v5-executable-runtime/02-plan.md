# Workflow V5 Executable Runtime — Implementation Plan (Phase C)

## Waves（每 wave：实现 → targeted tests → lint → progress 更新 → 独立 commit）

| # | Wave | 主要产物 | 测试 |
|---|---|---|---|
| 1 | V4 recompute 真实形态 bug fix [R1-B1] | `workflow_v4/recompute.py` 边端点归一化 | 真实包形态强闭包断言（data_role 全下游 / parameter 子树含 output / style 零科学 / recipe 全图） |
| 2 | contracts + machine | `workflow_runtime/contracts.py`、`machine.py` | 转移表正/负/边界（含 READY→STALE、SKIPPED→READY、CANCELLED 非终态限定、实例终态裁决） |
| 3 | fingerprints | `fingerprints.py` | 确定性、canonical 化、env_fp 各分量敏感度、content_revision 敏感度 |
| 4 | binding | `binding.py` | 正/负/边界（CRS 类失配 blocked、单位失配、空输出、宽端口、unknown 放行） |
| 5 | persistence | models + migration 0034 + `store.py` + `registry.py` | 单 head 断言、up/down/up、节点级 CAS（冲突/SQLite busy 分道）、租约清扫、owner 隔离 |
| 6 | reuse | `reuse.py` | same-inputs 命中、algo/param/输入/包指纹变化 miss、ref 失效/损坏自愈、跨 owner 不命中、shape 级不复用 |
| 7 | geocompute adapter | `adapters_geocompute.py` | 映射确定性、NODE_NOT_EXECUTABLE 诚实、单节点 plan 指纹、幂等（重复完成不重复注册） |
| 8 | driver | `driver.py` | 波次推进、认领互斥、取消传播、重试上界、崩溃恢复、孤儿复位 |
| 9 | recompute runtime | `recompute.py` | 差分 oracle、quiescence/pending、style-only、参数子树、上游数据闭包、强制重算 |
| 10 | service + projection | `service.py`、`projection.py` | instantiate/run/cancel/changes 全链 + 解释文本 |
| 11 | API | routes + 注册 | FastAPI TestClient：owner 404、输入界 422、全端点 smoke |
| 12 | chat/session hooks | plan_orchestrator / session_plan / mapspec_mutations 最小挂钩 | fail-open 注入（抛异常不回归）、锁外验证 |
| 13 | subworkflow | 展开深度/环/上限/义务合并/取消传播 | 嵌套 2 层场景 + depth guard + 义务 provenance |
| 14 | frontend + docs | runtime-inspector + ADR-0119 + CHANGELOG 最小追加 + manifest regen | vitest、quality gate --check |

## 关键测试场景（Epic §16 全覆盖 → 分配到 waves）

same-package reuse(W6) / algo change(W6) / style no-rerun(W9) / upstream data(W9) / invalid CRS(W4) / units(W4) / cancelled descendants(W8) / crash-restart(W8) / duplicate completion(W7,8) / stale package after registry update(W5) / old package replay(W5) / nested subworkflow(W13) / owner isolation(W6,11) / reused artifact deleted·corrupted(W6) / parameter canonicalization(W3) / concurrent project edits(W9 CAS)。

## 风险与对策

- 真实 DB 环境：测试用临时 SQLite 工厂（沿用 geocompute reuse_index 测试模式）。
- 执行真链路：synthetic 小数据 in-process（GeoExecutionEngine retain_outputs 仅供测试断言）。
- 共享文件：hooks 全部 fail-open + additive；迁移撞号按 §16 协议。
