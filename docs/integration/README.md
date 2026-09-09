# Integration / Coordination Platform（Quality V3，Epic 10）

并发研发（10+ worktree）下的 **integration authority**：共享文件所有权、
migration/ADR 分配协调、生成物权威、分支语义清单、影响选择、合并模拟、
SRE 健康面与发布证据。不篡夺业务域事实源——registries、quality
manifest、artifact_graph 仍是唯一事实源，本平台全部是元契约与投影。

## 开发者工作流（tooling UX）

### 我要开新分支/加 migration/写 ADR
```bash
python scripts/allocate_migration.py <slug> --dry-run   # 看 next 序号 + base SHA
python scripts/allocate_adr.py <slug> --dry-run         # 看 next 编号 + watermark
```
分配器降低撞号概率但不消除（并发窗口）；合并前闸兜底。

### 合并前我要跑什么
```bash
python scripts/quality_runner.py quick        # 全部质量红线 + 协调闸（秒~十秒级）
python scripts/quality_runner.py impact       # import 图驱动的最小可靠测试面
python scripts/quality_runner.py integration  # 协调闸 + 本分支 manifest
```

### 合并前怎么发现跨分支冲突
```bash
python scripts/gen_integration_manifest.py --branch HEAD
python scripts/simulate_merge.py --branches feat/a,feat/b
# 报告: .agent-work/integration/merge-sim/  checked/unknown/not_checked 三段诚实契约
```
真实进程级 chaos（API 重启/worker 丢失/trace 贯穿）：
```bash
python scripts/integration_harness.py --port 8901 --chaos
```

### 发布证据
```bash
python scripts/gen_release_readiness.py    # gates 现场跑 + 车道证据（opt-in）
# READY 只能来自 pass 证据或带 reason 的 waiver —— not-run 不可被合法化成全绿
```

## 权威与产物（全部有字节闸/结构闸）

| 文件 | 权威/生成 | 闸 |
|---|---|---|
| `docs/integration/ownership.json` | 手工权威（元契约本体） | ownership 结构 + parity（preflight） |
| `docs/integration/adr-link-baseline.json` | 棘轮基线（存量悬空链接） | preflight `adr_watermark` |
| `docs/integration/frontend-behavior.json` | `gen_frontend_behavior.py` | 字节闸（quick lane） |
| `docs/integration/RELEASE_READINESS.{json,md}` | `gen_release_readiness.py` | 字节闸 + 政策断言 |
| `.agent-work/integration/`（gitignored） | manifests / merge-sim / import 图缓存 | —（分支工作产物） |

## 模块地图

- `app/lib/integration/ownership.py` — 规则模型/校验/风险归约
- `app/lib/integration/migrations_coord.py` — alembic 图扫描/单头/NNNN 水位/碰撞
- `app/lib/integration/adr.py` — ADR 扫描/watermark 棘轮/引用校验
- `app/lib/integration/manifest.py` — 分支语义清单（risk/required_suites）
- `app/lib/integration/impact.py` — AST import 图/完备性选择器
- `app/lib/integration/merge_sim.py` — 合并模拟（checked/unknown/not_checked）
- `app/lib/observability/trace_context.py` — W3C traceparent（http+ws ASGI 中间件）
- `app/core/sre_metrics.py` + `app/api/routes/health.py` — SRE 指标/鉴权组件健康面

设计文档：`.agent-work/quality-v3/01-architecture.md`（含 Subagent-A 架构
挑战的 7 项强制修订）。
