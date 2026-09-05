# Workflow 编写指南（Authoring Guide）

如何新增一个专业 GIS Workflow —— 从零到全部测试绿灯。

## 0. 前置事实（只读 registry，不复制）

写 recipe 前先确认要引用的事实存在（manifest 编译期 fatal）：

- capability id：`app/lib/gis/capabilities/`（64 个）
- 制图元素 id：`app/lib/cartography/model_library.py`（19 个 MapModel）
- 组件类型：`app/services/gis_harness/components.py`（18 个 ComponentType）
- artifact 类型：`app/lib/gis/artifacts.py`（21 个）
- 科学前置条件 id：`app/lib/gis/scientific_preconditions.py`（如
  `min_numeric_samples:30` / `projected_crs_required` / `temporal_field_required`）

## 1. 选择归属

- 该工作流是否被某个**既有任务族**覆盖（intent.py 的 TaskType）？
  → 在对应领域包里加 recipe，`intent_tasks` 用既有任务族。
- 是否是全新的专业语义（现有任务族都无法表达）？
  → 先在 `intent.py` 加任务族 + 确定性规则（双语词表、置于特异性排序的
  正确位置），再写 recipe。参考 `terrain_analysis` / `sar_analysis`。
- 通用产品族（「分布情况」级别的宽语义）永远属于 V1 seed，不要用领域包
  侵蚀 —— seed 资历守卫会保证 seed 优先。

## 2. 编写 recipe（app/services/gis_harness/recipe_packs/<domain>.py）

```python
CartographyRecipe(
    id="my_domain_my_workflow",            # 全局唯一
    name="我的专业工作流",
    description="一段话说清语义与科学边界。",
    intent_tasks=["my_task"],              # 既有或新任务族
    intent_cartography=["raster_surface"], # ⊆ CartographyIntent 词表
    preferred_analysis=["raster_source", "my_capability"],
    optional_analysis=["zonal_statistics"],
    primary_cartography="raster_surface",
    secondary_cartography=[],
    default_components=MAP_COMPONENTS_CONTINUOUS,
    fallbacks=[],                          # 制图元素级（V1 语义）
    export_profile={"formats": ["png"]},
    priority=44,
    schema_version=2,                      # 带 workflow 必须为 2
    workflow=wf(
        "my_domain", "my_family",
        zh=["专业词一", "专业词二"],         # 路由关键词：必须特异！
        en=["professional term"],
        roles=[elevation_role(), boundary_role(required=False)],
        obligations=[
            obl("my_crs_guard", "precondition",
                precondition="local_metric_crs_required",
                code="MY_METRIC_CRS_REQUIRED",
                desc="米制 CRS 义务。", action="degrade_with_disclosure"),
        ],
        completion=standard_completion(uncertainty=True),
        fallbacks=[fb("MY_METRIC_CRS_REQUIRED", frm="my_product", to="raw_view",
                      downgrade="degraded", disclosure="角度坐标下仅呈现原始数据。")],
        evidence=["my_calibration_evidence"],
    ),
)
```

关键词纪律（专业路由的根基）：

- 关键词必须是**专业动作/语义词**（坡度分析/流域划分/莫兰指数），不能是
  主体词（学校/加油站）或泛动词（show me / 看看）—— 否则会劫持通用查询
  （corpus 回归会抓住）；
- 每族至少一个 zh 一个 en 词，与 intent 规则词表对齐。

## 3. 接入一致性语料（app/evaluation/conformance.py）

加一个 `ConformanceFamily`（人工审定的语义期望表）：

```python
ConformanceFamily(
    "my-domain-workflow", "my_domain", "我的工作流",
    ("专业短语一", "专业短语二"),
    ("professional phrase",),
    "my_task", "my_domain_my_workflow",
    ("my_capability",),
    group="conformance-<domain>",
)
```

该族的**所有**表述变体（3 短语 × 5 scope × 4 句式 = 60 案例）都必须解析到
同一 task/recipe —— 口语展示句式如诚实落到相邻任务族，用
`alternative_tasks=` / `alternative_recipes=` 显式声明（审定过的宽松，
不是失败）。

## 4. 契约案例（可选但推荐，app/evaluation/anti_claim.py）

数据画像驱动的义务/降级/裁决断言：`WorkflowContractCase`。

## 5. 回归与收尾

```bash
pytest tests/unit/gis_harness/ tests/cartography/ -q          # 全量回归
pytest tests/unit/gis_harness/test_recipe_packs.py -q          # registry parity
python scripts/gen_workflow_catalog.py                         # 重新生成 catalog
```

/catalog 与 registry 不一致时 `--check` 会失败（可挂 CI）。
