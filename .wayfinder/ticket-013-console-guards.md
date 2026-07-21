# Ticket: Guard console statements by NODE_ENV in frontend

**Label**: `wayfinder:task` | **Type**: AFK

## Question

25+ `console.error` and `console.warn` statements exist in production frontend code, potentially leaking internal state. Should we wrap all console calls in `if (process.env.NODE_ENV === 'development')` guards, or use a structured logging utility?

## Context

- Files: `story/page.tsx`, `assets-tab.tsx`, `map-studio-tab.tsx`, `task-progress.tsx`, `chart-renderer.tsx`, `map-action-handler.tsx`, `map-panel.tsx`, `settings-panel.tsx`, `code-block.tsx`, `layersSlice.ts`, `use-sse-stream.ts`, `useMapBridge.ts`, `use-workspace-session.ts`
- Severity: P2-medium
