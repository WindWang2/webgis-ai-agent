import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { useHudStore } from '@/lib/store/useHudStore';
import { normalizeStepResult } from '@/lib/results/normalize';
import { ResultList } from '@/components/sidebar/results/result-list';
import { ResultDetail } from '@/components/sidebar/results/result-detail';
import { ResultsTab } from '@/components/sidebar/results-tab';
import { createMockLayer } from '../test-utils';
import type { AnalysisResult } from '@/lib/results/types';

beforeEach(() => {
  useHudStore.getState().clearResults();
  useHudStore.setState({ layers: [], focusLayerId: null });
});

function hotspotResult(summary = '已识别热点。'): AnalysisResult {
  return normalizeStepResult({
    step_id: 's1',
    tool: 'hotspot_analysis',
    geojson_ref: 'ref:geojson-x1',
    result: {
      success: true,
      summary,
      bbox: [116, 39, 117, 40],
      data: { type: 'FeatureCollection', features: [], hot_spots_count: 12, cold_spots_count: 3, distance_band_m: 800 },
    },
  });
}

describe('ResultList', () => {
  it('shows an empty state when there are no results', () => {
    render(<ResultList results={[]} selectedId={null} onSelect={() => {}} />);
    expect(screen.getByText('暂无分析结果')).toBeInTheDocument();
  });

  it('renders results and selects on click', () => {
    const r = hotspotResult();
    const onSelect = vi.fn();
    render(<ResultList results={[r]} selectedId={null} onSelect={onSelect} />);
    expect(screen.getByText('已识别热点。')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button'));
    expect(onSelect).toHaveBeenCalledWith('s1');
  });
});

describe('ResultDetail — truthfulness + map linkage', () => {
  it('shows metrics, keeps CRS unknown, surfaces warnings, and toggles layer visibility', () => {
    // A genuine warning via correction_hint (separate from the summary, so the
    // surfaced warning text is unambiguous).
    const r = normalizeStepResult({
      step_id: 's1',
      tool: 'hotspot_analysis',
      geojson_ref: 'ref:geojson-x1',
      result: {
        success: true,
        summary: '已识别热点。',
        correction_hint: '请检查输入字段是否完整。',
        bbox: [116, 39, 117, 40],
        data: { type: 'FeatureCollection', features: [], hot_spots_count: 12, cold_spots_count: 3, distance_band_m: 800 },
      },
    });
    useHudStore.setState({ results: [r] });
    useHudStore.setState({
      layers: [createMockLayer({ id: 'ref:geojson-x1', name: 'Hotspot', visible: false })],
    });

    render(
      <ResultDetail result={r} sessionId="sess" ownerToken={null} onBack={() => {}} onSend={() => {}} />,
    );

    // Metric rendered (hot spot count = 12).
    expect(screen.getByText('12')).toBeInTheDocument();
    // CRS kept truthful ("未知"), never fabricated as EPSG:4326.
    expect(screen.getByText('未知')).toBeInTheDocument();
    // Warning surfaced (not buried in raw JSON).
    expect(screen.getByText('请检查输入字段是否完整。')).toBeInTheDocument();

    // Map action: layer is hidden → "在地图上显示" toggles store visibility.
    const showBtn = screen.getByRole('button', { name: '在地图上显示' });
    fireEvent.click(showBtn);
    expect(useHudStore.getState().layers[0].visible).toBe(true);
  });

  it('renders the failed state and correction hint', () => {
    const failed = normalizeStepResult({
      step_id: 's-fail',
      tool: 'hotspot_analysis',
      result: { success: false, error_type: 'VALIDATION_ERROR', summary: 'Missing value_field.', correction_hint: '请提供 value_field。' },
    });
    useHudStore.setState({ results: [failed] });
    render(
      <ResultDetail result={failed} sessionId="sess" ownerToken={null} onBack={() => {}} onSend={() => {}} />,
    );
    expect(screen.getByText('失败')).toBeInTheDocument();
    expect(screen.getByText('请提供 value_field。')).toBeInTheDocument();
  });
});

describe('ResultDetail — V4 action grouping + status vocabulary', () => {
  function renderDetail(r: AnalysisResult, layers: ReturnType<typeof createMockLayer>[] = []) {
    useHudStore.setState({ results: [r], layers });
    return render(
      <ResultDetail result={r} sessionId="sess" ownerToken={null} onBack={() => {}} onSend={() => {}} />,
    );
  }

  it('keeps map controls inside 输出与地图 and analytical intents in 后续操作', () => {
    renderDetail(hotspotResult(), [
      createMockLayer({ id: 'ref:geojson-x1', name: 'Hotspot', visible: true }),
    ]);

    const output = screen.getByRole('region', { name: '输出与地图' });
    const next = screen.getByRole('region', { name: '后续操作' });

    // Map controls (store-driven) live with the layer state they act on.
    expect(within(output).getByRole('button', { name: '在地图上隐藏' })).toBeInTheDocument();
    expect(within(output).getByRole('button', { name: '缩放至结果' })).toBeInTheDocument();
    // Analytical intents are a separate, secondary group — never mixed in.
    expect(within(output).queryByRole('button', { name: '导出结果' })).not.toBeInTheDocument();
    expect(within(next).getByRole('button', { name: '导出结果' })).toBeInTheDocument();
  });

  it('zoom focuses the bound layer through the store', () => {
    renderDetail(hotspotResult(), [
      createMockLayer({ id: 'ref:geojson-x1', name: 'Hotspot', visible: true }),
    ]);
    fireEvent.click(screen.getByRole('button', { name: '缩放至结果' }));
    expect(useHudStore.getState().focusLayerId).toBe('ref:geojson-x1');
  });

  it('labels warning-carrying results 含告警 from the shared status vocabulary', () => {
    const r = normalizeStepResult({
      step_id: 's-warn',
      tool: 'isochrone_network',
      geojson_ref: 'ref:iso-x',
      result: { success: true, summary: 'Isochrone built. 3 facility(ies) unreachable (disconnected from the road network).' },
    });
    renderDetail(r);
    expect(screen.getByText('含告警')).toBeInTheDocument();
  });

  it('drops the output and next-action sections for a failed result', () => {
    const failed = normalizeStepResult({
      step_id: 's-fail2',
      tool: 'hotspot_analysis',
      result: { success: false, error_type: 'VALIDATION_ERROR', summary: 'Missing value_field.', correction_hint: '请提供 value_field。' },
    });
    renderDetail(failed);
    expect(screen.queryByRole('region', { name: '输出与地图' })).not.toBeInTheDocument();
    expect(screen.queryByRole('region', { name: '后续操作' })).not.toBeInTheDocument();
  });

  it('explains an unbound ref instead of rendering dead map controls', () => {
    const r = normalizeStepResult({
      step_id: 's-unbound',
      tool: 'hotspot_analysis',
      geojson_ref: 'ref:gone',
      result: { success: true, summary: 'ok' },
    });
    renderDetail(r, []);
    expect(screen.getByText('引用层未绑定到当前地图会话。')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '缩放至结果' })).not.toBeInTheDocument();
  });

  it('explains a ref-less result has no map layer', () => {
    const r = normalizeStepResult({
      step_id: 's-stat',
      tool: 'moran_i',
      result: { success: true, summary: 'ok', data: { moran_i: 0.42 } },
    });
    renderDetail(r, []);
    expect(screen.getByText('该结果未挂载为地图图层。')).toBeInTheDocument();
  });

  it('export on a ref-less statistic still calls onSend', () => {
    const r = normalizeStepResult({
      step_id: 's-stat',
      tool: 'moran_i',
      result: { success: true, summary: 'ok', data: { moran_i: 0.42 } },
    });
    const onSend = vi.fn();
    render(
      <ResultDetail result={r} sessionId="sess" ownerToken={null} onBack={() => {}} onSend={onSend} />,
    );
    fireEvent.click(screen.getByRole('button', { name: '导出结果' }));
    expect(onSend).toHaveBeenCalledTimes(1);
    expect(onSend.mock.calls[0][0]).toContain('导出');
  });
});

