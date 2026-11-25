/**
 * DesignSeriesPanel 测试（mock API；服务端行为由后端测试覆盖）。
 *
 * 覆盖简报清单：列表渲染 / 空态 / 违规披露 / 锁定徽标 / 传播 receipt 渲染 /
 * apply 禁用逻辑（error findings / 未跑一致性）/ 窄屏单列 class。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('@/lib/api/design-specs', () => ({
  asDesignSpecError: vi.fn(),
  listDesignSpecs: vi.fn(),
  getDesignSpec: vi.fn(),
  createDesignSpec: vi.fn(),
  createDesignSpecVersion: vi.fn(),
  getDesignSpecVersion: vi.fn(),
  publishDesignSpec: vi.fn(),
  retireDesignSpec: vi.fn(),
  deprecateDesignSpecVersion: vi.fn(),
  getDesignSpecPreview: vi.fn(),
}));

vi.mock('@/lib/api/map-series', () => ({
  listMapSeries: vi.fn(),
  getMapSeries: vi.fn(),
  createMapSeries: vi.fn(),
  createMapSeriesVersion: vi.fn(),
  upsertMapSeriesMember: vi.fn(),
  removeMapSeriesMember: vi.fn(),
  getMapSeriesConsistency: vi.fn(),
  previewPropagation: vi.fn(),
  applyPropagation: vi.fn(),
  freezeMapSeriesPlan: vi.fn(),
}));

import { DesignSeriesPanel } from './design-series-panel';
import {
  getDesignSpec,
  getDesignSpecPreview,
  listDesignSpecs,
} from '@/lib/api/design-specs';
import {
  applyPropagation,
  getMapSeries,
  getMapSeriesConsistency,
  listMapSeries,
  previewPropagation,
} from '@/lib/api/map-series';
import { asDesignSpecError } from '@/lib/api/design-specs';
import type {
  DesignSpecDetail,
  DesignSpecSummary,
  MapSeriesDetail,
  MapSeriesSummary,
} from '@/lib/design-series/contracts';

const mocked = vi.mocked;

// asDesignSpecError 用真实解码逻辑（错误信封 {data:{code,message,violations}}）。
function useRealErrorDecoder() {
  (mocked(asDesignSpecError) as unknown as ReturnType<typeof vi.fn>).mockImplementation(
    (err: unknown) => {
      const body = (err as { body?: unknown } | null)?.body;
      const rec = body && typeof body === 'object' ? (body as Record<string, unknown>) : null;
      const src = rec && typeof rec.data === 'object' && rec.data !== null
        ? (rec.data as Record<string, unknown>) : null;
      if (src && typeof src.code === 'string') {
        return { code: src.code, message: String(src.message ?? src.code), violations: [] };
      }
      return { code: 'DESIGN_SERIES_REQUEST_FAILED', message: 'boom', violations: [] };
    },
  );
}

function specSummary(overrides: Partial<DesignSpecSummary> = {}): DesignSpecSummary {
  return {
    id: 'spec-1',
    name: '城市底图规范',
    description: '主城区底图',
    is_builtin: false,
    status: 'draft',
    current_version: 2,
    structural_digest: 'dspec-sha256:abcdef123456',
    retired: false,
    retirement_note: null,
    creator_id: 'u1',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

function specDetail(overrides: Partial<DesignSpecDetail> = {}): DesignSpecDetail {
  return {
    success: true,
    spec: specSummary(),
    payload: {
      spec_version: 'design_spec.v1',
      name: '城市底图规范',
      color: { theme_id: 'cartographic.light_interactive', palette_sequential: 'Blues' },
      classification: { default_method: 'quantiles', shared_required: true },
      output: { targets: ['interactive', 'png'] },
    },
    reference_validation: { ok: true, violations: [], warnings: [] },
    ...overrides,
  };
}

function seriesSummary(overrides: Partial<MapSeriesSummary> = {}): MapSeriesSummary {
  return {
    id: 'series-1',
    name: '三年对照系列',
    description: null,
    status: 'draft',
    current_version: 1,
    structural_digest: 'mser-sha256:123456abcdef',
    design_spec_id: 'spec-1',
    member_count: 2,
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

function seriesDetail(overrides: Partial<MapSeriesDetail> = {}): MapSeriesDetail {
  return {
    success: true,
    series: seriesSummary(),
    payload: {
      spec_version: 'map_series.v1',
      name: '三年对照系列',
      design_spec_id: 'spec-1',
      members: [
        {
          member_id: 'm1', title: '2020 主图', session_id: 'sess-2020',
          role: 'primary', dimension_tags: ['2020'], locked_fields: ['palette'],
          enabled: true,
        },
        {
          member_id: 'm2', title: '2022 对照', session_id: 'sess-2022',
          role: 'comparison', dimension_tags: ['2022'], locked_fields: [],
          enabled: false,
        },
      ],
    },
    ...overrides,
  };
}

describe('DesignSeriesPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useRealErrorDecoder();
  });

  it('renders the spec list and discloses reference violations on select', async () => {
    mocked(listDesignSpecs).mockResolvedValue({ success: true, items: [specSummary()] });
    mocked(getDesignSpec).mockResolvedValue(specDetail({
      reference_validation: {
        ok: false,
        violations: [{ reason: 'DATA_VALUES_FORBIDDEN', field: 'classification_breaks',
          message: 'payload 禁止数据断点' }],
        warnings: [],
      },
    }));
    const user = userEvent.setup();
    render(<DesignSeriesPanel />);

    const item = await waitFor(() => screen.getByTestId('design-series-spec-item-spec-1'));
    // 列表行披露：状态 / 版本 / digest 短码
    expect(item.getAttribute('data-status')).toBe('draft');
    expect(item).toHaveTextContent('v2');
    expect(item).toHaveTextContent('#abcdef12');

    await user.click(item);
    // 违规披露（reference_validation.violations）
    const violations = await screen.findByTestId('design-series-violations');
    expect(violations).toHaveTextContent('DATA_VALUES_FORBIDDEN');
    expect(violations).toHaveTextContent('classification_breaks');
  });

  it('shows empty states for specs and series (no fake data)', async () => {
    mocked(listDesignSpecs).mockResolvedValue({ success: true, items: [] });
    mocked(listMapSeries).mockResolvedValue({ success: true, items: [] });
    const user = userEvent.setup();
    render(<DesignSeriesPanel />);

    await waitFor(() => {
      expect(screen.getByTestId('design-series-specs-empty')).toBeTruthy();
    });
    await user.click(screen.getByTestId('design-series-tab-series'));
    await waitFor(() => {
      expect(screen.getByTestId('design-series-series-empty')).toBeTruthy();
    });
    expect(screen.queryByTestId('design-series-series-item-series-1')).toBeNull();
  });

  it('renders member cards with locked badge and session ref disclosure', async () => {
    mocked(listDesignSpecs).mockResolvedValue({ success: true, items: [] });
    mocked(listMapSeries).mockResolvedValue({ success: true, items: [seriesSummary()] });
    mocked(getMapSeries).mockResolvedValue(seriesDetail());
    const user = userEvent.setup();
    render(<DesignSeriesPanel />);

    await user.click(screen.getByTestId('design-series-tab-series'));
    await user.click(await screen.findByTestId('design-series-series-item-series-1'));

    const m1 = await screen.findByTestId('design-series-member-card-m1');
    expect(m1.getAttribute('data-locked')).toBe('true');
    expect(m1.getAttribute('data-enabled')).toBe('true');
    // 锁定徽标（user-wins 锁如实披露）
    expect(m1.querySelector('[data-testid="design-series-locked-badge"]')).toBeTruthy();

    const m2 = screen.getByTestId('design-series-member-card-m2');
    expect(m2.getAttribute('data-locked')).toBe('false');
    expect(m2.getAttribute('data-enabled')).toBe('false');

    // 选中成员 → 「在地图打开」仅展示 session_id 引用（不跨会话加载）
    await user.click(screen.getByTestId('design-series-member-select-m1'));
    const openInMap = await screen.findByTestId('design-series-open-in-map');
    expect(openInMap).toHaveTextContent('sess-2020');
  });

  it('grid is single-column on narrow screens, two columns on lg', async () => {
    mocked(listDesignSpecs).mockResolvedValue({ success: true, items: [] });
    render(<DesignSeriesPanel />);
    const grid = await waitFor(() => screen.getByTestId('design-series-grid'));
    expect(grid.className).toContain('grid-cols-1');
    expect(grid.className).toContain('lg:grid-cols-2');
  });

  it('propagation: apply disabled until consistency runs clean; receipts render', async () => {
    mocked(listDesignSpecs).mockResolvedValue({ success: true, items: [specSummary()] });
    mocked(listMapSeries).mockResolvedValue({ success: true, items: [seriesSummary()] });
    mocked(getMapSeries).mockResolvedValue(seriesDetail());
    mocked(previewPropagation).mockResolvedValue({
      success: true,
      series_id: 'series-1',
      member_count: 3,
      unresolvable_member_ids: [],
      members: [
        { member_id: 'm1', unresolvable: false, locked_fields_hit: [],
          changes: [{ intent: 'palette' }], eligible: true },
        { member_id: 'm2', unresolvable: false, locked_fields_hit: [],
          changes: [], eligible: false },
        { member_id: 'm3', unresolvable: false, locked_fields_hit: ['palette'],
          changes: [], eligible: false },
      ],
    });
    mocked(applyPropagation).mockResolvedValue({
      success: true,
      receipts: [
        { member_id: 'm1', status: 'committed', revision: 4, error: null },
        { member_id: 'm2', status: 'superseded', revision: null, error: null },
        { member_id: 'm3', status: 'skipped_locked', revision: null, error: null },
        { member_id: 'm4', status: 'failed', revision: null, error: 'MAPSPEC_CONFLICT' },
      ],
    });
    const user = userEvent.setup();
    render(<DesignSeriesPanel />);

    await user.click(screen.getByTestId('design-series-tab-propagation'));

    // 未选系列（未跑一致性）→ apply 禁用 + 提示
    const applyBtn = screen.getByTestId('design-series-prop-apply') as HTMLButtonElement;
    expect(applyBtn.disabled).toBe(true);

    // error 级 findings → apply 仍禁用 + error 提示
    mocked(getMapSeriesConsistency).mockResolvedValue({
      ok: false,
      findings: [{ code: 'SPEC_PIN_MISSING', severity: 'error', member_id: 'm2',
        component: 'spec', message: '规范 pin 缺失', fix_hint: '先绑定设计规范' }],
    });
    await user.selectOptions(screen.getByTestId('design-series-prop-series'), 'series-1');
    const gateHint = await screen.findByTestId('design-series-prop-gate-hint');
    await waitFor(() => {
      expect(gateHint).toHaveTextContent('(1)');
    });
    expect((screen.getByTestId('design-series-prop-apply') as HTMLButtonElement).disabled).toBe(true);

    // 一致性干净后 → preview 三列 → apply 可用 → 二次确认 → receipts
    mocked(getMapSeriesConsistency).mockResolvedValue({ ok: true, findings: [] });
    await user.click(screen.getByTestId('design-series-prop-rerun'));
    await waitFor(() => {
      const btn = screen.getByTestId('design-series-prop-apply') as HTMLButtonElement;
      expect(btn.disabled).toBe(false);
    });

    await user.click(screen.getByTestId('design-series-prop-preview-run'));
    await waitFor(() => {
      expect(screen.getByTestId('design-series-prop-col-affected')).toHaveTextContent('m1');
    });
    const colAffected = screen.getByTestId('design-series-prop-col-affected');
    expect(colAffected).toHaveTextContent('m1');
    expect(colAffected).not.toHaveTextContent('m2');
    // 锁定列 = members[].locked_fields_hit 投影（member_id:field）
    expect(screen.getByTestId('design-series-prop-col-locked')).toHaveTextContent('m3:palette');
    expect(screen.getByTestId('design-series-prop-col-unresolvable')).toHaveTextContent('空');

    await user.click(screen.getByTestId('design-series-prop-apply'));
    await user.click(screen.getByTestId('design-series-prop-confirm-run'));

    const receipts = await screen.findByTestId('design-series-receipts');
    expect(receipts.querySelectorAll('li')).toHaveLength(4);
    expect(screen.getByTestId('design-series-receipt-m1').querySelector('[data-status="committed"]'))
      .toBeTruthy();
    expect(screen.getByTestId('design-series-receipt-m2').querySelector('[data-status="superseded"]'))
      .toBeTruthy();
    expect(screen.getByTestId('design-series-receipt-m3').querySelector('[data-status="skipped_locked"]'))
      .toBeTruthy();
    const failed = screen.getByTestId('design-series-receipt-m4');
    expect(failed.querySelector('[data-status="failed"]')).toBeTruthy();
    expect(failed).toHaveTextContent('MAPSPEC_CONFLICT');
  });

  it('preview render targets and conflicts are disclosed for the selected spec', async () => {
    mocked(listDesignSpecs).mockResolvedValue({ success: true, items: [specSummary()] });
    mocked(getDesignSpec).mockResolvedValue(specDetail());
    mocked(getDesignSpecPreview).mockResolvedValue({
      success: true,
      spec: specSummary(),
      version: { id: 'v2', spec_id: 'spec-1', version: 2, description: null,
        deprecated: false, parent_version_id: 'v1' },
      effective_payload: { spec_version: 'design_spec.v1', name: '城市底图规范' },
      inheritance_chain: ['spec-1', 'spec-1'],
      reference_validation: { ok: true, violations: [], warnings: [] },
      render_targets: {
        supported: ['interactive', 'png'],
        unsupported: [{ target: 'svg', reason: 'theme lacks sequential palette' }],
        font_findings: [{ font: 'noto-cjk', available: true }],
      },
      conflicts: [{ code: 'THEME_TARGET_CONFLICT', message: 'dark theme vs pdf' }],
      resolved_digest: 'dspec-sha256:ffffffff',
    });
    const user = userEvent.setup();
    render(<DesignSeriesPanel />);

    await user.click(await screen.findByTestId('design-series-spec-item-spec-1'));
    await user.click(await screen.findByTestId('design-series-preview-run'));
    await waitFor(() => {
      expect(screen.getByTestId('design-series-targets-supported')).toHaveTextContent('interactive');
    });
    expect(screen.getByTestId('design-series-targets-unsupported')).toHaveTextContent('svg');
    expect(screen.getByTestId('design-series-conflicts')).toHaveTextContent('THEME_TARGET_CONFLICT');
  });
});
