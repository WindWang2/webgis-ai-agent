# 00 — Baseline（只读审计基线）

- 日期：2026-09-11
- 基线 commit：`2aabdc436842ffe0fe66419ea50fde3d1bfcceda`（master，工作区干净）
- 执行：ZCode 全自动（/goal）

## 规模 census（详表：census/production-source-census.csv）

| 区域 | 文件数 | LOC |
|---|---|---|
| backend `app/`（.py） | 952 | 359,516 |
| frontend（.ts/.tsx，排除 node_modules/dist） | 636 | 122,289 |
| 生产源码合计 | 1,588 | 481,838 |
| tests（.py，参考） | 1,141 | 273,660 |

子系统文件数 top：services/gis_harness 101、components/map 87、services 顶层 71、
services/data_fabric 65、lib/gis 63、tools 57、lib/cartography 54、lib/geo_analysis 45、
services/chat 43、services/geocompute 32、api/routes 31、services/modelops 22。

## 近期合并背景（git log）

master 在基线前连续合并大型方向：harness-v6-semantic-autonomy、data-fabric-v7、
lakehouse-v7、workflow-v5、science-v5、spatial-modelops-v2、geocompute-v7、
cartography-v6、workbench-v6、extensions-v3、quality-v3、methodology-v2、
fix/audit-findings（39 findings）等，之后有一轮集成修复（ADR 碰撞重编号 0120-0129、
4 个 0034 alembic head 合并、tier-2 域对齐、baseline 刷新）。
基线 HEAD 即该轮集成修复的收尾（2aabdc43 fix(quality): update drift report fingerprints...）。

## 基线即时的机器检查结果

- `scripts/check_integration_preflight.py` → **FAIL（generated_staleness RED）**：
  16 个生成物输入指纹已变待再生成（RELEASE_READINESS、frontend-behavior.json、
  CONTRACT_DRIFT_REPORT、QUALITY_MANIFEST/REPORT、7 个 certification、
  contract-drift-report.json、quality-manifest.json、quality-report.json、
  BENCHMARK_MANIFEST、tests/quality/snapshots/realtime-contract.json）。
  → 注意：2aabdc43 声称更新了 drift report fingerprints，但仍有 16 项 RED，说明该
  commit 后仍有输入变化未再生成，或指纹更新不完整。【Finding INT-1】
- migration_heads：ok（单 head）
- adr_watermark：ok
- ownership parity：ok
- ADR 目录仍存在 watermark 以下历史重复编号：0077×2、0088×3、0094×2、0096×2、
  0099×2、0101×4、0103×3、0104×5、0105×2、0118×7（被 watermark 机制容忍；
  引用歧义风险记录为文档契约问题）。

## 已有审计资产（不重复劳动，但要重新验证）

- `.audit/`（2026-08-27 六路审计）与 `AUDIT_REMEDIATION_REPORT.md`（39 findings 已修）
- `.agent-work/quality-v1..v3` 等 29 个方向工作记录
- GitHub：audit2 系列 issue 全部 CLOSED（#1062-#1113 等）；当前 open issue 数：0

## 环境基线

- Python 3.13.9；alembic 1.19.2、fastapi 0.135.3、SQLAlchemy 2.0.43
- gh CLI 已登录（WindWang2），仓库 github.com/WindWang2/webgis-ai-agent
