"""G3D04 M1 · 3D 意图场景语料（30+ 典型意图 → typed 属性映射 + 断链缺口表）。

用途：

1. **cue 回归语料**：每条 ``expected_cues`` 是 ``extract_scene3d_cues`` 的
   确定性断言（tests/unit/gis_harness/scene3d/test_cues_corpus.py 钉住）；
2. **断链缺口表数据源**：``chain`` 记录该意图在链路
   （cue→IR→决策→工具→mutation→渲染→交互→验收）上的覆盖状态与归属
   （``now`` = 本方向交付；``g3d01/g3d02/g3d03`` = 相邻方向依赖；
   ``partial`` = adapter + fixture 面已通、真实面待相邻方向合入）。

纪律：expected_cues 是**用户话语表面**的投影断言，不是能力裁决——
"展示立体地形"在无 DEM 时仍 cue 出 dimension=3d+terrain=True，能力裁决
（诚实降级）是 decision 层的职责。
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

#: 场景元组：(id,话语,族,期望 cue dict,链路状态 dict)
_SCENARIOS: List[Tuple[str, str, str, Dict[str, Any], Dict[str, Any]]] = [
    # ── 维度表达（用户显式） ─────────────────────────────────────────
    ("s01", "展示成都市的立体地形", "dimension",
     {"dimension": "3d", "terrain_requested": True, "analysis_goals": []},
     {"cue": "now", "ir": "now", "decision": "now", "mutation": "now",
      "terrain_source": "g3d01", "render": "g3d02"}),
    ("s02", "把地图切换成 3D 视图", "dimension",
     {"dimension": "3d"},
     {"cue": "now", "ir": "now", "decision": "now", "mutation": "now"}),
    ("s03", "用三维地图显示小学分布", "dimension",
     {"dimension": "3d"},
     {"cue": "now", "ir": "now", "decision": "now", "mutation": "now"}),
    ("s04", "改成 2D 平面地图", "dimension",
     {"dimension": "2d"},
     {"cue": "now", "ir": "now", "decision": "now", "mutation": "now"}),
    ("s05", "换成二维视图", "dimension",
     {"dimension": "2d"},
     {"cue": "now", "ir": "now", "decision": "now", "mutation": "now"}),
    ("s06", "用 2.5D 晕渲表达地貌", "dimension",
     {"dimension": "2.5d", "terrain_requested": True},
     {"cue": "now", "ir": "now", "decision": "now", "mutation": "now",
      "terrain_source": "g3d01"}),
    # ── 零推断红线（无 3D cue 不得推断） ────────────────────────────
    ("s07", "显示成都小学分布情况", "no_inference",
     {"dimension": "", "terrain_requested": None, "analysis_goals": []},
     {"cue": "now", "ir": "now", "decision": "now"}),
    ("s08", "做一个人口密度热力图", "no_inference",
     {"dimension": "", "extrusion_requested": None},
     {"cue": "now", "ir": "now", "decision": "now"}),
    ("s09", "统计各区的 GDP 并制图", "no_inference",
     {"dimension": ""},
     {"cue": "now", "ir": "now", "decision": "now"}),
    # ── 立体挤出（建筑白模） ─────────────────────────────────────────
    ("s10", "添加建筑白模", "extrusion",
     {"extrusion_requested": True},
     {"cue": "now", "ir": "now", "decision": "now", "height_evidence": "now",
      "scene_asset": "g3d02"}),
    ("s11", "用建筑物高度做立体拉伸展示", "extrusion",
     {"extrusion_requested": True},
     {"cue": "now", "ir": "now", "decision": "now", "height_evidence": "now"}),
    ("s12", "建筑白模没高度数据的话就用平面图", "extrusion_honesty",
     {"extrusion_requested": True},
     {"cue": "now", "ir": "now", "decision": "now（诚实降级+披露）"}),
    # ── 地形/高程 ───────────────────────────────────────────────────
    ("s13", "叠加地形起伏效果", "terrain",
     {"terrain_requested": True},
     {"cue": "now", "ir": "now", "decision": "now", "terrain_source": "g3d01"}),
    ("s14", "加载 DEM 高程模型", "terrain",
     {"terrain_requested": True},
     {"cue": "now", "ir": "now", "decision": "now", "dem_pipeline": "g3d01"}),
    ("s15", "地形夸张 3 倍", "exaggeration",
     {"terrain_requested": True, "exaggeration": 3.0},
     {"cue": "now", "ir": "now", "decision": "now（钳制+失真披露）"}),
    ("s16", "垂直夸张 15 倍", "exaggeration",
     {"terrain_requested": True, "exaggeration": 15.0},
     {"cue": "now", "ir": "now", "decision": "now（钳到 10+披露）"}),
    # ── 相机/视角 ───────────────────────────────────────────────────
    ("s17", "从西南方向看", "camera",
     {"camera_bearing": 45.0},
     {"cue": "now", "ir": "now", "decision": "now", "mutation": "now（SetView）"}),
    ("s18", "从北边看这座城市", "camera",
     {"camera_bearing": 180.0},
     {"cue": "now", "ir": "now", "decision": "now"}),
    ("s19", "朝东南方向看", "camera",
     {"camera_bearing": 135.0},
     {"cue": "now", "ir": "now", "decision": "now"}),
    ("s20", "压低视角俯仰角 30 度", "camera",
     {"camera_pitch": 30.0},
     {"cue": "now", "ir": "now", "decision": "now（user-wins 锁）"}),
    ("s21", "鸟瞰整个城区", "camera",
     {"camera_pitch": None},
     {"cue": "now（意图词面）", "ir": "now", "decision": "now（推荐档）"}),
    ("s22", "换个角度看", "camera",
     {"camera_bearing": None},
     {"cue": "now（意图词面，不造角度）", "decision": "now"}),
    # ── 媒介（诚实降级） ────────────────────────────────────────────
    ("s23", "导出一张打印用的立体地图", "medium",
     {"dimension": "3d", "medium": "print"},
     {"cue": "now", "ir": "now", "decision": "now（静态媒介禁 3D→2.5d 降级+保留偏好）"}),
    ("s24", "出一张 PDF 静态图", "medium",
     {"medium": "export_pdf"},
     {"cue": "now", "ir": "now", "decision": "now"}),
    # ── 3D 分析目标 ─────────────────────────────────────────────────
    ("s25", "计算塔台的通视范围", "analysis",
     {"analysis_goals": ["viewshed", "line_of_sight"]},
     {"cue": "now", "ir": "now", "tool": "now（viewshed_analysis 真实 dispatch）",
      "algorithm": "g3d03（增强）", "result_attach": "partial"}),
    ("s26", "分析这个山脊的可视域", "analysis",
     {"analysis_goals": ["viewshed"]},
     {"cue": "now", "ir": "now", "tool": "now", "result_attach": "partial"}),
    ("s27", "看看两点之间的通视情况", "analysis",
     {"analysis_goals": ["viewshed", "line_of_sight"]},
     {"cue": "now", "ir": "now", "tool": "g3d03（LOS 算法待补）"}),
    ("s28", "提取这条线的高程剖面", "analysis",
     {"analysis_goals": ["terrain_profile"]},
     {"cue": "now", "ir": "now", "tool": "now（extract_contours 相邻）",
      "profile_algo": "g3d03"}),
    ("s29", "评估天际线影响", "analysis",
     {"analysis_goals": ["skyline"]},
     {"cue": "now", "ir": "now", "tool": "g3d03（skyline 算法待补）"}),
    # ── 复合意图（多通道同话语） ────────────────────────────────────
    ("s30", "成都小学分布加立体地形，从西南方向看，再算这个山头的可视域，最后导出 PNG",
     "composite",
     {"dimension": "3d", "terrain_requested": True, "camera_bearing": 45.0,
      "analysis_goals": ["viewshed"], "medium": "export_png"},
     {"cue": "now", "ir": "now", "decision": "now", "tool": "now",
      "export_parity": "g3d02/g3d01"}),
    ("s31", "卫星底图上做建筑白模并夸张地形 2 倍", "composite",
     {"extrusion_requested": True, "terrain_requested": True, "exaggeration": 2.0},
     {"cue": "now", "ir": "now", "decision": "now",
      "basemap": "g3d01", "scene_asset": "g3d02"}),
    ("s32", "3D 显示受体分布，DEM 作为高程底面", "composite",
     {"dimension": "3d", "terrain_requested": True},
     {"cue": "now", "ir": "now", "decision": "now", "dataset_roles": "now",
      "dem_pipeline": "g3d01"}),
]


def intent_scenarios() -> List[Dict[str, Any]]:
    """语料展开（dict 视图；测试与缺口表生成共用，顺序稳定）。"""
    return [
        {
            "id": sid,
            "utterance": utterance,
            "family": family,
            "expected_cues": cues,
            "chain": chain,
        }
        for sid, utterance, family, cues, chain in _SCENARIOS
    ]


def gap_summary() -> Dict[str, List[str]]:
    """断链缺口汇总：按链路环节聚合未闭环场景 id（`planning/.../INTENT_SCENARIOS.md` 数据源）。"""
    stages = ("cue", "ir", "decision", "mutation", "tool", "render",
              "terrain_source", "height_evidence", "scene_asset", "algorithm",
              "result_attach", "export_parity", "basemap", "dem_pipeline",
              "profile_algo")
    gaps: Dict[str, List[str]] = {}
    for entry in intent_scenarios():
        for stage, status in entry["chain"].items():
            if stage in stages and status not in ("now",) \
                    and "now" not in str(status):
                gaps.setdefault(stage, []).append(entry["id"])
    return gaps
