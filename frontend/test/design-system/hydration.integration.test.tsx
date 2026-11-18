import React from 'react';
import { expect, it, vi } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import { renderToString } from 'react-dom/server';
import { ClientProviders } from '@/components/providers/client-providers';
import { useHudStore, disableHudPersistWrites, enableHudPersistWrites, PERSIST_KEY } from '@/lib/store/useHudStore';

it('keeps SSR/first-client defaults, preserves pre-mount storage, then hydrates through the real provider', async () => {
  // Global setup installs no-op storage spies; use jsdom's actual Storage
  // implementation for this persistence integration test.
  vi.stubGlobal('localStorage', window.sessionStorage);
  const defaults = useHudStore.getInitialState();
  const initial = `${defaults.theme}:${defaults.baseLayer}`;
  const savedTheme = defaults.theme === 'dark' ? 'light' : 'dark';
  const seed = JSON.stringify({ state: { theme: savedTheme, baseLayer: 'ESRI 影像' }, version: 0 });
  const prior = localStorage.getItem(PERSIST_KEY);
  disableHudPersistWrites();
  localStorage.setItem(PERSIST_KEY, seed);
  const renders: string[] = [];
  function Probe() {
    const theme = useHudStore((state) => state.theme);
    const layer = useHudStore((state) => state.baseLayer);
    renders.push(`${theme}:${layer}`);
    return <output data-testid="hydrated-values">{theme}:{layer}</output>;
  }
  try {
    expect(useHudStore.persist.hasHydrated()).toBe(false);
    expect(renderToString(<ClientProviders><Probe /></ClientProviders>)).toContain(defaults.baseLayer);
    expect(useHudStore.persist.hasHydrated()).toBe(false);
    useHudStore.setState({ leftPanelOpen: !defaults.leftPanelOpen });
    expect(localStorage.getItem(PERSIST_KEY)).toBe(seed);
    render(<ClientProviders><Probe /></ClientProviders>);
    await waitFor(() => expect(useHudStore.persist.hasHydrated()).toBe(true));
    expect(renders[0]).toBe(initial);
    expect(renders[1]).toBe(initial);
    expect(screen.getByTestId('hydrated-values')).toHaveTextContent(`${savedTheme}:ESRI 影像`);
    act(() => useHudStore.setState({ baseLayer: 'Carto 浅色' }));
    expect(JSON.parse(localStorage.getItem(PERSIST_KEY)!).state.baseLayer).toBe('Carto 浅色');
  } finally {
    enableHudPersistWrites();
    if (prior === null) localStorage.removeItem(PERSIST_KEY); else localStorage.setItem(PERSIST_KEY, prior);
    vi.unstubAllGlobals();
  }
});