describe('ResultList — V4 density contracts', () => {
  it('marks the selected row with aria-current', () => {
    const r = hotspotResult();
    render(<ResultList results={[r]} selectedId="s1" onSelect={() => {}} />);
    const row = screen.getByRole('button');
    expect(row).toHaveAttribute('aria-current', 'true');
  });

  it('restores focus to the opened row after Back (drill-in focus contract)', () => {
    const r = hotspotResult();
    useHudStore.setState({ results: [r] });
    render(<ResultsTab sessionId="sess" ownerToken={null} onSend={() => {}} />);

    // Drill in: focus lands on the detail's back button.
    fireEvent.click(screen.getByRole('button'));
    expect(screen.getByRole('button', { name: '返回结果列表' })).toHaveFocus();

    // Go back: focus returns to the row that was opened.
    fireEvent.click(screen.getByRole('button', { name: '返回结果列表' }));
    expect(screen.getByRole('button')).toHaveFocus();
  });

  it('falls back to the list container when Back targets a removed row', () => {
    // Removing a result from its own detail view goes Back to a row that no
    // longer exists — keyboard focus must not fall to <body>.
    const r1 = hotspotResult();
    const r2 = normalizeStepResult({
      step_id: 's2',
      tool: 'hotspot_analysis',
      geojson_ref: 'ref:geojson-x2',
      result: {
        success: true,
        summary: '第二个结果',
        bbox: [116, 39, 117, 40],
        data: { type: 'FeatureCollection', features: [] },
      },
    });
    useHudStore.setState({ results: [r1, r2] });
    render(<ResultsTab sessionId="sess" ownerToken={null} onSend={() => {}} />);

    // Drill into the first row, then remove it from the detail (remove+back).
    fireEvent.click(screen.getAllByRole('button')[0]);
    expect(screen.getByRole('button', { name: '返回结果列表' })).toHaveFocus();
    fireEvent.click(screen.getByRole('button', { name: '从列表移除' }));

    // The opened row is gone: focus lands on the list container itself.
    expect(screen.getByLabelText('分析结果列表')).toHaveFocus();
    // And the remaining row is still offered.
    expect(screen.getAllByRole('button').length).toBeGreaterThan(0);
  });

  it('shows the layer chip only when an output is mounted as a layer', () => {
    const withLayer = hotspotResult();
    const without = normalizeStepResult({
      step_id: 's-stat',
      tool: 'moran_i',
      result: { success: true, summary: '统计完成。', data: { moran_i: 0.42 } },
    });
    render(<ResultList results={[withLayer, without]} selectedId={null} onSelect={() => {}} />);
    const chips = screen.getAllByText('图层');
    expect(chips).toHaveLength(1);
  });

  it('shows the warning tally in the warning colour vocabulary, not amber literals', () => {
    const r = normalizeStepResult({
      step_id: 's-warn',
      tool: 'isochrone_network',
      geojson_ref: 'ref:iso-x',
      result: { success: true, summary: 'Isochrone built. 3 facility(ies) unreachable (disconnected from the road network).' },
    });
    render(<ResultList results={[r]} selectedId={null} onSelect={() => {}} />);
    expect(screen.getByText('1 条告警')).toBeInTheDocument();
    expect(screen.getByText('1 条告警').className).toContain('status-warning');
    // No raw Tailwind palette literals for the warning role.
    expect(screen.getByText('1 条告警').className).not.toMatch(/amber|yellow-/);
  });
});
