/**
 * #610 regression: the asset download button must route through the
 * authenticated transport (downloadWithAuth) with the backend-required
 * session_id — a bare window.open could carry neither Bearer nor session_id
 * (恒 422), and `download=true` was a phantom param the backend ignores.
 */
import { describe, expect, it, vi, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { AssetCard } from './asset-card';

const downloadWithAuthMock = vi.fn().mockResolvedValue(undefined);

vi.mock('@/lib/api/authenticated-download', () => ({
  downloadWithAuth: (...args: unknown[]) => downloadWithAuthMock(...args),
}));

afterEach(() => {
  vi.clearAllMocks();
});

function renderCard(props: Partial<React.ComponentProps<typeof AssetCard>> = {}) {
  return render(
    <AssetCard
      asset={{ id: 42, original_name: 'analysis.geojson', format: 'geojson' }}
      onLoad={() => undefined}
      onDelete={() => undefined}
      onRename={() => undefined}
      {...props}
    />,
  );
}

describe('AssetCard download', () => {
  it('downloads through downloadWithAuth with session_id and owner token (#610)', () => {
    renderCard({ sessionId: 'sess-1', ownerToken: 'owner-1' });

    fireEvent.click(screen.getByLabelText('下载资产'));

    expect(downloadWithAuthMock).toHaveBeenCalledTimes(1);
    const [url, options] = downloadWithAuthMock.mock.calls[0] as [string, Record<string, unknown>];
    expect(url).toContain('/api/v1/layers/data/42');
    expect(url).toContain('session_id=sess-1');
    expect(url).not.toContain('download=true');
    expect(options.ownerToken).toBe('owner-1');
  });

  it('prefers asset.ref_id when present and omits session_id when absent', () => {
    renderCard({ asset: { id: 9, ref_id: 'ref:abc-123', original_name: 'x.geojson' } });

    fireEvent.click(screen.getByLabelText('下载资产'));

    const [url] = downloadWithAuthMock.mock.calls[0] as [string];
    expect(url).toContain('/api/v1/layers/data/ref%3Aabc-123');
    expect(url).not.toContain('session_id=');
  });
});