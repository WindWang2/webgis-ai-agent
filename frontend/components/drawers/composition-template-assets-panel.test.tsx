/**
 * Tests for the Composition Template Assets panel (G04).
 *
 * Contract focus:
 *  - builtin assets render in the builtin section; DB assets in mine
 *  - "Save as my template" posts session_id + visibility and reloads
 *  - save disabled without a session
 *  - DB-unavailable disclosure (reason_codes) surfaces as an info notice
 *  - apply dispatches through the parent callback (no second write path)
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { I18nProvider } from '@/lib/i18n/i18n-provider';
import { CompositionTemplateAssetsPanel } from './composition-template-assets-panel';

// 与 clarification-card 等同款：包 I18nProvider（默认 zh-CN catalog），
// 否则 useT fallback 返回 key 字符串而非本地化文案。
function renderPanel(ui: React.ReactElement) {
  return render(<I18nProvider>{ui}</I18nProvider>);
}

const mockApi = vi.hoisted(() => ({
  list: vi.fn(),
  create: vi.fn(),
  get: vi.fn(),
  publish: vi.fn(),
  retire: vi.fn(),
  restore: vi.fn(),
}));
vi.mock('@/lib/api/composition-template-assets', () => ({
  compositionTemplateAssetsApi: mockApi,
}));

const BUILTIN = {
  asset_id: 'asset.builtin.heat_stats_portfolio',
  revision: 1,
  status: 'published' as const,
  builtin: true,
  fingerprint: 'asset-sha256:abcdef',
  template_id: 'asset.builtin.heat_stats_portfolio',
  name: '热力+统计图表组合',
  compatible_map_models: ['visual_heatmap'],
  output_targets: ['interactive', 'png'],
};
const DB_ASSET = {
  ...BUILTIN,
  asset_id: 'asset.db1',
  builtin: false,
  name: '我的版式',
  visibility: 'private' as const,
  latest_revision: 1,
};

beforeEach(() => {
  vi.clearAllMocks();
  mockApi.list.mockResolvedValue({
    success: true,
    assets: [BUILTIN, DB_ASSET],
    asset_count: 2,
    reason_codes: [],
  });
});

describe('CompositionTemplateAssetsPanel', () => {
  it('renders builtin and DB assets in their sections', async () => {
    renderPanel(
      <CompositionTemplateAssetsPanel open onClose={() => {}} sessionId="sess-1" />
    );
    await waitFor(() => {
      expect(screen.getByTestId('asset-row-asset.builtin.heat_stats_portfolio')).toBeTruthy();
      expect(screen.getByTestId('asset-row-asset.db1')).toBeTruthy();
    });
    expect(screen.getByText('热力+统计图表组合')).toBeTruthy();
    expect(screen.getByText('我的版式')).toBeTruthy();
    expect(mockApi.list).toHaveBeenCalledTimes(1);
  });

  it('save posts session_id + visibility then reloads', async () => {
    mockApi.create.mockResolvedValue({
      success: true, asset_id: 'asset.new1', status: 'published', revision: 1,
      validation: [],
    });
    renderPanel(
      <CompositionTemplateAssetsPanel open onClose={() => {}} sessionId="sess-9" />
    );
    await waitFor(() => expect(mockApi.list).toHaveBeenCalled());
    fireEvent.click(screen.getByTestId('save-my-template'));
    await waitFor(() => {
      expect(mockApi.create).toHaveBeenCalledWith(
        expect.objectContaining({ session_id: 'sess-9', visibility: 'private' })
      );
    });
    await waitFor(() => expect(mockApi.list).toHaveBeenCalledTimes(2));
  });

  it('save is disabled without a session', async () => {
    renderPanel(<CompositionTemplateAssetsPanel open onClose={() => {}} />);
    await waitFor(() => expect(mockApi.list).toHaveBeenCalled());
    const btn = screen.getByTestId('save-my-template') as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });

  it('publish-gate failure surfaces an error notice (draft saved)', async () => {
    mockApi.create.mockResolvedValue({
      success: true, asset_id: 'asset.new2', status: 'draft', revision: 1,
      validation: ['unknown component type'],
    });
    renderPanel(
      <CompositionTemplateAssetsPanel open onClose={() => {}} sessionId="sess-1" />
    );
    await waitFor(() => expect(mockApi.list).toHaveBeenCalled());
    fireEvent.click(screen.getByTestId('save-my-template'));
    await waitFor(() => {
      expect(screen.getByText(/发布门校验未通过/)).toBeTruthy();
    });
  });

  it('DB-unavailable disclosure renders an info notice', async () => {
    mockApi.list.mockResolvedValue({
      success: true, assets: [BUILTIN], asset_count: 1,
      reason_codes: ['db_unavailable: no such table'],
    });
    renderPanel(
      <CompositionTemplateAssetsPanel open onClose={() => {}} sessionId="sess-1" />
    );
    await waitFor(() => {
      expect(screen.getByText(/自定义资产暂不可用/)).toBeTruthy();
    });
  });

  it('apply dispatches through the parent callback', async () => {
    const onApply = vi.fn();
    renderPanel(
      <CompositionTemplateAssetsPanel
        open onClose={() => {}} sessionId="sess-1" onApplyAsset={onApply}
      />
    );
    await waitFor(() => expect(mockApi.list).toHaveBeenCalled());
    const row = screen.getByTestId('asset-row-asset.db1');
    fireEvent.click(row.querySelector('button:last-child')!);
    expect(onApply).toHaveBeenCalledWith(
      expect.objectContaining({ asset_id: 'asset.db1' })
    );
  });

  it('does not render when closed', () => {
    render(<CompositionTemplateAssetsPanel open={false} onClose={() => {}} />);
    expect(screen.queryByTestId('composition-template-assets-panel')).toBeNull();
    expect(mockApi.list).not.toHaveBeenCalled();
  });
});
