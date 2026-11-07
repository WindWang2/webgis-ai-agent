# G01-PI-NATIVE-RUNTIME — goal-loop 目标级台账

| 轮 | 改动 | 验证结果 | 通过否 | 下一步 |
|---|---|---|---|---|
| 1 | M0 审计：fetch origin/master=585812ee4、8 open PR（全 dependabot）、#1999-2007 issues 避让决策、worktree 建立、vendor/pi init 至 v1.5.0 gitlink f9a26365、子代理 A 深读审计（G1-G9 缺口清单） | BASELINE.md 落盘；无冲突 | 通过 | M1 基线 |
| 2 | M1 基线：Pi 测试 5 批 465 passed；build-vendor-pi.sh 成功；vendor contract+golden 9 passed；PI_JOURNEY_E2E=1 stub 旅程 1 passed（40s）；pi_doctor 6 域全 PASS | 合计 475 passed / 0 failed | 通过 | M2 设计+失败测试 |

> **事故记录（第 6 轮后）**：在共享 bare 仓库的多 worktree 环境误用 `git stash`，
> 弹出了其他 worktree（codex/all-issues-deep-review）的共享 stash 造成 UU 冲突。
> 处置：`git reset --hard HEAD`（仅本 worktree，4 个提交无损）+ 恢复误删的被跟踪
> .claude 文件 + 保留外来 stash@{0} 不动。**纪律：本仓库永不使用 git stash**。
