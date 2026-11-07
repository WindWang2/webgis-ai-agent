# REVIEW — G05-SITUATED-WORKBENCH

## R1（独立复审，子代理 B 冷读，2026-11-08）

- 范围：`git diff origin/master...c90fc76f9`（8 commits）
- 判定：**BLOCK**（P0×1 / P1×3 / P2×2 / P3×10）
- 复审人实测复现 P0（本机 32 字符 id×35 起 accepted-but-dropped）+ 8 组
  篡改演练 + 全部相关套件复跑（188/31/25/212 后端 + 107/12 前端全绿）。

### 清偿（R1 → fix commit）

| 发现 | 级别 | 清偿 |
|---|---|---|
| P0-1 `_MAX_RECORD_BYTES=1536` 未随 4096B 宽预算 → 真实前端 32 字符 id 框选事件 accepted-but-dropped（Agent 比修复前更失明） | P0 | `ingest.py` 记录预算 = `MAX_PAYLOAD_BYTES_SELECTION+512`；关系测试改用宽预算并钉扎等式；新增 32 字符 id×50 全链 ingest 测试（accepted 且入环且投影 50+matched80+truncated）；金标 `box_select_frontend_capped_50_sample`/`box_select_20_range_faithful` 改 32 字符 id（真实 emit clip 对齐） |
| P1-1 required+pending 时终验收口行显示"自动确认"（错误披露、与交互卡自相矛盾） | P1 | `FinalAcceptanceLine` 补 pending 优先分支（"等待用户确认"新 i18n 键 zh/en）；显示条件简化为水合即显示；补 required+pending 组件测试（断言不出现"自动确认"）；ADR-0287 D3 补 pending 态 |
| P1-2 框选 bbox 在验证面被数据集 bbox 覆盖（范围放大误导） | P1 | ValidatedScope 新增独立键 `selection_bbox`（claimed bbox 透传；`bbox` 保留数据集描述符语义）；验证面 fake-store 测试断言两键分义；契约文档 §2.2 同步 |
| P1-3 短 id 金标掩盖 P0 + 文档数字过期 + "不静默"声称失真 | P1 | 金标 32 字符 id（见 P0-1）；TEST_MATRIX 29→31；契约文档预算段补记录预算分层；ADR Consequences 修正（~21 条满载 + 记录预算关系钉扎） |
| P2-1 manifest 双闸前端侧缺口（50/20 上限前端无断言） | P2 | `contract.test.ts` 新增三方对齐闸（ids/categories × manifest↔镜像↔常量）+ 宽预算分层断言；`CONTRACT_MAX_CATEGORIES` 镜像导出 |
| P2-2 pre-turn matched_count 无上界（绕过 IR 面 10M 上限） | P2 | `_scope_disclosure` 收敛 `0 ≤ matched ≤ MAX_MATCHED_COUNT`（pre-turn 唯一披露入口；不改 chat.py——#2012 避让）+ 测试 |
| P3-1 bbox 宽进严出不对称 | P3 | normalize 阶段 bbox 收敛 4 元有限数值（畸形→空列表） |
| P3-2 `SelectionEpoch.truncated` 恒 False 误导 | P3 | 注释标明防御层语义（消费方读 ids_truncated） |
| P3-3 categories >20 截断无披露 | P3 | 文档记录为类别谓词既定语义（前端限 20 正常链路不触发）；follow-up |
| P3-4 id 裁剪三方不一致（32 vs 64 vs 128） | P3 | bus.ts emit clip 32→64（与 brush/selection-store 对齐；预算 50×66≈3.3KB<4096） |
| P3-5 pre-turn ids_truncated 镜像缺席 | P3 | follow-up（pre-turn 为回退路径，前端已 ≤50 不触发；chat.py 避让 #2012） |
| P3-6 测试名与断言矛盾 | P3 | 改名 `..._invertible_with_numeric_before` |
| P3-7 chart select value 不在 options 显示空白 | P3 | safeValue 回退 '' |
| P3-8 矛盾披露无交叉校验 | P3 | 有意设计（防误伤去重），记录 DECISIONS D15；测试钉扎 |
| P3-9 ADR 判据 2 未回写 | P3 | ADR-0287 D1 补双判据 + pre-turn 上界说明 |
| P3-10 receipt-bus after 数值不对称 | P3 | after 任一键非数值整效果不录 |

### 未清偿（follow-up 记录，非 blocker）

- P3-3 categories 截断披露键（类别谓词语义已文档化）
- P3-5 pre-turn ids_truncated 镜像（chat.py 属 #2012 避让面）
- 复审确认的有意设计：单选不上行 matched_count（不虚报）；矛盾披露
  matched<ids 原样透传（可能去重，见 D15）；recharts 内核键盘化不做（D10）。

## R2（同员复审清偿结果，2026-11-08）

- **结论：SHIP（P0=0，P1=0）**。
- R1 全部 P0/P1/P2 验证清偿（各有钉扎测试，非仅改码）：P0 复现脚本重跑
  32 字符 id ×20/30/35/40/50/60 全部 accepted+入环；P1-1 pending 分支
  组件测试断言"不含自动确认"；P1-2 FakeStore 双断言两 bbox 分义；
  P2-1 前端闸断言；P2-2 巨数/负数/bool 收敛实测。
- R2 遗留非阻塞 3 项 → 已当场清偿（见下）。

## R2 遗留清偿（N-1/N-2/N-3）

| 发现 | 清偿 |
|---|---|
| N-1 (P2) 满信封伪造仍 accepted-but-dropped | `_append_bounded` 返回 bool；超记录预算 → **rejected/record_budget 诚实裁决**（不再谎报 accepted）；钉扎测试（伪造满信封 64 字符 id → rejected+reason） |
| N-2 (P3) clip 64 后文档/测试注释滞后 | 测试改名 `..._full_width_ids_...` 并改 64 字符（钉扎最宽真实 id）；ADR 50×64 措辞 |
| N-3 (P3) bbox 非 list 绕过收敛 | 非 list bbox 摄入即丢键；钉扎测试（字符串/dict/int 三类） |

最终状态：R1+R2 全部发现（P0×1/P1×3/P2×3/P3×11）清偿或记录为
有意设计/follow-up（P3-3 categories 披露键、P3-5 pre-turn 镜像
——chat.py 属 #2012 避让面）。
