# 01 — Trap keyboard focus inside the history drawer while open

**What to build:** When the history drawer modal is open, a keyboard user should not be able to
Tab out of it into the background page. Today the drawer has full dialog semantics
(`role="dialog"`, `aria-modal`, `aria-labelledby`), closes on Escape, moves focus into the panel
on open, and restores focus to the previously-focused element on close — but Tab and Shift+Tab
freely leave the drawer. Close that gap so the drawer satisfies the keyboard part of the dialog
pattern.

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] Tab from the drawer's last focusable element wraps focus back to its first focusable
      element (focus stays inside the dialog)
- [ ] Shift+Tab from the first focusable element wraps to the last (reverse cycle)
- [ ] Existing behaviour preserved: Escape still closes; focus still moves into the panel on open
      and restores to the trigger on close; backdrop click still closes
- [ ] No new dependency introduced — hand-roll the cycle in the existing `useEffect` keydown
      handler (query the panel's focusable elements and wrap on Tab/Shift+Tab), consistent with
      the existing hand-rolled Escape handling
- [ ] Behavior test added (new `history-drawer.test.tsx`) using `@testing-library/user-event`
      (already a dev dependency): open the drawer, Tab through, assert focus never lands on a
      known background element outside the panel. Prior art for a11y testing in this repo:
      `components/map/baselayer-switcher.test.tsx`
- [ ] `npm run typecheck`, `npm run lint`, `npm run test` all green
