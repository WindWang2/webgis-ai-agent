# PR_OVERLAP_MATRIX — 去重矩阵

## 状态（2026-10-29，网络中断下的本地证据 + 待网络恢复复核）

本地证据：`git branch -r --no-merged origin/master` 为空（最后成功 fetch = 锚点 4b3629c9）；所有远端分支（feat/g01–g05、h01–h02、g3d01–05、g3dv2-a/b/c、fix/*、zcode/swarm）均已在 master 合并。

| 已合并 PR | 与本任务实质重叠 | 裁决 |
|---|---|---|
| #1759 G01 数据资格/多角色绑定/descriptor | 数据集级角色绑定、preflight、provenance 均其成果 | **复用为权威，不重建**；本任务仅补字段级确认回路 + 准备面 |
| #1760 G02 GIS 科学契约 | 契约纪律 | 遵守，不重叠 |
| #1761 G03 工具一致性 | 工具注册纪律 | 遵守（tier/domains/typed error） |
| #1762 G04 制图模板资产 | 无重叠 | — |
| #1763 G05 任务验收 | 无重叠 | — |
| #1755/#1758 swarm 修复 | 无重叠 | — |
| #1771/#1835 g3d01/DEM 供给 | DEM 管线 | 明确不做 |
| #1834/#1836 g3d02/b 场景运行时 | 3D | 明确不做 |
| #1773/#1829/#1867 g3d03/04/c 分析闭环 | Requirement IR/harness | 仅消费（DataRequirement/preflight），不改 |
| #1830 g3d05 3D 制图产品 | 无重叠 | — |
| #1549/#1551/#1598/#1679/#1680 修复类 | 无重叠 | — |
| dependabot #1764–#1768, #1770, #1772, #1831–#1833 | 依赖 | 不碰 |

## 开放 PR / issues（2026-10-29 23:4x 网络恢复后二次去重 —— 已完成）

- `gh pr list --state open` → **(none)**：零重叠。
- `git fetch origin master` → 仍为 4b3629c90（未前移，无需 rebase）。
- issues #1842–#1866：全部为 2026-10 大审查系（S1–S7 汇总 + High 若干）的
  **既有**发现。与本任务的关系裁决：
  - 跨租户身份伪造类（#1841 events 注入 / #1842/#1843 记忆 org/user 取自
    map_state / #1847 _mission_binding）：本任务**未引入同类面** —— 新端点
    session 归属一律 DB 校验（authorize_session_write，404 不泄露），工具走
    _resolve_session_id（verified runtime context，LLM 提供的 session_id 不可信）。
  - #1844（质量规则环截断）/ #1854（dataset_semantics Medium）/ #1851（数据源
    面板 Medium）：既有代码的独立清偿面，本 diff 对这些文件零修改（仅 import/
    additive），不抢修、不冲突。
- 复核时点：① push 前 ✅（本节）；② gh pr create 前 ✅（同一次，间隔内无新提交）。
