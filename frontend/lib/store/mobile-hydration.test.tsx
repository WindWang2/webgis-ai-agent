import { act } from '@testing-library/react';
import { hydrateRoot, type Root } from 'react-dom/client';
import { renderToString } from 'react-dom/server';
import { create, type StateCreator } from 'zustand';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { HudState } from './hud-types';
import { createUiSlice } from './slices/uiSlice';
import { useLayoutMode } from '@/lib/hooks/use-layout-mode';
import { mapInsetLeftStyle } from '@/lib/utils/workspace-inset';

function newUiStore() {
  return create<HudState>()(createUiSlice as StateCreator<HudState>);
}

describe('mobile workspace hydration', () => {
  let root: Root | undefined;
  afterEach(async () => {
    if (root) await act(() => root?.unmount());
    root = undefined;
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    document.body.innerHTML = '';
  });

  it('uses identical server/client initial state and replaces desktop inset after hydration', async () => {
    const browserWindow = window;
    vi.stubGlobal('window', undefined);
    const serverStore = newUiStore();
    function Workspace({ store }: { store: ReturnType<typeof newUiStore> }) {
      const open = store(s => s.leftPanelOpen);
      const mode = useLayoutMode();
      return <div data-mode={mode} style={{ left: mapInsetLeftStyle(mode === 'desktop' && open, 330) }} />;
    }
    const html = renderToString(<Workspace store={serverStore} />);
    vi.stubGlobal('window', browserWindow);
    vi.stubGlobal('innerWidth', 390);
    vi.stubGlobal('matchMedia', vi.fn((query: string) => ({
      matches: query === '(max-width: 768px)', media: query,
      addEventListener: vi.fn(), removeEventListener: vi.fn(),
    })));
    const clientStore = newUiStore();
    expect(clientStore.getInitialState().leftPanelOpen).toBe(serverStore.getInitialState().leftPanelOpen);
    const container = document.createElement('div');
    container.innerHTML = html;
    document.body.append(container);
    const error = vi.spyOn(console, 'error').mockImplementation(() => {});
    await act(() => { root = hydrateRoot(container, <Workspace store={clientStore} />); });
    const canvas = container.firstElementChild as HTMLElement;
    expect(error).not.toHaveBeenCalled();
    expect(canvas.dataset.mode).toBe('mobile');
    expect(canvas.style.left).toBe('0px');
    // Later panel open/close operations must never reintroduce desktop inset.
    await act(() => clientStore.setState({ leftPanelOpen: false }));
    await act(() => clientStore.setState({ leftPanelOpen: true }));
    expect(canvas.style.left).toBe('0px');
  });
});
