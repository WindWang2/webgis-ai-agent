# Plan

1. Recheck #1989 leftovers on `99f6d1e89`. Skip anything already fixed or not reproduced.
2. Add a pure geography verdict. Reject only proved contradictions (name mismatch, or disjoint lon/lat boxes when names are absent). Warn when geography cannot be proved.
3. Call `solve_layout_v5` from the vector publication path and the cartographer print path only when the page profile is one the solver already knows. Leave the default document untouched.
4. Tell the browser export tool that it did not evaluate constraint layout. Do not call that export a publication success.
5. Lock the behavior with unit tests. Independent review reads the diff. Fix P0/P1 from that review. Push a PR. Do not merge.

Out of scope: a new solver, a MapSpec version bump, an SSE reconnect patch, a Playwright claim, a performance rewrite.
