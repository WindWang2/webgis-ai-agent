# Independent review R2

R2 re-read the patched files. The four R1 items were confirmed fixed. The default publication document is still the same object when `page_profile` is absent. Unevaluated chrome keeps its historical coordinates.

R2 then found two new P1s. Both are fixed and locked:

| P1 | Fix | Test |
|---|---|---|
| A subtitle move was applied to the whole title block, so a top-center title followed the subtitle. | Title and subtitle coordinates are independent. The historical combined block is unchanged when neither moved. | `test_subtitle_move_does_not_drag_the_title` |
| A legend solved to a bottom anchor used that y as the top of a box that then grew off the page. | The moved legend box is shifted up by its height and clamped inside the margin. | `test_moved_legend_box_stays_on_the_page` |

No remaining P0/P1 from R2 after those fixes.

Residual, not treated as a defect to patch further:

- Browser raster export still does not run `solve_layout_v5`. The tool result says so. It is not a publication success.
- Playwright, a live Pi extension journey, and a performance before/after were not run.
- SSE reconnect was not reproduced.
