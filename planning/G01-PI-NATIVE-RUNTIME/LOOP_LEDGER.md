# G01-PI-NATIVE-RUNTIME — goal-loop 目标级台账

| 轮 | 改动 | 验证结果 | 通过否 | 下一步 |
|---|---|---|---|---|
| 1 | M0 审计：fetch origin/master=585812ee4、8 open PR（全 dependabot）、#1999-2007 issues 避让决策、worktree 建立、vendor/pi init 至 v1.5.0 gitlink f9a26365、子代理 A 深读审计（G1-G9 缺口清单） | BASELINE.md 落盘；无冲突 | 通过 | M1 基线 |
| 2 | M1 基线：Pi 测试 5 批 465 passed；build-vendor-pi.sh 成功；vendor contract+golden 9 passed；PI_JOURNEY_E2E=1 stub 旅程 1 passed（40s）；pi_doctor 6 域全 PASS | 合计 475 passed / 0 failed | 通过 | M2 设计+失败测试 |

> **事故记录（第 6 轮后）**：在共享 bare 仓库的多 worktree 环境误用 `git stash`，
> 弹出了其他 worktree（codex/all-issues-deep-review）的共享 stash 造成 UU 冲突。
> 处置：`git reset --hard HEAD`（仅本 worktree，4 个提交无损）+ 恢复误删的被跟踪
> .claude 文件 + 保留外来 stash@{0} 不动。**纪律：本仓库永不使用 git stash**。
| 7 | W6a: PI-09 旅程扩展（J6 wrap 拒绝/J6b 两段式邻近真实计算/J7 resume 零 LLM）+ 发现并修复长尾 dormant 工具参数投影 P0（schema 未解包→空 closed schema→vendor 拒参） | 旅程连跑两遍 1 passed（33.6s/32.9s）；vendor contract+golden 9；surface 族 54；commit 5ab9333bc | 通过 | W6b 文档 |
| 8 | W6b: PI_RUNTIME_CONTRACT.md + PI_UPSTREAM_COMPAT_MATRIX.md 落盘（每条带测试锚点） | 文档与代码同步 | 通过 | M8 独立 review |
| 9 | M9 预备：全量 Pi 回归 5 批 590 passed + 新测试批 135；发现并修复本方向引入的测试污染（包级 run_settle_effects 打桩物化 PEP562 惰性委托，monkeypatch 恢复机制反噬）→ 手工保真恢复 | batch5 修复后 135 passed（此前 134+1 failed）；pi_doctor 6 域全 PASS + runtime mode 行；全 diff ruff 干净；repo mypy 门绿（35 files）；diff --check 干净；commit 7e6c0e7e5 | 通过 | M8 review 结论 → 修复 → PR |
| 10 | M8-R1 复审（子代理 B 冷读全 diff）：P0×0/P1×3/P2×10 → 清偿 P1-1（回收后活复检）/P1-2（recycle 折入 turn 锁临界区，锁序核实无死锁）/P1-3（撤销 5s 预算）+ P2×10（commit 50c517161） | 新测试 53 passed；受影响回归 77；旅程 1 passed | 通过 | R2 |
| 11 | M8-R2 复审：P0×0/P1×0/P2(新)×3 放行；随手清偿 probe_failed 词表+测试/契约句/teardown try/finally | disclosure+refresh+perf 26 passed | 通过 | M9 PR |
