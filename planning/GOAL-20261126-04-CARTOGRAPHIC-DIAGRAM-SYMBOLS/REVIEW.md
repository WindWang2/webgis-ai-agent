# REVIEW — 独立审查两轮（A 科学语义 / B 安全同源）+ 修复记录

## 第一轮（reviewer A + B，逐行 + 实际复现）
- A：**P0×1**（默认 size_policy 裸 KeyError→500）+ **P1×2**（全零组成要素阻塞整层；SVG twin features/values 对齐卫兵整层丢弃）+ P2×4（segmented 误纳 total_area_encoding 虚假声明；前端 svg-emit 3位舍入/缺 donut 全圆 fallback/大弧 flag 口径；Math.max spread 上限；数值字符串静默转型）+ P3×8。正面确认：角度/面积/长度语义、量化单点、fsum/pythonRound 位级孪生、布局确定性、schema additive、fixtures 逐字节一致。
- B：无 P0；**P1×1**（layer.ts 假缓存声明 vs 每帧重三角化）+ **P2×5**（reference_zoom 无校验→500；bind 覆写 user legend_spec；superseded 被面板报成功；plan 端点 max_glyphs=20k 碰撞最坏 O(n²·96) 同步阻塞；前端 SVG 分支不过滤/不截断）+ P3×9。正面确认：转义全覆盖、无注入、三路由鉴权真校验、preview 零突变/CAS/幂等有测试钉、PNG 同 buffer、fixtures 逐字节一致。

## 第一轮修复（commit 见 git log）
已修（P0/P1 全部 + P2 5 项）：
1. A-P0：service 走 normalize_size_policy 补默认（typed INVALID_SIZE_POLICY），缺省键不再裸 KeyError
2. A-P1#2：全零组成/分段要素 skip-with-accounting（zero_total_features 会计），geometry ZERO_TOTAL 保留为最终防线
3. A-P1#3：SVG twin 按 glyph_feature_ids 重排 features（与 service/前端孪生同构）
4. B-P1：layer.ts 落地真实 WeakMap 三角化缓存 + 文档改真
5. B-P2：reference_zoom 有限性 typed 校验（与 TS 收紧口径对齐）
6. B-P2：bind 图例 user-wins（非自己写的 legend_spec 不覆写）
7. B-P2：面板 superseded/error 不再报成功（i18n notApplied）
8. A-P2：segmented + total_area_encoding typed 拒绝
9. A-P2：前端资格层数值字符串不再静默转型（typed FIELD_NOT_NUMERIC）
10. A-P2：svg-emit donut 全圆双半圆弧 fallback
- 验证：后端图符 106+33 绿、前端 105 绿、tsc 0、eslint 0、ruff 0 新增。

## 立案跟踪（本线边界外/低风险，合并说明披露）
- plan 端点 max_glyphs 上限与碰撞最坏路径成本（P2#3，鉴权会话内；建议后续分片/异步）
- 前端 SVG 分支不消费 layer.filter/截断（P2#6，后端为过滤导出权威；建议后续对齐）
- svg-emit 与后端 SVG 字节口径（2位 vs 3位小数、引线样式）——几何 oracle 已位级一致，path 字节非 parity 面（P2#5 残余/P3#9）
- 前端 SVG 会计属性不对称、GL blend 状态不复位、bar_gap 上限、死代码、8 方位注释、12 步位移保守性（P3 集合）
- hit-test circle proxy 未落（INTERFACES 承诺未兑现；交互命中暂走 base 层 query 通道）—— 立案
