"""3D 结果的交互失效裁决（G3D04 M6）。

语义边界（谁失效谁不失效）：

- **观察点移动**（``feature_selected`` 换点 / AOI 变更）→ 分析失效
  （视域是观察点的函数）→ ``stale_observer_moved``，建议重算；
- **数据输入变化**（DEM 源被移除/替换）→ 分析失效 → ``stale_inputs``；
- **图层显隐**（用户隐藏结果层）→ 分析**不**失效，但"用户可见性"
  失效 → ``display_stale``——完成判据不得宣称"用户可见"；
- **相机变化**（viewport_settled）→ **不失效任何分析**（视域与镜头
  无关）——把相机当分析输入是范畴错误，本模块显式拒绝。

输入是 Interaction IR 投影快照 + MapSpec 权威面（零新真相）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

#: 失效状态词表。
STALENESS_STATES = ("fresh", "stale", "display_stale")


class AnalysisBinding(BaseModel):
    """一条 3D 分析结果与它的语义输入绑定（运行时登记的值对象）。"""

    model_config = {"extra": "forbid"}

    analysis_goal: str                          # viewshed/line_of_sight/...
    result_layer_id: str = Field("", max_length=128)
    observer_feature_id: str = Field("", max_length=128)
    dem_source_id: str = Field("", max_length=128)
    observer_x: Optional[float] = None
    observer_y: Optional[float] = None
    spec_mutation_revision: Optional[int] = None

    def observer_fingerprint(self) -> str:
        return f"{self.observer_feature_id or ''}|{self.observer_x},{self.observer_y}"


class StaleVerdict(BaseModel):
    """失效裁决（typed；recommendation 是受约束动作词表）。"""

    model_config = {"extra": "forbid"}

    schema_version: str = "scene3d_staleness.v1"
    state: str                                   # STALENESS_STATES
    reason_code: str = ""
    detail: str = Field("", max_length=200)
    recommended_action: str = ""                 # none | recompute | disclose_hidden
    stale_inputs: List[str] = Field(default_factory=list, max_length=4)

    @property
    def needs_recompute(self) -> bool:
        return self.state == "stale"


def evaluate_staleness(
    binding: AnalysisBinding,
    *,
    interaction_snapshot: Optional[Dict[str, Any]] = None,
    current_dem_source_ids: Optional[List[str]] = None,
    current_mutation_revision: Optional[int] = None,
) -> StaleVerdict:
    """一条 3D 分析结果的失效裁决（纯函数，确定性）。

    Args:
        binding: 分析登记（观察点/DEM 绑定/结果层）。
        interaction_snapshot: ``project_interaction_snapshot`` 产物。
        current_dem_source_ids: 当前 spec 的 raster-dem 源 id 集。
        current_mutation_revision: 当前 MapSpec mutation revision。
    """
    snap = interaction_snapshot if isinstance(interaction_snapshot, dict) else {}

    # ── 数据输入失效：DEM 绑定源不存在于当前 spec ─────────────────────
    if current_dem_source_ids is not None and binding.dem_source_id \
            and binding.dem_source_id not in current_dem_source_ids:
        return StaleVerdict(
            state="stale",
            reason_code="SCENE_STALE_DEM_SOURCE_GONE",
            detail=f"分析绑定的 DEM 源 {binding.dem_source_id!r} 不在当前 spec "
                   "sources——分析输入已失效",
            recommended_action="recompute",
            stale_inputs=[binding.dem_source_id],
        )

    # ── 观察点失效：用户选择了新的观察要素（feature_selected 语义） ──
    selection = snap.get("selection")
    if isinstance(selection, dict):
        sel_feature = str(selection.get("feature_id") or "")
        sel_layer = str(selection.get("layer_id") or "")
        if sel_feature and sel_feature != binding.observer_feature_id:
            # 用户点选了非分析观察点的要素 → 观察点语义漂移（点别的看）
            return StaleVerdict(
                state="stale",
                reason_code="SCENE_STALE_OBSERVER_MOVED",
                detail=f"用户当前选择 {sel_layer or '?'}/{sel_feature} 与分析"
                       f"观察点 {binding.observer_feature_id or '坐标点'} 不符"
                       "——观察点语义已漂移",
                recommended_action="recompute",
                stale_inputs=["observer"],
            )

    # ── 显示失效：结果层被用户隐藏（分析不失效，可见性失效） ─────────
    overrides = snap.get("user_overrides")
    if isinstance(overrides, dict) and binding.result_layer_id:
        entry = overrides.get(binding.result_layer_id)
        if isinstance(entry, dict) and entry.get("visible") is False:
            return StaleVerdict(
                state="display_stale",
                reason_code="SCENE_STALE_RESULT_HIDDEN",
                detail=f"结果层 {binding.result_layer_id} 被用户隐藏——"
                       "分析结论仍有效，但不可宣称'用户可见'",
                recommended_action="disclose_hidden",
            )

    # viewport/相机变化**不**进入失效判定（视域与镜头无关——显式拒绝
    # 把相机当分析输入的范畴错误；调用方不得据 viewport_settled 失效）。

    return StaleVerdict(
        state="fresh", reason_code="SCENE_STALE_FRESH",
        detail="分析绑定与当前输入一致",
        recommended_action="none",
    )
