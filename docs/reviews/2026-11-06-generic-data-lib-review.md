# Generic data library findings

2026-11-06. Exact assigned subset of #1864 S5-003/004/005: 83 original diagnostics in 36 files. No rule/baseline increase. Original line numbers below refer to the captured lint inventory.

| Path:original line | Rule | State | Review / implementation |
|---|---|---|---|
| `app/lib/geo_analysis/_vector.py:31` | S110 | fixed | Reviewed fallback/teardown path; add an explicit diagnostic or typed failure rather than silent catch. |
| `app/lib/geo_analysis/aggregation.py:78` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/geo_analysis/aggregation.py:597` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/geo_analysis/cokriging_lmc.py:519` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/geo_analysis/dasymetric.py:225` | B007 | fixed | Unused loop target renamed only at this binding; remaining uses precede this loop. |
| `app/lib/geo_analysis/dasymetric.py:332` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/geo_analysis/density.py:153` | S110 | fixed | Reviewed fallback/teardown path; add an explicit diagnostic or typed failure rather than silent catch. |
| `app/lib/geo_analysis/density.py:412` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/geo_analysis/density.py:425` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/geo_analysis/density.py:572` | B007 | fixed | Unused loop target renamed only at this binding; remaining uses precede this loop. |
| `app/lib/geo_analysis/density.py:572` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/geo_analysis/density.py:628` | B007 | fixed | Unused loop target renamed only at this binding; remaining uses precede this loop. |
| `app/lib/geo_analysis/density.py:628` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/geo_analysis/geometry_ops.py:48` | S110 | fixed | Reviewed fallback/teardown path; add an explicit diagnostic or typed failure rather than silent catch. |
| `app/lib/geo_analysis/geometry_ops.py:158` | B007 | fixed | Unused loop target renamed only at this binding; remaining uses precede this loop. |
| `app/lib/geo_analysis/geometry_ops.py:158` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/geo_analysis/heatmap_grid.py:100` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/geo_analysis/interpolation.py:147` | S110 | fixed | Reviewed fallback/teardown path; add an explicit diagnostic or typed failure rather than silent catch. |
| `app/lib/geo_analysis/interpolation.py:264` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/geo_analysis/interpolation.py:265` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/geo_analysis/interpolation.py:585` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/geo_analysis/interpolation.py:783` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/geo_analysis/kriging.py:1555` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/geo_analysis/kriging.py:2905` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/geo_analysis/kriging.py:2906` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/geo_analysis/kriging.py:2916` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/geo_analysis/kriging.py:2917` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/geo_analysis/kriging.py:3123` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/geo_analysis/kriging.py:3385` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/geo_analysis/kriging.py:3466` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/geo_analysis/kriging_simulation.py:662` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/geo_analysis/kriging_st.py:528` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/geo_analysis/network.py:71` | B905 | fixed | Adjacent endpoints intentionally have unequal lengths; explicit strict=False. |
| `app/lib/geo_analysis/network.py:167` | B007 | fixed | Unused loop target renamed only at this binding; remaining uses precede this loop. |
| `app/lib/geo_analysis/raster_grid.py:66` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/geo_analysis/raster_grid.py:509` | S110 | fixed | Reviewed fallback/teardown path; add an explicit diagnostic or typed failure rather than silent catch. |
| `app/lib/geo_analysis/raster_math.py:172` | S110 | fixed | Reviewed fallback/teardown path; add an explicit diagnostic or typed failure rather than silent catch. |
| `app/lib/geo_analysis/raster_math.py:684` | S110 | fixed | Reviewed fallback/teardown path; add an explicit diagnostic or typed failure rather than silent catch. |
| `app/lib/geo_analysis/rbf_interpolation.py:365` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/geo_analysis/regression_kriging.py:178` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/geo_analysis/regression_kriging.py:179` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/geo_analysis/regression_kriging.py:541` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/geo_analysis/rs_cube_descriptor.py:443` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/geo_analysis/rs_fusion.py:75` | B007 | fixed | Unused loop target renamed only at this binding; remaining uses precede this loop. |
| `app/lib/geo_analysis/rs_fusion.py:78` | B007 | fixed | Unused loop target renamed only at this binding; remaining uses precede this loop. |
| `app/lib/geo_analysis/rs_v3.py:1177` | B007 | fixed | Unused loop target renamed only at this binding; remaining uses precede this loop. |
| `app/lib/geo_analysis/statistics.py:1368` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/geo_analysis/statistics.py:3617` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/geo_analysis/tasseled_cap.py:160` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/geo_analysis/tasseled_cap.py:170` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/geo_analysis/tasseled_cap.py:196` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/geo_analysis/terrain.py:665` | B007 | fixed | Unused loop target renamed only at this binding; remaining uses precede this loop. |
| `app/lib/geo_analysis/terrain.py:738` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/geo_analysis/terrain.py:870` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/geo_analysis/terrain.py:880` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/geo_analysis/terrain.py:1459` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/geo_analysis/terrain.py:1728` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/geo_analysis/terrain.py:1873` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/geo_analysis/terrain.py:2042` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/geo_analysis/terrain.py:2985` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/geo_analysis/terrain.py:3111` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/geo_analysis/tin_interpolation.py:377` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/geo_analysis/tin_interpolation.py:630` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/geo_analysis/tin_interpolation.py:730` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/geo_analysis/trend_surface.py:410` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/geo_processor/core.py:276` | S110 | fixed | Reviewed fallback/teardown path; add an explicit diagnostic or typed failure rather than silent catch. |
| `app/lib/geo_raster/reader.py:125` | S110 | fixed | Reviewed fallback/teardown path; add an explicit diagnostic or typed failure rather than silent catch. |
| `app/lib/gis/dataset_profile.py:200` | S110 | fixed | Reviewed fallback/teardown path; add an explicit diagnostic or typed failure rather than silent catch. |
| `app/lib/gis/longitude.py:178` | S110 | fixed | Reviewed fallback/teardown path; add an explicit diagnostic or typed failure rather than silent catch. |
| `app/lib/gis/longitude.py:290` | B905 | fixed | Adjacent endpoints intentionally have unequal lengths; explicit strict=False. |
| `app/lib/gis/method_use_contract.py:107` | S112 | fixed | Reviewed fallback/teardown path; add an explicit diagnostic or typed failure rather than silent catch. |
| `app/lib/gis/pattern_projection.py:168` | B007 | fixed | Unused loop target renamed only at this binding; remaining uses precede this loop. |
| `app/lib/modelops/evaluation.py:191` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/modelops/multimodal.py:152` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/modelops/promptable.py:57` | B007 | fixed | Use box origins in finite-coordinate validation instead of suppressing the variables. |
| `app/lib/modelops/promptable.py:57` | B007 | fixed | Use box origins in finite-coordinate validation instead of suppressing the variables. |
| `app/lib/modelops/stitching.py:80` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/modelops/stitching.py:151` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/modelops/stitching.py:198` | B007 | fixed | Unused loop target renamed only at this binding; remaining uses precede this loop. |
| `app/lib/modelops/stitching.py:253` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/modelops/stitching.py:356` | B905 | fixed | Aligned numerical/index/result sequences must have equal length; strict=True rejects truncation. |
| `app/lib/modelops/vectorize.py:111` | S110 | fixed | Reviewed fallback/teardown path; add an explicit diagnostic or typed failure rather than silent catch. |
| `app/lib/tool_cache.py:316` | S110 | fixed | Reviewed fallback/teardown path; add an explicit diagnostic or typed failure rather than silent catch. |

Verification: selected S110/S112/B905/B007/B018 inventory is empty; normal Ruff on all assigned paths and compileall pass. Negative behavior + longitude/raster hardening: `/tmp/data-generic141.log`, 41 passed. Expanded lib/modelops/science/tool-cache suite: `/tmp/data-generic-lib140.log`, 3817 passed, 8 skipped, 18 failed (9 unique science oracle expectations run twice); those scientific contract differences remain under review, not represented as passing.

Science oracle follow-up: the 18 failures were 9 stale expectations replayed by two test entry points. Independently recalculated GeoDetector F=SSW1/SSW2 with 50-digit Decimal residual sums and an incomplete-beta F CDF (not the production routine), yielding strong F=0.17961836725924768, p=0.00011369689624946595 and weak F=1.162878331086739, p=0.7205206348947868; both df=23. Removed obsolete t-surrogate expectations. Wrapped dateline midpoint belongs to UTM1; generic projected evidence cannot satisfy local metric policy; exact H3 global count 2+120*7^r and current200k cap yield only res5. Updated the source generator and regenerated manifest through its required script. Full `tests/science_oracles`: `/tmp/data-oracle147.log`, **2298passed**. No scientific production formulas were changed to make the oracle pass.
