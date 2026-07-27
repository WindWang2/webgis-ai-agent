# Spec: Close out findings.md audit findings (state, typing, key stability)

**Source:** Synthesizes the `/code-review` of branch `fix/remaining-type-safety-and-state`
(against `master`) and the originating `findings.md` audit.
**Status:** `ready-for-agent` — fixes implemented and verified in the working tree; this spec
formalizes the work and drives backfilled behavior tests.

---

## Problem Statement

A line-by-line audit of `frontend/components/**/*.tsx` (`findings.md`) flagged a handful of
type-safety, state-management, and React-key findings. An earlier commit on this branch
attempted to close four of them, but a `/code-review` (Standards + Spec axes) found that the
fixes were partial or sidestepped:

- The reasoning-panel state reset used a `useEffect` that resets expansion on **every** result
  change — clobbering the user's own expand/collapse toggles — and its `[result]` dependency
  fires on parent-object identity, so in-place `reasoning_chain` mutation is still missed.
- The slider styling dropped the standard `appearance` property for two vendor prefixes, on a
  false premise that `appearance` is absent from `CSSProperties`.
- The prompt and message-list keys became index keys with cosmetic prefixes, which read as stable
  IDs but are positional.

From the user's perspective: the explorer panel forgets which reasoning steps were expanded
whenever a new analysis loads (or worse, resets mid-session), and the key changes give a false
sense of stability that would break silently if the prompt or message lists ever became dynamic.

## Solution

Re-apply the four fixes correctly, with the shape the audit actually asked for, and lock the
behavior that matters behind component tests.

- **Reasoning panel state** — derive the default expansion from the result, and reset it **only**
  when a genuinely new `result` object arrives — not on every parent re-render of the same object.
  User toggles on the current result must survive.
- **Slider styling** — use the standard, unprefixed `appearance: 'none'` (no `as any`; it is valid
  in `CSSProperties`), dropping the vendor-prefix pair.
- **Suggested-prompts keys** — keep stable, data-derived keys for a static list.
- **Message-list keys** — apply one consistent fallback convention across both message lists in
  the chat tab.

## User Stories

1. As a spatial analyst, I want the explorer's reasoning panel to default-expand the first step
   when a **new** analysis result loads, so that I immediately see where the reasoning starts.
2. As a spatial analyst, I want my own expand/collapse choices to **persist** while I read the
   current result, so that the panel doesn't reset out from under me when the parent re-renders.
3. As a spatial analyst, I want my expand/collapse choices to reset cleanly when I open a
   **different** analysis, so that I'm not left looking at stale expansion state for a result I
   no longer have.
4. As a user, I want the layer opacity slider to render consistently across browsers, so that the
   control looks and behaves the same regardless of engine.
5. As a developer, I want the slider's `appearance` setting to use the standard CSS property
   without type-system escapes, so that the code is type-safe and forward-compatible.
6. As a user, I want the suggested-prompt buttons to keep stable identities even if the prompt
   list is later reordered or extended, so that transitions and state stay attached to the right
   prompt.
7. As a user, I want the chat message list to use one consistent keying convention, so that React
   reconciliation is predictable across both the user and assistant message lists.
8. As a developer, I want these four fixes to be covered by behavior tests, so that a regression
   (e.g. re-introducing an always-reset effect, or a key collision) is caught before merge.

## Implementation Decisions

- **Reasoning panel — derived-state pattern over effect.** Replace the `useEffect` reset with the
  React-documented "adjust some state when a prop changes" pattern: hold the previous `result`
  identity in a second `useState`, and during render compare `prevResult !== result`; on a genuine
  identity change, update both. This avoids the extra render pass an effect causes and limits resets
  to actual new objects.
  - Extraction: pull the default-expansion computation into a small helper
    (`defaultExpandedSteps(result) -> Set<number>`) so the lazy initializer and the reset path share
    one definition rather than duplicating the expression.
  - Scope of reset: only the identity change resets to default. In-place mutation of
    `reasoning_chain` on the same object is **out of scope** (see Out of Scope) — the data layer is
    expected to hand the panel a new object when the chain meaningfully changes.
- **Slider styling — standard `appearance`.** A single `appearance: 'none'` in the inline style
  object; remove `MozAppearance` / `WebkitAppearance` and the now-inaccurate explanatory comment.
- **Suggested-prompts keys.** Use the data-derived key for the static `SUGGESTIONS` constant rather
  than an index-with-prefix. (The list is a module-level constant today; if it ever becomes dynamic
  the same convention — a data-derived id — should be applied then.)
- **Message-list keys.** One fallback convention across both message lists in the chat tab:
  primary identifier when present, a consistent prefixed positional fallback otherwise. Apply to
  both the user and assistant message rows so the two lists stop diverging.

## Testing Decisions

What makes a good test here: assert **external behavior**, not implementation. The mechanism by
which the reset happens (effect vs. render-time adjustment) is an implementation detail; the
behavior is "expansion resets when a new result arrives, and survives re-renders of the same
result."

- **Reasoning panel (new test file).** Render the panel with a `result` fixture; expand a
  non-default step; re-render with a **new object** (same content, different identity) and assert
  expansion resets to the default. Then re-render with the **same object reference** and assert the
  user's toggle survives. The two cases together pin the regression the effect introduced. Uses the
  existing `result: SpatialReasoningResult` prop — no store mock needed.
  - Prior art for prop-driven component tests: `components/chat/cartography-result-card.test.tsx`.
- **Suggested-prompts (extend existing test file).** Assert that rendering the component produces
  no React `key` warning (i.e. keys are unique), guarding against a future regression to a
  colliding-key scheme. Prior art: the existing `suggested-prompts.test.tsx` already renders the
  component with a `framer-motion` mock; the new assertion extends that harness.
- **Layers-tab and chat-tab fixes** are not given dedicated behavior tests. The slider change is a
  CSS-typing fix (the slider behaved correctly before and after; `tsc --noEmit` enforces the
  typing), and the chat-tab change is internal key stability with no user-observable behavior.
  These are covered by the existing `tsc --noEmit` + `vitest` suite, not by new tests. This is a
  deliberate, honest scope call — the spec does not pretend test surface exists where it doesn't.

## Out of Scope

- **In-place `reasoning_chain` mutation on the same `result` object.** The reset is keyed on object
  identity. If the parent mutates the chain array in place without producing a new object, the panel
  will not reset. Treating this is a data-layer concern (producers should hand the panel a new
  object when content changes), not this spec.
- **The remaining ~80 open findings** in `findings.md` (a11y, type safety, security, perf across
  ~91 files). This spec closes only the four re-reviewed here.
- **Making `SUGGESTIONS` or the message list dynamic / remotely sourced.** The key decisions assume
  today's static array and current message production; dynamic-list rework is a separate concern.
- **Renaming or reorganizing** the touched components.

## Further Notes

- The four fixes are already present in the working tree on `fix/remaining-type-safety-and-state`
  and verified: `npm run typecheck` clean, `npm run lint` clean (only pre-existing warnings in
  untouched files), `npm run test` → 272 passed / 3 skipped (pre-existing skips). The committed
  implementation matches the Implementation Decisions above.
- The follow-on work this spec drives is **adding the two behavior tests** described in Testing
  Decisions; the production code itself needs no further change.
- Tracking convention follows the project's local tracker: one issue file per unit under
  `.scratch/<feature>/issues/NN-*.md` with a `ready-for-agent` status. The issue tracker
  precondition (`docs/agents/issue-tracker.md`) is not present, but this convention is already in
  established use.
