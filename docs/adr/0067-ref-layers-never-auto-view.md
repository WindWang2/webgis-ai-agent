# ref 层永远不做 auto-view：取景是显式动作

Date: 2026-08-21

## Status

Accepted

## Context

#688 让授权路径从 ref descriptor O(1) 派生 Spatial Meta Profile。派生 profile 的 `suggestedView` **恒为空**——实现时的直接理由是技术保守：descriptor 不携带 CRS，投影坐标系的数据上按 WGS84 推导 view 会给出错位几万公里的取景。

但这条规则的实际效果是产品级的：**ref 层（即所有大数据集层）永远不做自动取景**。grilling 追问了规则的本质，判定为**产品语义而非技术保守**：

- #680 证明 ref 层的 auto-view 因双门条件（ref 载体跳过 profiling + crs 显式声明）从未真正工作过——用户从未拥有过这个行为，也就从未依赖它。
- "上传/查询完成即自动飞行到几万要素的 bbox"对用户是惊吓行为（视角被劫持），且 bbox 取景对点云类数据经常给出无意义的全城视野。
- 取景的正确触发是显式动作：`zoom_to_bbox` / `fly_to` / 用户手势 / agent 的明确意图。

## Decision

1. **ref 层不做 auto-view 是产品语义**：即使未来 descriptor 增加 CRS 字段（技术前提消失），派生 profile 也不恢复 `suggestedView`——要恢复必须显式修订本 ADR。
2. 派生 profile（`profile_from_descriptor`）的 `suggestedView` 恒为 `{}`；全量 profiler 对非显式地理 CRS 同样返回空——两条路径在边界上一致。
3. 取景由显式动作驱动；agent 需要"给用户看某层数据"时应在工具调用里表达取景意图，而不是依赖授权副作用。

## Consequences

- 若产品未来要"上传后自动带用户去看"，这是一个需要产品决策 + 本 ADR 修订的功能，不是一个 bug。
