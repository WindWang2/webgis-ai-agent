# G06 — PR 交付清单

> 在 M9 完成后填入真实值。**没有真实 PR URL 不得声称已提交 PR。**

## 交付事实（M9 后填）

| 项 | 值 |
|---|---|
| 实时 `origin/master` | `585812ee4e2d75612f2b124e6fe6587af3deca5c`（执行开始刷新；M9 再刷新） |
| 目标 branch | `zcode/goal-gis-capability-ecosystem-20261107` |
| worktree | `/home/kevin/projects/webgis/webgis-wt-gis-capability-ecosystem-20261107` |
| head SHA | （待填） |
| commit 列表 | （待填） |
| PR URL | （待填；`gh pr create --base master --head ...`） |

## PR body 骨架（实际提交时展开）

1. **问题**：声明/历史通过与当前可执行混淆；检索不感知可用性；
   秒/米与经纬度/米缺统一预检；provider 资格分散；
   两项原生原语缺口；结果摘要失真无对拍面。
2. **实现**：`CapabilityAvailabilitySnapshot` 只读投影 + 5 状态词表；
   三态科学预检；可用性重排 + 黄金语料；provider 资格只读转换；
   两项真实原语；schema/摘要对拍；示范包与 5 分钟验收脚本。
3. **兼容性**：algorithm count 仍 253；既有工具名/签名不变；
   `to_dict()` additive + `schema_version` 版本门；不改 dispatch / Pi 宿主 /
   压缩器 / 数据 fabric / MapSpec。
4. **证据**：14 Oracle 全绿；gis 全套 798 passed / 3 skipped；
   catalog/元数据/Pi 面 78 passed / 1 skipped；ruff + mypy clean；
   一键验收 6/6；`execution_catalog_docs --check` up to date。
5. **测试命令**：见 `TEST_MATRIX.md` §2。
6. **风险与未验证项**：PostGIS opt-in；无真实 LLM 公网旅程；
   线上 CI 不等待、不宣称通过；Issue #2007 历史生成物债务；
   新原语诚实标 `available_unverified`（未挂算法链）；
   扩展 lease 未引入；演示为本地 fixture。
7. **避让声明**：#2005/#2006 只做只读对拍，不抢独立 issue；
   G01–G05 文件未触碰；Pi v1.5.0 已集成，不作为新功能。

## 提交前门禁（逐条确认）

- [ ] `git diff --check` clean
- [ ] `git status --short` 无敏感凭据 / 缓存 / 巨型中间产物 / 偏离目标重构
- [ ] 实时 master / PR / Reviews / Issues 已复核，无照搬已合并功能、无撞在途 PR
- [ ] 必要 rebase 已完成，未改写他人提交
- [ ] 独立冷审 P0/P1 清零，二次 review 通过
- [ ] 本地验收全部完成，不等待线上 CI
- [ ] `gh pr create` 返回真实 URL
