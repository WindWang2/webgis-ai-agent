/**
 * StacExplorer 加载态守卫：session 域缺少 sessionId 时必须在早退分支解除
 * loading（否则在途请求被 abort+seq++ 后 finally 不再复位 → 常驻 spinner）。
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor, act } from '@testing-library/react';

const api = vi.hoisted(() => ({ searchCatalogStac: vi.fn() }));
vi.mock('@/lib/api/lakehouse', () => ({ lakehouseApi: api }));
vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: (selector: (s: { addLayer: () => void }) => unknown) =>
    selector({ addLayer: () => {} }),
}));
vi.mock('@/components/ui/toast', () => ({
  useToastStore: (selector: (s: { addToast: () => void }) => unknown) =>
    selector({ addToast: () => {} }),
}));

import { StacExplorer } from './stac-explorer';

function deferred<T = unknown>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => { resolve = r; });
  return { promise, resolve };
}

describe('StacExplorer loading guard', () => {
  it('在途检索时 sessionId 消失：loading 解除，不留下常驻 spinner', async () => {
    const d = deferred();
    api.searchCatalogStac.mockImplementationOnce(() => d.promise);
    const view = render(
      <StacExplorer ownerType="session" ownerId="s1" sessionId="sess-1" />,
    );
    await waitFor(() => expect(api.searchCatalogStac).toHaveBeenCalledTimes(1));
    expect(screen.getByRole('status')).toBeInTheDocument();

    // session 域缺少 sessionId → load() 早退分支
    view.rerender(
      <StacExplorer ownerType="session" ownerId="s1" sessionId="" />,
    );
    await waitFor(() => expect(screen.queryByRole('status')).toBeNull());
    await act(async () => {
      d.resolve({ collection: { id: 'c', links: [] }, items: [] });
      await Promise.resolve();
    });
  });
});
