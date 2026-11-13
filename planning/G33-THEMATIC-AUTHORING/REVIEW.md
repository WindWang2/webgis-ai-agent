# G33 REVIEW — 独立评审记录

## Round 1（reviewer：独立 subagent 槽 C，未参与实现）

- 结论：**NO-GO as-is**——3×P1 + 2×P2 + 7×P3；核心链路（typed 计划/同版本
  统计/统一求值器/单笔 CAS/undo/权限/资源/Pi 旅程）判可合入。
- 复跑证据：thematic 71+2skip、perf 2、共享合同 42、cartography 大面
  3649+4skip（1 失败为 master 既有、与本分支无关）、前端 12+tsc、Pi 旅程 1
  通过；三个 P1 均有真实链路复现脚本（/tmp/g33_review/）。

### Findings 与修复（commit 63161bb7c）

| ID | 问题 | 修复 |
|---|---|---|
| P1-1 | 共享表达识别用不存在形状（shared_expressions/layer_ids），真实 G15 契约（shared_expression 单数 + frames[].data.layer_id）永不触发 | service._shared_expression_scope 按真实契约重写；测试用真实 FrameProductContract.v1 + 强制断言 |
| P1-2 | size_channel 换通道留幽灵 paint 键，revert_auto 也清不掉 | 换通道先 pop 本域旧键；回归测试覆盖换向与 revert |
| P1-3 | 类别 key_type "bool" live 镜像判 "boolean"——bool 类别层 live 全落 default | thematic-paint.ts 对齐 "bool"；前端回归测试（typed boolean cases） |
| P2-1 | revert_auto 对文本类别层必然 NO_NUMERIC_DATA | 证据块存 previous_legend_spec 快照；revert 优先恢复快照（preview/commit 同候选 _candidate_for）；无快照类别层保持当前表并披露 |
| P2-2 | 共享域测试空转通过模式（if 包裹） | 移除；scope 识别强制断言 |
| P3-1 | ambiguous cap 5 拒绝面不足 | cap→64（仍受扫描帽约束） |
| P3-2 | 扫描内存 O(distinct) | 记录为已知边界（要素已整体水合前提；未改——避免为假想规模加复杂度） |
| P3-3 | 工具 apply 无 mutation_id | 确定性 id = 计划指纹×修订（重放幂等） |
| P3-4/6 | class_colors/labels 超出实际类数静默截断；全量覆盖后 palette 溯源失真无披露 | builder 披露两条（长度偏差 + palette_id 溯源说明） |
| P3-5 | 通道/层型不匹配 live 无声无效 | 入口 SIZE_CHANNEL_LAYER_TYPE_MISMATCH |
| P3-7 | 测试计数口径 73 实为 71+2 | 账本改记 74 passed + 2 skipped（perf 隔离另跑 2） |

### 修复后本地验证（全部重跑）

- 后端 thematic_authoring：74 passed + 2 skipped（perf 另跑 2 passed）
- 共享合同回归（jenks/legend_v2/converter/mapspec_to_svg/raster/cartography）：153 passed + 1 skipped
- Pi 真实旅程（PI_JOURNEY_E2E=1）：1 passed
- 前端：13 passed + tsc 干净；ruff 全绿

## Round 2（复审：新独立复审者，携 Round-1 报告；未参与实现）

- 结论：**GO**。逐项独立真实链路复现验证（不 import 作者测试助手；脚本
  /tmp/g33_round2/）：
  - P1-1 PASS（行为矩阵：shared/shared_plus_frame/independent/enabled=False/
    shared_expression=None 全对；fixture 空转 if 已移除）
  - P1-2 PASS（含作者测试未覆盖的「circle 写 radius → 层型被外部改 line →
    width 提交」分支；外部键无误伤）
  - P1-3 PASS（四端词表链 stats→builder→thematic_spec→thematic-paint 无
    "boolean" 残留；前端回归 13 passed）
  - P2-1 PASS（_candidate_for 分派矩阵逐格验证；快照有界 1143B；preview/
    commit 恢复候选逐字节相等；二次 revert 单步语义正确）
  - P2-2 PASS；P3 抽查全 PASS（P3-3 幂等重放/CAS 不误吞双验证；P3-2 维持
    已知边界的论证被复核接受）
- 复跑全绿：74+2skip / perf 2 / 前端 13+tsc / Pi E2E 1 / ruff。
- 新发现 4×P3（不阻塞）：1) revert 快照路径 binding.field 分歧；2) 共享
  fixture 未过权威 schema；3) dedup 命中回执口径；4) 三个 P3 行为缺测试锚。

### Round-2 P3 落地（后续 commit）

- 新发现1：binding.field 跟随恢复 spec 的 thematic_field
- 新发现2：fixture 过 FrameProductConfig.model_validate（并按真实词表修正：
  frame_product.v1 / category_compare / grid.columns / breaks=k+1）
- 新发现3：duplicate 回执改描述库内已提交表达（deduplicated=True + 披露）
- 新发现4：三条测试锚（cap64 / 重放幂等+CAS 不误吞 / 披露文案）
- 无害瑕疵清理（重复 prev_authoring 赋值）
- 终验：thematic 77+2skip、Pi 旅程 1、共享合同 75+1skip、ruff 全绿
