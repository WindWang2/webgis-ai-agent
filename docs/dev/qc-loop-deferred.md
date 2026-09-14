# qc-loop Deferred（转出清单）

本文件按任务书 §2-P（risk=5 不修即转此）与 §9（达上限/收尾时剩余项转此）维护。
来源：quality/review-optimize-loop 分支 qc-loop 循环（state.json 为准）。

## R2Q004 · tools.py:608 — overlay_refs 文档化输入被静默丢弃（P1，暂缓原因：需实现消费逻辑）

- 证据：`webgis_map_product` 的 args 文档声明"辅助数据 ref 列表（如行政区边界面
  ref），补充 reference 角色"，代码 `overlay_refs = list(overlay_refs or [])`
  规范化后全文无读取（全仓 grep 唯一消费者缺失）。
- 暂缓原因：修复不是守卫而是**功能补全**——需要设计 overlay ref 在产品装配链
  （mapspec layer/ref-cursor）中的落位，涉及产品语义决策（reference 角色如何
  入图、如何披露），不宜由自动循环单方面定案。
- 建议：由 08（publish/export）或产品线认领；短期可先在工具响应中如实披露
  "overlay_refs 暂不支持"以消除静默忽略。

## 环境项 · golden 完整校验在本 worktree 不可运行（非代码缺陷）

- 现象：9 场景（含 nightly-only 负路径）全部 0.2s 内 `map_loaded=false`、
  `fatalError=null`、零 console/page 错误。
- 诊断：`npx tsx` 依赖的 tsx 不在 frontend/package.json（主检出环境经全局/
  npx 缓存解析）；python playwright 不在本 venv（浏览器二进制缓存存在）。
- 建议：PR 合并前在主检出环境跑一次 `bash scripts/quality_gate_local.sh`
  （不带 SKIP_BROWSER）复核 golden 全绿；或把 tsx 显式收进 frontend devDependencies。

## 其余 open P1（E1 达成时 ≤5 允许保留，列出供后续认领）

- Q053 · style_tokens.py:356 — `plan_palette_profile_migration` 子集内序数
  重映射与 docstring"源色带索引占比"相悖（test-only，无生产调用方）。
- R2Q006 · trace_store.py:327 — 最旧段全保护时 pass 1 提前 break，pass 2
  force 逐出违反文档承诺的保护顺序（长会话证据链提前丢失）。
- R2Q009 · plan_candidates.py:505 — 排序后第一个 recipe 候选 ≠ 路由 top-1，
  rerouted 判定在"blocked top-1"场景失效。
