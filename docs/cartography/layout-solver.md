# Layout Solver Contract（后端 V2）

## 定位

- 后端 `app/lib/cartography/layout_solver.py`：**结构化**求解（槽位分配、
  容量、抑制决策）—— 供组合校验、golden 语料、服务端证据使用。
- 前端 `frontend/lib/map-components/resolve-layout.ts`：**像素**求解
  （堆叠偏移、高度预算）—— live/export 渲染的单一真值（ADR-0084）。
- 两者共享同一组槽位/优先级约定（`COMPONENT_LAYOUT_META` ↔
  descriptor `default_position`/`priority`，由测试锁定不漂移）。
- 后端不做像素假设；前端不改变语义。

## 算法（单遍、确定性）

输入：`LayoutParticipant[]`（id/type/requested_zone/priority/optional/
fallback_zones）+ `page_profile`。

1. 排序键：`(optional, priority, id)` —— required 先于 optional，
   priority 升序（小者优先），id 字典序破平。
2. 逐个分配候选槽序：requested → slot fallback_zones →
   `ZONE_POSITIONS` 邻接表。
3. 接受条件：槽容量未满（`ZONE_CAPACITY × profile multiplier`）、
   exclusive 槽未被占（top-center）、singleton 类型未重复
   （title/north_arrow/scale_bar/attribution）、顶/底槽累计预算未尽。
4. 溢出处置：
   - `optional` → 确定性抑制（`reason="overflow_suppressed"`）+ warning；
   - required → 保留在 requested 位置（`required_overflow_kept`）+ warning
     —— 功能性组件不静默消失。
5. `none` 槽（map_border/graticule/export_layout）不占容量直接通过。

## Page Profiles

| profile | multiplier | top 预算 | bottom 预算 |
|---|---|---|---|
| viewport | 1 | 3 | 3 |
| a4_portrait | 1 | 3 | 4 |
| a4_landscape | 2 | 4 | 4 |
| presentation_16x9 | 2 | 4 | 3 |
| academic_figure | 2 | 3 | 4 |

## 输出

`LayoutSolution{placements, suppressed, warnings, page_profile}` ——
pydantic 模型，可序列化（golden 语料的 layout 段即其投影）。

## 保证

- 无循环、无随机、无时间相关输入：同输入永远同输出（golden 锁定）；
- 组件不凭空消失：每个参与者必出现在 placements 或 suppressed；
- 与 `detect_collisions`（QA 报警）并存，既有校验路径行为不变。
