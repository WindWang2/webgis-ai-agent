# G03-GEODATA-READINESS — M8 独立评审与清偿记录

## 评审轮次 1（子代理 B，冷读，2026-11-07）

对象：`git diff origin/master...HEAD`（30 文件 +3075/-8）。
方法：全量冷读 + 关键支撑事实溯源；F1/F3 以独立脚本本机实证复现。
**结论：需修复后合并**（1×P1、4×P2、10×P3）。

### 发现与清偿

| # | 级 | 发现 | 清偿（commit 见账本 M8a） |
|---|---|---|---|
| F1 | P1 | 守卫步数预算耗尽静默跳过——LIFO 序 + 共享预算可把 NaN/深结构/超大串藏垫料后绕过（实证）；>20k 顶点真实几何也会触发尾部检查截断 | **守卫重写**：坐标扫描（100 万步/要素 ≈33 万顶点）与属性扫描（20k 步/要素）独立预算；任何一路耗尽 → 类型化 `GUARD_BUDGET_EXCEEDED` 拒绝，无静默路径。回归测试复现全部三种绕过反例 + 真实 3 万顶点边界不误拒 |
| F2 | P2 | ADR D2 写 license 入指纹，与代码/测试（不入指纹）正面矛盾 | ADR 修订：明确不入指纹及理由（与 ADR-0265 一致），消费面走 source 绑定透传 |
| F3 | P2 | task_scope/descriptor bbox 含 NaN/Inf 伪通过（NaN 比较恒 False；实证 compatible） | `_normalize_bbox` 全分量 isfinite；descriptor `_bbox` 校验器同修；preflight 区分「未声明」与「声明不可解析」（后者 → SCOPE_UNVERIFIABLE，评审 F14 一并清偿）。测试复现 NaN 反例 |
| F4 | P2 | 深度帽不作用于坐标树（docstring 虚标共用） | 坐标扫描内建深度计数（>64 → STRUCTURE_TOO_DEEP）；200 层嵌套坐标测试锁定 |
| F5 | P2 | mapspec task_scope 接线零测试 + 吞异常无日志 | 集成测试：同数据同城重绑无 TASK_SCOPE_CHANGED / 切城 → recompute+TASK_SCOPE_CHANGED（真实 source_profile 路径）；except 加 warning 日志 |
| F6 | P3 | query 静默忽略 QuerySpec 谓词；capabilities 虚报 projection | metadata 披露 `filters_not_evaluated: true`；capabilities 收敛为 ["limit"] |
| F7 | P3 | 负数 limit 变尾部切片 | clamp [0, cap]；测试锁定（limit=-5 → 空结果 + truncated=True 诚实披露） |
| F8 | P3 | 每次 dispatch 无条件读 recovery state | 参数无数据 ref 时短路（collect_gate_refs 先行） |
| F9 | P3 | >16 ref 静默逃逸 | verdict 披露 `__refs_capped__` |
| F10 | P3 | "inferred" 无生产者但文档写成现行 | 契约文档标注 reserved |
| F11 | P3 | 字符串坐标行静默无语义身份 | debug 日志（诚实缺席有痕） |
| F12 | P3 | 共享拆包无直接特征化测试 | to_feature_collection/inspect_semantics 两处直接单测（含非 FC 内层不误拆） |
| F13 | P3 | 慢滴服务器无总时长限制 | overall deadline（(帽/64KB)×per-read，120s 硬顶） |
| F14 | P3 | 坏声明 bbox 静默当未声明 | 随 F3 清偿（SCOPE_UNVERIFIABLE + detail 区分） |
| F15 | P3 | describe 以首见几何类型代表混合 FC | join 披露 |

### 评审确认可靠项（抽查）

SSRF 每跳重校验/Content-Type fail-closed/流式预算无 chunked 绕过/闸门
fail-open 与 dedup 槽释放/取消回滚幂等/性能不变量机器无关/journey 真实
算法非 stub。

## 轮次 1 修复后验证

- `tests/data/test_g03_review_fixes.py` 14 passed（含全部实证反例复现）；
- g03 全家 + 邻接面回归：190 + 185 passed；
- ruff（改动面）/mypy（新模块）全绿。

（轮次 2 复审结论见下节，复审后追加。）

## 评审轮次 2（复审，2026-11-08）

对象：修复 commit 88a56ff75。方法：全量重读 + 新守卫 6 组对抗形状独立
再攻击 + F3 三面闭环实测。

**结论：F1-F15 全部 fixed（逐项实证复核）；修复未引入性能回归/None
properties 问题。可合并（附带一条建议同 PR 快修的 P2）。**

新发现与清偿（同 PR 完成）：
- N1（P2，GeometryCollection 内层 NaN 逃逸，实证）→ `_scan_geometry`
  迭代展开 geometries 数组（防御性支持嵌套），成员坐标独立走扫描；
  反例测试（内层 NaN 拒绝 + 合法集合不误拒）已加。
- N2（P3，契约文档 §5 漂移）→ 守卫表补 GUARD_BUDGET_EXCEEDED/双预算/
  坐标树深度帽；ADR D7 同步。
- N3（P3，preflight 数据侧 isfinite 防御纵深）→ 判别分量全 isfinite，
  非有限 → unverifiable。
- N4（P3，iter_content 原生异常）→ 断流/超时统一 typed
  SourceUnreachableError。

观察项（接受，不阻断）：单要素 >33 万顶点合法重几何 → GUARD_BUDGET_
EXCEEDED 类型化拒收（fail-closed 且披露；50MB 帽内可达的极端海岸线
形状，可辩护权衡）。

最终：**无未处理 P0/P1；N1-N4 已清偿；独立评审签结可合并。**
