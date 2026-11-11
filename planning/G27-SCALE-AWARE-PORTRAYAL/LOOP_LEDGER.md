# G27 LOOP LEDGER

> 每里程碑更新：下一步可执行动作、最新 SHA、有效契约、必须保留的用户约束、未解阻断。

## L0 — 2026-11-11 14:40Z · 侦察与独立工作区

- **SHA**：BASE `8a9a79f76875eb95d31bd448baad971edb2e61d3`；分支 `zcode/g27-scale-aware-portrayal-20261111T144000Z`；worktree `webgis-wt-g27-scale-aware-20261111T144000Z`（干净）。
- **已完成**：安全 fetch 成功；远端全景（3 开放 PR / 近 30 天合并 PR / 开放 issues / 120 远端分支）；PR body 与正式 review 区分；只读架构调查员产出真实调用图；`RECON.md` / `DUPLICATION_MATRIX.md` / `PLAN.md` / `INTERFACES.md` 落地。
- **有效契约**：MapSpec v2.1；四类尺度优先级 = 用户隐藏 > pin/锁 > 结构 zoom 门 > 自动 LOD > DPI/物理比例尺 > data-plane 传输档；ADR-0218 为历史快照。
- **必须保留的用户约束**：起始工作区 `M vendor/pi` + 未跟踪 `.claude/skills/*` + `_tmp_47_*` 只读保留；不改 master、不合并、不删他人分支/worktree；不等线上 CI 但不是降低门禁；不自行升级 Pi vendor（G22 独占）；不接管 R1 issues；避开 #2084/#2085/#2086；最多 3 个 subagent、不派生下级。
- **资源**：16 CPU / 62 GiB / 543G 可用；`-j2`；`flock` + `/home/kevin/projects/webgis/.bare/webgis-heavy-stage.lock` 宿主级重任务锁；本方向专属 venv `/home/kevin/.workbuddy/binaries/python/envs/g27-scale-aware`；worktree 无共享 node_modules/DB。
- **下一步**：① 隔离 venv 就绪后派实现者做 P1 后端纯函数 + corpus + 单测；② 主 Agent 并行做前端镜像、生产落笔、PublicationIR scale viewport、UI/工具；③ 独立 reviewer 复跑；④ review→fix→review；⑤ 推送分支 + PR。
- **未解阻断**：无 `/goal` / goal-loop skill 原生入口（按指令"缺失不阻断"）；无法读取真实 Goal 配额（如实记录）；前端 node_modules 未安装（真实浏览器验证前需评估，可能 blocked）。
- **状态**：P0 `in_progress`；P1–P7 `not_started`。
