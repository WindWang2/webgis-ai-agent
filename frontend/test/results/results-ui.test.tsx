import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { useHudStore } from '@/lib/store/useHudStore';
import { normalizeStepResult } from '@/lib/results/normalize';
import { ResultList } from '@/components/sidebar/results/result-list';
import { ResultDetail } from '@/components/sidebar/results/result-detail';
import { createMockLayer } from '../test-utils';
import type { AnalysisResult } from '@/lib/results/types';

beforeEach(() => {
  useHudStore.getState().clearResults();
  useHudStore.setState({ layers: [] });
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
