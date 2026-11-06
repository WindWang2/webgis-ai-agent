# PR overlap

Checked 2026-11-06 against `origin/master` `99f6d1e89`.

| Area | Open work | This branch |
|---|---|---|
| `requirements*.txt`, frontend lockfiles | dependabot #1978–#1980, #1982–#1986 | not touched |
| `vendor/pi` | none | not touched |
| MapSpec schema version | none | no version bump; additive `page_profile` on the vector PDF request only |
| #1989 usability locks | merged | not reimplemented; name-token tests still pass |
| Publication solver | none open | existing `solve_layout_v5` called only for an explicit solver profile |

No open issue overlaps this change. Master stays read-only.
