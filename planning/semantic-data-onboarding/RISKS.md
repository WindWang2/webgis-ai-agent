# RISKS

| # | 风险 | 概率/影响 | 缓解 |
|---|---|---|---|
| R1 | 网络持续中断：无法 push/PR、无法二次去重 open PR/issues | 高/中 | 本地保留全部分支与提交；后台监控恢复；PR body 预写好可粘贴；不得声称已建 PR |
| R2 | 与锚点后新合并 PR 冲突（explorer/dataset_semantics 面被他人改） | 低/中 | 动中心文件最小 patch；push 前 rebase origin/master 复测 |
| R3 | capability 词表封闭导致工具无法声明新能力 | 中/低 | M6 先验证 descriptor validate 词表；封闭则最小适配 patch（预列在契约 §8） |
| R4 | OpenAPI/contract 快照与并行分支冲突 | 中/低 | 用项目生成器再生（API_SNAPSHOT_UPDATE=1），不手改；冲突时重生成 |
| R5 | worktree 无 node_modules，pnpm 离线安装失败 | 中/中 | pnpm store 缓存 + --offline 优先；失败则用主检出 node_modules 跑只读检查并如实披露 |
| R6 | 10 万行准备执行超时/内存 | 低/中 | 步执行走流式/分块（复用 ref store 读取接口）；pytest timeout=180 内有界样本为主，全量仅一次冒烟 |
| R7 | 字段提案引擎与既有三套推断语义打架（同一字段不同结论） | 中/中 | 提案引擎为增量超集 + adapter 投影；不改既有消费者；文档披露差异 |
| R8 | descriptor 写回与 #1759 恢复链冲突（版本爆炸/指纹漂移） | 低/高 | 写走既有 store 版本化（MAX 8）；evidence=user_confirmed 单独标记；复用 reuse 对账测试加负例 |
| R9 | 迁移撞号（若需 DB 表） | 低/低 | 目标无新表（计划存 ref store + artifact metadata）；如需再走 allocator + watermark |
| R10 | 测试与并行 subagent 抢资源 | 中/低 | 窄面单进程默认；宽面错峰；文件锁由主 Agent 单入口 |

## R2 复审残差（P3，已披露不阻塞）

- R2-2 内容锚首跑盲区：空指纹计划在构建与首次执行之间被原地覆写时无先例
  收据可比对（前置链窄：无 descriptor + 覆写窗口）。后续：计划携带构建时
  内容哈希，执行前对账。
- R2-4 _EXEC_LOCKS 无回收（每 (session,plan_hash) 一条，量级可忽略）；
  asyncio.Lock 绑定首次 acquire 的事件循环 —— 单 loop 生产安全，多 loop
  嵌入场景需换 per-loop 工厂。
- R2-6 幂等重入 O(n)×2（deepcopy + canonical sha256）：双锚校验的必要代价；
  大表后续可让计划携带锚做前置短路。

## master 既有红灯（非本分支引入，干净锚点同口径复现）

- tests/data/test_data_profile_raster_band_cap_1716.py 三例（band cap 1716）
  在 origin/master@4b3629c9（/tmp/master-check 只读复跑）同样失败。
