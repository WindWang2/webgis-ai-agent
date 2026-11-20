# G32 PR_HANDOFF

- **PR 标题**: feat(g32): 持久要素变更提案与字段、几何冲突审阅
- **base**: ca7b3ec273f2b43336852951e2292a416b2238e6(origin/master,fetch 复核未前移)
- **branch**: zcode/g32-vector-change-proposals-20261112T174659Z
- **head**: 见 PR(5 commits:d962237b1 核心 / 4352ffc0d 前端 / 0e38cefa7 Pi 全链+文档 / 6cf8f699f review 修复 / 42be3d023 R2 清偿)
- **worktree**: /home/kevin/projects/webgis/webgis-worktrees/g32-vector-change-proposals-20261112T174659Z
- **正文**: planning/G32-VECTOR-CHANGE-PROPOSALS/PR_BODY.md

## 验证证据(全本地,不等线上 CI)

| 门 | 结果 |
|---|---|
| pytest tests/unit/feature_proposals/ + tests/unit/feature_edit/ | **168 passed**(45 本域 + 123 G14 回归) |
| Pi 全链(真实 vendor Pi 子进程 pin b4afdddf + 生产扩展 + 真实 dispatch + 生产 app;PI_JOURNEY_E2E=1) | **4 passed** |
| 前端 vitest(d drawer) | **4 passed**;eslint --max-warnings 0;tsc 通过 |
| ruff(本域全部新改文件) | 全绿 |
| git diff --check | OK |

## 独立评审

request-changes(2×P0+3×P1+4×P2+10×P3,实证)→ 全修复+10 回归 → 复审 **approve**(reviewer 复核"全部真实修复";新报 2×P2+6×P3)→ 2×P2+1×P3 同批清偿+2 回归。全程见 REVIEW.md(三轮)。

## 已披露未测项

真实浏览器 Playwright E2E;P21 MapSpec stamp 延迟注入;大规模性能基准;R2-4/6/7/8(P3 边缘/可选)。

## 回退

删除 main.py/tools 注册 4 行 + 移除新增目录;G14 适配参数均为可选,默认路径行为不变(123 回归证明)。
