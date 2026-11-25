/**
 * G34 PrintLayoutPanel 回归测试：能力/草稿边界条件。
 * - 字体清单为空 ≠ 字体不可用（不得禁用导出）；
 * - 无草稿时纸张选择器不可交互（patch-plan 会被 reducer 丢弃）；
 * - 清空数值输入不得把 0/NaN 写进版式草稿。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('@/lib/mapspec/session-cursor', () => ({
  getMapSpecSessionCursor: () => ({ sessionId: 'sess-print', ownerToken: 'tok' }),
  setMapSpecRevision: vi.fn(),
}));

vi.mock('@/lib/print-layout/api', () => ({
  fetchInspect: vi.fn(),
  fetchCapabilities: vi.fn(),
  fetchPages: vi.fn(),
  postApply: vi.fn(),
  postPreview: vi.fn(),
  postPropose: vi.fn(),
  postExport: vi.fn(),
  postTemplate: vi.fn(),
  asPrintLayoutError: vi.fn(() => ({ code: 'PRINT_LAYOUT_REQUEST_FAILED', message: 'x' })),
}));

import { PrintLayoutPanel } from './print-layout-panel';
import { defaultPrintLayout, type PrintLayoutV1 } from '@/lib/print-layout/contracts';
import { fetchCapabilities, fetchInspect, fetchPages } from '@/lib/print-layout/api';
import type { Capabilities, PagesResult } from '@/lib/print-layout/api';

const mocked = vi.mocked;

function capabilities(fonts: Capabilities['fonts'] = [{
  font_id: 'noto-sans-sc-embedded', family: 'Noto Sans SC', available: true,
  weights: [], supports_cjk: true, sha256: '',
}]): Capabilities {
  return {
    success: true,
    schema_version: 'print_layout.v1',
    fonts,
    font_asset_version: '',
    paper_profiles: { a4_landscape: { width_mm: 297, height_mm: 210 } },
    operations: [],
    component_kinds: [],
    text_bindings: [],
    budgets: {},
  };
}

function inspect(plan: PrintLayoutV1 | null = defaultPrintLayout(), revision = 5) {
  return {
    success: true, status: 'configured' as const, plan, locks: [],
    mapspec_revision: revision, font_asset_version: '',
  };
}

const PAGES: PagesResult = {
  success: true, page_count: 1, page_budget: 20, planner_page_count: 1,
  exceeds_budget: false, toc: [],
  pages: [{ page_id: 'main', title: '主图', page_number: 1 }], disabled_pages: [],
};

beforeEach(() => {
  vi.clearAllMocks();
  mocked(fetchInspect).mockResolvedValue(inspect());
  mocked(fetchCapabilities).mockResolvedValue(capabilities());
  mocked(fetchPages).mockResolvedValue(PAGES);
});

async function renderLoaded() {
  render(<PrintLayoutPanel />);
  await waitFor(() => {
    expect(screen.getByTestId('print-layout-status').getAttribute('data-status')).toBe('committed');
  });
}

describe('PrintLayoutPanel', () => {
  it('keeps export enabled when capabilities report an empty font list', async () => {
    mocked(fetchCapabilities).mockResolvedValue(capabilities([]));
    await renderLoaded();
    for (const fmt of ['pdf', 'svg', 'png']) {
      expect(screen.getByTestId(`print-layout-export-${fmt}`)).toBeEnabled();
    }
  });

  it('disables the paper selector when there is no draft to patch', async () => {
    mocked(fetchInspect).mockResolvedValue(inspect(null));
    render(<PrintLayoutPanel />);
    await waitFor(() => {
      expect(screen.getByTestId('print-layout-status').getAttribute('data-status')).toBe('idle');
    });
    expect(screen.getAllByRole('combobox')[0]).toBeDisabled();
  });

  it('ignores cleared numeric inputs instead of writing zero or NaN into the draft', async () => {
    await renderLoaded();
    const user = userEvent.setup();
    const frameX = screen.getByLabelText('title-x');
    await user.clear(frameX);
    expect(frameX).toHaveValue(15);
    const zoom = screen.getAllByRole('spinbutton')[0];
    await user.clear(zoom);
    expect(zoom).toHaveValue(1);
  });
});
