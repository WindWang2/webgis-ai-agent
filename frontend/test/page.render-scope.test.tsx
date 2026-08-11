import { render, waitFor } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import Home from '@/app/page';

/**
 * D-F8 — page-level `messages` state re-renders the whole app (P1).
 *
 * `messages` lives in Home (app/page.tsx) via useSSEStream, so every token
 * batch re-renders Home → TopBar, MapPanel, EmbodiedHud, SpatialCrosshair,
 * LeftSidebar, FloatingLegend all re-render even though nothing they depend
 * on changed.
 *
 * This test renders the REAL Home with mocked hooks + mocked leaf components
 * (render counters), then simulates N token batches exactly the way
 * use-sse-stream updates state (new array; only the streaming message object
 * replaced). Assertions:
 *
 *   - BEFORE: every sibling re-renders N times (render count == N) → FAIL.
 *   - AFTER (memoized siblings): TopBar/MapPanel/EmbodiedHud/SpatialCrosshair
 *     never re-render during streaming (count == 0); LeftSidebar still
 *     re-renders per batch (it displays the messages) → PASS.
 */

const counters = vi.hoisted(() => ({
  topBar: 0,
  mapPanel: 0,
  sidebar: 0,
  hud: 0,
  crosshair: 0,
}));

// Mocked hooks + stable values. `bridge` is a stable object reference so
// MapPanel's onViewportChange prop is stable across renders (as in the real
// hook, where useMapBridge memoizes the returned object).
const stream = vi.hoisted(() => {
  const noop = () => {};
  return {
    messages: [] as Array<{
      id: string;
      role: 'user' | 'assistant';
      content: string;
      timestamp: Date | number | null;
    }>,
    // Stable references — mirrors the real hooks, whose useCallback/memo
    // guarantees keep these identities across renders (required for the
    // memoized-sibling behavior to hold).
    bridge: { onViewportChange: noop },
    setMessages: noop,
    handleSend: noop,
    handlePlanAction: noop,
  };
});

vi.mock('@/lib/hooks/use-sse-stream', () => ({
  useSSEStream: () => ({
    messages: stream.messages,
    setMessages: stream.setMessages,
    aiStatus: 'idle' as const,
    isLoading: false,
    handleSend: stream.handleSend,
    handlePlanAction: stream.handlePlanAction,
    bridge: stream.bridge,
  }),
}));

vi.mock('@/lib/hooks/use-workspace-session', () => ({
  useWorkspaceSession: () => ({
    sessionId: 's1',
    setSessionId: stream.setMessages,
    sessionIdRef: { current: 's1' },
    sessionTokenRef: { current: null },
    sessions: [],
    selectSession: stream.handleSend,
    startNewSession: stream.handleSend,
  }),
}));

vi.mock('@/lib/hooks/use-geolocation', () => ({
  useGeolocation: () => ({ location: null }),
}));

vi.mock('@/lib/contexts/map-action-context', () => ({
  useMapAction: () => ({ getMapSnapshot: () => null, dispatchAction: () => {} }),
}));

const hud = vi.hoisted(() => ({
  state: {
    layers: [],
    removeLayer: () => {},
    toggleLayer: () => {},
    leftPanelOpen: true,
    settingsOpen: false,
    historyOpen: false,
    setHistoryOpen: () => {},
    hudOpen: true,
    ragPanelOpen: false,
    setRagPanelOpen: () => {},
    sidebarWidth: 360,
    theme: 'light',
    accentColor: '#16a34a',
    fontSize: 14,
  },
}));

vi.mock('@/lib/store/useHudStore', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/store/useHudStore')>();
  return {
    ...actual,
    default: (selector: (s: typeof hud.state) => unknown) => selector(hud.state),
  };
});

vi.mock('@/components/layout/top-bar', () => ({
  default: () => {
    counters.topBar += 1;
    return <div data-testid="topbar" />;
  },
}));

vi.mock('@/components/map/map-panel', () => ({
  MapPanel: () => {
    counters.mapPanel += 1;
    return <div data-testid="mappanel" />;
  },
}));

vi.mock('@/components/sidebar/left-sidebar', () => ({
  LeftSidebar: () => {
    counters.sidebar += 1;
    return <div data-testid="sidebar" />;
  },
}));

vi.mock('@/components/hud/embodied-hud', () => ({
  EmbodiedHud: () => {
    counters.hud += 1;
    return <div data-testid="hud" />;
  },
}));

vi.mock('@/components/map/spatial-crosshair', () => ({
  SpatialCrosshair: () => {
    counters.crosshair += 1;
    return <div data-testid="crosshair" />;
  },
}));

vi.mock('@/components/map/map-error-boundary', () => ({
  MapErrorBoundary: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

vi.mock('@/components/map/floating-legend', () => ({
  default: () => <div data-testid="legend" />,
}));

vi.mock('@/components/tweaks-panel', () => ({
  default: () => <div data-testid="tweaks" />,
}));

vi.mock('@/components/panel/rag-independent-panel', () => ({ default: () => null }));
vi.mock('@/components/drawers/history-drawer', () => ({ HistoryDrawer: () => null }));
vi.mock('@/components/settings/settings-panel', () => ({ SettingsPanel: () => null }));
vi.mock('@/components/map/export-mask', () => ({ ExportMask: () => null }));

const mkMsg = (id: string, content: string) => ({
  id,
  role: 'assistant' as const,
  content,
  timestamp: new Date(),
});

describe('page render scope (D-F8)', () => {
  it('memoized siblings do not re-render per token batch', async () => {
    stream.messages = [
      { id: 'u1', role: 'user' as const, content: '分析北京市学校分布', timestamp: new Date() },
      mkMsg('a1', '**第一**段回答'),
      mkMsg('a2', ''),
    ];

    const { rerender } = render(<Home />);

    // Settle: dynamic MapPanel import resolves and mounts.
    await waitFor(() => expect(counters.mapPanel).toBe(1));

    // Initial mount rendered every sibling exactly once.
    expect(counters.topBar).toBe(1);
    expect(counters.sidebar).toBe(1);
    expect(counters.hud).toBe(1);
    expect(counters.crosshair).toBe(1);

    // Reset, then simulate N token batches (only the streaming message object
    // is replaced, matching use-sse-stream's setMessages updater).
    (Object.keys(counters) as Array<keyof typeof counters>).forEach((k) => {
      counters[k] = 0;
    });

    const N = 20;
    for (let i = 1; i <= N; i++) {
      stream.messages = stream.messages.map((m) =>
        m.id === 'a2' ? { ...m, content: `${m.content} token-${i}` } : m
      );
      rerender(<Home />);
    }

    console.log(
      `[D-F8 page] N=${N} batches: topBar=${counters.topBar}, mapPanel=${counters.mapPanel}, ` +
        `hud=${counters.hud}, crosshair=${counters.crosshair}, sidebar=${counters.sidebar}`
    );

    // Siblings with stable props must not re-render during token streaming.
    expect(counters.topBar).toBe(0);
    expect(counters.mapPanel).toBe(0);
    expect(counters.hud).toBe(0);
    expect(counters.crosshair).toBe(0);
    // LeftSidebar receives the new messages array each batch — re-rendering
    // per batch is required (control).
    expect(counters.sidebar).toBe(N);
  });
});
