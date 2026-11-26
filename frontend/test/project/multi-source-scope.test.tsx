/**
 * 多源范围组件测试（WP4）：逐源计数 chips / 传播披露 / unavailable 原因
 * （不冒充 0）/ 不可投影与 handle 跳过披露 / loading / 空态 / 错误态。
 * 纯展示组件（props 驱动）——不 mock 网络层。
 */
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { MultiSourceScope } from '@/components/linked-exploration/multi-source-scope';
import type { PlanPreviewResponse } from '@/lib/linked-exploration/contracts';

function previewFixture(): PlanPreviewResponse {
  return {
    schema_version: 3,
    primary_source_id: 'school',
    anchors: {
      school: { ref_id: 'ref:s', content_revision: 1 },
      pop: { ref_id: 'ref:p', content_revision: 1 },
    },
    conditions_digest: 'g46m-abc',
    plan_digest: 'g46-def',
    per_source: {
      school: {
        source_id: 'school',
        role: 'primary',
        anchor: { ref_id: 'ref:s', content_revision: 1 },
        total: 6,
        match_count: 4,
        match_count_exact: true,
        own_where: null,
        propagations: [],
        map_filter: null,
        map_filter_projected: true,
      },
      pop: {
        source_id: 'pop',
        role: 'context',
        anchor: { ref_id: 'ref:p', content_revision: 1 },
        total: 4,
        match_count: 3,
        match_count_exact: true,
        own_where: null,
        propagations: [
          {
            link_id: 'l1',
            from_source: 'school',
            target_fields: ['district'],
            key_count: 3,
            from_unfiltered: false,
          },
        ],
        map_filter: ['in', ['get', 'district'], ['literal', ['a', 'b', 'c']]],
        map_filter_projected: true,
      },
    },
    charts: [],
    metrics: [
      {
        metric_id: 'm1',
        op: 'rate',
        unit: '所/万人',
        scale: 10000,
        title: '学校密度',
        source_id: 'school',
        grain_source_id: 'pop',
        groups: [
          { key: '武侯区', key_label: '武侯区', value: 0.03, available: true, count: 1 },
          {
            key: '零人口区', key_label: '零人口区', value: null,
            available: false, unavailable_reason: 'denominator_zero', count: 1,
          },
        ],
        truncated: false,
        disclosure: {},
      },
    ],
    completeness: 'complete',
  };
}

describe('MultiSourceScope', () => {
  afterEach(cleanup);

  it('renders per-source counts and propagation keys honestly', () => {
    render(<MultiSourceScope preview={previewFixture()} />);
    expect(screen.getByTestId('ms-source-count-school').textContent).toBe('4 / 6');
    expect(screen.getByTestId('ms-source-count-pop').textContent).toBe('3 / 4');
    const prop = screen.getByTestId('ms-prop-pop-l1');
    expect(prop.textContent).toContain('3');
  });

  it('renders unavailable metric groups with reason, never fake zero', () => {
    render(<MultiSourceScope preview={previewFixture()} />);
    const ok = screen.getByTestId('ms-metric-value-m1-武侯区');
    expect(ok.getAttribute('data-available')).toBe('true');
    const bad = screen.getByTestId('ms-metric-value-m1-零人口区');
    expect(bad.getAttribute('data-available')).toBe('false');
    expect(bad.getAttribute('data-reason')).toBe('denominator_zero');
    expect(bad.textContent).toContain('denominator_zero');
    expect(bad.textContent).not.toMatch(/0$/);
  });

  it('renders unprojected and handle-skipped disclosures', () => {
    const fx = previewFixture();
    fx.per_source.pop.map_filter_projected = false;
    fx.per_source.pop.selection_handle_skipped = {
      reason: 'propagation_not_ast_expressible',
      note: 'n/a',
    };
    render(<MultiSourceScope preview={fx} />);
    expect(screen.getByTestId('ms-unprojected-pop')).toBeTruthy();
    expect(screen.getByTestId('ms-noscope-pop')).toBeTruthy();
  });

  it('marks primary source role', () => {
    render(<MultiSourceScope preview={previewFixture()} />);
    expect(screen.getByTestId('ms-source-role-school').textContent).toContain('primary');
  });

  it('shows loading, empty and error states', () => {
    const { rerender } = render(<MultiSourceScope preview={null} loading />);
    expect(screen.getByTestId('ms-loading')).toBeTruthy();
    rerender(<MultiSourceScope preview={null} />);
    expect(screen.getByTestId('ms-empty')).toBeTruthy();
    rerender(
      <MultiSourceScope
        preview={null}
        error={{ code: 'STALE_SOURCE', message: '锚过期', hint: '重新预览' }}
      />,
    );
    const err = screen.getByTestId('ms-error');
    expect(err.getAttribute('role')).toBe('alert');
    expect(err.textContent).toContain('锚过期');
    expect(err.textContent).toContain('重新预览');
  });
});
