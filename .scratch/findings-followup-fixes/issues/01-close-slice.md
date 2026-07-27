# 01 — Close the findings-followup slice

**What to build:** Land the verified set of four audit follow-up fixes (state management, CSS
typing, key stability) plus the two behavior tests that lock them, then close the slice. After
this ticket, the branch is in a committable, demoable state and the spec is fully delivered.

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] Production fixes present in the working tree (verified `tsc --noEmit` clean):
  - [ ] Reasoning panel expansion state resets on a new result object and survives same-object
        re-renders (no `useEffect`; render-time prop-change pattern; `defaultExpandedSteps` helper)
  - [ ] Layer opacity slider uses standard `appearance: 'none'` (no `as any`, no vendor prefixes)
  - [ ] Suggested-prompts keys are data-derived (not an index with a prefix)
  - [ ] Both message lists in the chat tab use one consistent key-fallback convention
- [ ] Behavior tests present and green (`vitest run`, 275 passed / 3 skipped):
  - [ ] `reasoning-panel.test.tsx` — reset-on-new-object (fails against master; locks the bug) and
        preserve-on-same-object
  - [ ] `suggested-prompts.test.tsx` — no duplicate-key React warning
- [ ] Commit the slice on `fix/remaining-type-safety-and-state` with a message that reflects this
      being a review correction of the prior "state management + CSS typing + key stability" batch
      (e.g. `fix(frontend): correct key/appearance/reasoning-state from review`)
