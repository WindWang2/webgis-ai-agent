# Independent review R1

Reviewers read the worktree diff. They did not edit files.

## GIS scope

P1: explicit degree CRS values other than a short WGS84 list (`gcj02`, `EPSG:4490`, `CRS:84`, `urn:ogc:def:crs:EPSG::4326`) skipped the bbox check, so a Chengdu point could still be drawn for 北京市.

P2: a feature sample with no CRS and in-range numbers such as `[12, 8]` was treated as lon/lat and could be rejected.

## Publication layout

P1: disclosure caps of 16 were used as the apply set, so key notes past the cap could be disabled.

P1: an evaluated title/subtitle block kept the historical top y, and a subtitle-only move was ignored.

## Export honesty

No P0/P1. Browser export returns `constraint_layout=not_evaluated` outside the frontend command params.

## Fixes applied before R2

- Degree-system CRS aliases are compared. Unknown or projected CRS stays unverified.
- A feature-only sample with no CRS is unverified. A descriptor bbox with no CRS can still reject a disjoint box.
- Apply uses the full `suppressed_ids` and `key_component_ids`. Disclosure lists stay capped.
- Title and subtitle moves are independent. A subtitle move does not drag the title.
- The product summary includes `scope_unverified`, because the LLM slim drops `warnings`.
- The vector PDF message says `not_evaluated` unless an explicit profile was solved.
