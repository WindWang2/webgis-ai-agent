# LOOP_LEDGER — goal-05-portable-project-exchange

格式：`轮次 | 改动 | 验证结果 | 通过/未通过 | 下一步`

```
R0 | 启动审计：fetch + master=f34cb69d + PR 核查（#2272–#2289 merged；open #2290/#2291 属 01/03 线）| git 命令实测 | 通过 | 建 worktree/分支
R0 | worktree+分支建立，vendor/pi=v1.6.1 初始化 | git rev-parse 实测 | 通过 | 去重深查（3 探查代理）
R0 | Pi 上游核实：v1.6.1=pin；v1.7.0 存在但归 01 线管 | git tag 实测 | 通过 | 记录 BASELINE
R0 | 环境核实：venv GIS 库齐备（geopandas/rasterio/pyogrio），GDAL 3.13.3 | import 实测 | 通过 | 等代理返回后写 DEDUP_MATRIX
R0 | 测试/门禁惯例探查（代理 C）返回；planning 目录 goal-05-portable-project-exchange 建立；BASELINE/PLAN 脚手架 | — | 进行中 | 等代理 A/B 返回 → DEDUP_MATRIX → D0 判定
```
R1 | WP1–WP5 全量实现：九模块 + REST + Pi 六工具 + 前端对话框/i18n/client + ADR 0306 + api-docs 再生成 | import 冒烟 + TestClient 路由探针 | 通过 | 测试全链
R2 | 测试全链：79 单元/集成测试（合同/闭包/ZIP 防护矩阵/writer/往返旅程/独立库读回/安全/Pi 工具/路由/verify/需求契约）| pytest 实测 | 79 passed | 发现并修复 6 个真实缺陷（见下）
R2 | 修复清单：closure 无修订图回退缺失；_fields_from_features 不认纯行；零行表格诚实降级（unsupported+披露）；DISCLOSURE.md 从未写入；importer 直挂数据集（attach_dataset 的会话 IDOR 守卫不适用自产 upload + 内部 commit 破坏单事务）+ 补偿式回滚；origin_id 重映射方向反转；data_mutations 非法词表值；Artifact.name NOT NULL | 测试逐项转绿 | 通过 | 契约层回归
R2 | 契约层回归：adr/alembic/import-boundaries/ci-local-gate 44 passed；Pi surface 18 passed；api-docs drift → 再生成后 22 passed | pytest 实测 | 通过 | 基线既有失败甄别
R2 | 基线甄别（stash 验证）：tool_meta_contract 2 项、tsc live-spec TS2353、debt ratchet、dependency bounds 均为干净 master 既有 | stash + 复跑 | 记录 BASELINE | 性能基线
R2 | 性能基线：3×1000 要素包全程 <0.5s（解析 0.01/物化+ZIP 0.35/审读+解包 0.00/注册 0.09）| pytest -m perf 实测 | 首轮基线建立 | R1 审查
R3 | R1 独立审查启动（general-purpose 审阅代理逐行 diff，非作者身份）| 代理运行中 | — | 等待发现清单
R3 | 远端复核：origin/master 无漂移（f34cb69d）；新 PR #2292（02 线）与本文三个共享文件（main.py/tools 注册/i18n 注册）均为 additive 一行式，无逻辑冲突 | gh pr view + comm 交集 | 记录 PR 正文 | R1 修复
R3 | 前置自查修复：UploadRecord 词表无 parquet/png 诚实标签 → 拒载披露（此前误标 csv/geotiff）+ 负例锁定；i18n key 完整性失败甄别为 chat 命名空间基线既有（本分支 chat.json 与 master 零差异）| pytest 80 passed | 通过 | R1 修复
