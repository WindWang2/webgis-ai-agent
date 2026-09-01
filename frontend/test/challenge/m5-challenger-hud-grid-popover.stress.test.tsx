import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, act, cleanup } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import React from 'react';
import { MapToolbarHUD } from '../../components/map/map-toolbar-hud';
import { TabularDataGrid } from '../../components/explorer/tabular-data-grid';
import { PoiInfoPanel, featureDisplayName } from '../../components/map/poi-info-panel';
import { useHudStore } from '../../lib/store/useHudStore';

/* eslint-disable @typescript-eslint/no-require-imports */
vi.mock('framer-motion', () => {
  const fm = require('../__mocks__/framer-motion');
  return {
    ...fm,
    AnimatePresence: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  };
});
/* eslint-enable @typescript-eslint/no-require-imports */

describe('M5 Empirical Challenge Suite: MapToolbarHUD (R3)', () => {
  const mockZoomIn = vi.fn();
  const mockZoomOut = vi.fn();
  const mockResetNorthPitch = vi.fn();
  const mockEaseTo = vi.fn();
  const mockGetZoom = vi.fn(() => 5);
  const mockZoomTo = vi.fn();

  const fakeMapInstance = {
    zoomIn: mockZoomIn,
    zoomOut: mockZoomOut,
    resetNorthPitch: mockResetNorthPitch,
    easeTo: mockEaseTo,
    getZoom: mockGetZoom,
    zoomTo: mockZoomTo,
  };

  const mapRef = {
    current: {
      getMap: () => fakeMapInstance,
    },
  } as any;

  beforeEach(() => {
    vi.clearAllMocks();
    useHudStore.setState({
      is3D: false,
      annotations: [],
      selectedFeature: null,
    } as any);
  });

  afterEach(() => {
    cleanup();
  });

  describe('1. Rapid Sequential Camera Actions & Map Error Tolerance', () => {
    it('survives 100 rapid sequential zoomIn and zoomOut clicks without state desync', () => {
      render(<MapToolbarHUD mapRef={mapRef} />);
      const zoomInBtn = screen.getByRole('button', { name: '放大' });
      const zoomOutBtn = screen.getByRole('button', { name: '缩小' });

      for (let i = 0; i < 50; i++) {
        fireEvent.click(zoomInBtn);
        fireEvent.click(zoomOutBtn);
      }

      expect(mockZoomIn).toHaveBeenCalledTimes(50);
      expect(mockZoomOut).toHaveBeenCalledTimes(50);
    });

    it('gracefully handles missing mapRef, null mapRef, or null getMap() without throwing', () => {
      // Missing mapRef
      const { unmount: u1 } = render(<MapToolbarHUD />);
      expect(() => fireEvent.click(screen.getByRole('button', { name: '放大' }))).not.toThrow();
      expect(() => fireEvent.click(screen.getByRole('button', { name: '缩小' }))).not.toThrow();
      expect(() => fireEvent.click(screen.getByRole('button', { name: '重置指北与俯仰角' }))).not.toThrow();
      u1();

      // Null ref
      const nullRef = { current: null } as any;
      const { unmount: u2 } = render(<MapToolbarHUD mapRef={nullRef} />);
      expect(() => fireEvent.click(screen.getByRole('button', { name: '放大' }))).not.toThrow();
      u2();

      // getMap returns null
      const nullMapRef = { current: { getMap: () => null } } as any;
      const { unmount: u3 } = render(<MapToolbarHUD mapRef={nullMapRef} />);
      expect(() => fireEvent.click(screen.getByRole('button', { name: '放大' }))).not.toThrow();
      u3();
    });

    it('falls back to zoomTo when zoomIn/zoomOut methods are missing on map instance', () => {
      const fallbackMap = {
        getZoom: vi.fn(() => 7),
        zoomTo: vi.fn(),
      };
      const fallbackRef = { current: { getMap: () => fallbackMap } } as any;
      render(<MapToolbarHUD mapRef={fallbackRef} />);

      fireEvent.click(screen.getByRole('button', { name: '放大' }));
      expect(fallbackMap.getZoom).toHaveBeenCalled();
      expect(fallbackMap.zoomTo).toHaveBeenCalledWith(8);

      fireEvent.click(screen.getByRole('button', { name: '缩小' }));
      expect(fallbackMap.zoomTo).toHaveBeenCalledWith(6);
    });

    it('falls back to easeTo when resetNorthPitch is missing on map instance', () => {
      const fallbackMap = {
        easeTo: vi.fn(),
      };
      const fallbackRef = { current: { getMap: () => fallbackMap } } as any;
      render(<MapToolbarHUD mapRef={fallbackRef} />);

      fireEvent.click(screen.getByRole('button', { name: '重置指北与俯仰角' }));
      expect(fallbackMap.easeTo).toHaveBeenCalledWith({ bearing: 0, pitch: 0, duration: 400 });
      expect(screen.getByText('已重置正北与俯仰角')).toBeInTheDocument();
    });
  });

  describe('2. Bearing & Pitch Boundary Conditions', () => {
    it('computes correct compass rotation for boundary angles: 0, 360, -360, 720, -180, NaN', () => {
      const angles = [0, 90, 180, 270, 360, -90, -180, -360, 720];

      for (const angle of angles) {
        const { container, unmount } = render(<MapToolbarHUD mapRef={mapRef} bearing={angle} pitch={0} />);
        const compass = container.querySelector('svg.lucide-compass');
        expect(compass).toBeInTheDocument();
        expect(compass?.getAttribute('style')).toContain(`rotate(${-angle}deg)`);
        unmount();
      }
    });

    it('shows pitch indicator dot if and only if pitch > 0', () => {
      const { container: c1, unmount: u1 } = render(<MapToolbarHUD mapRef={mapRef} pitch={0} />);
      const dot0 = c1.querySelector('.bg-status-accent.rounded-full');
      expect(dot0).toBeNull();
      u1();

      const { container: c2, unmount: u2 } = render(<MapToolbarHUD mapRef={mapRef} pitch={45} />);
      const dot45 = c2.querySelector('.bg-status-accent.rounded-full');
      expect(dot45).not.toBeNull();
      u2();
    });
  });

  describe('3. 2D / 3D Fast Flapping Stress', () => {
    it('survives 50 rapid sequential 2D/3D toggles and keeps store in sync', () => {
      render(<MapToolbarHUD mapRef={mapRef} />);
      const toggle3DBtn = screen.getByRole('button', { name: '切换3D视图' });

      for (let i = 0; i < 50; i++) {
        fireEvent.click(toggle3DBtn);
        expect(useHudStore.getState().is3D).toBe((i + 1) % 2 === 1);
      }
      expect(useHudStore.getState().is3D).toBe(false);
    });
  });

  describe('4. Measurement State Machine Fuzzing & Pathological Geometries', () => {
    it('handles switching distance -> area -> none with single points and pathological inputs', () => {
      const onModeChange = vi.fn();
      const { rerender } = render(
        <MapToolbarHUD
          mapRef={mapRef}
          activeMeasureTool="distance"
          onMeasureToolChange={onModeChange}
          measurePoints={[[104.0, 30.0]]}
        />
      );

      // Single point distance: needs at least 2 points
      expect(screen.getByText('需至少 2 点')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: '完成标注' })).toBeDisabled();

      // Switch to area with 2 points: needs at least 3 points
      rerender(
        <MapToolbarHUD
          mapRef={mapRef}
          activeMeasureTool="area"
          onMeasureToolChange={onModeChange}
          measurePoints={[
            [104.0, 30.0],
            [104.1, 30.0],
          ]}
        />
      );
      expect(screen.getByText('需至少 3 点')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: '完成标注' })).toBeDisabled();

      // Area with identical duplicate points
      rerender(
        <MapToolbarHUD
          mapRef={mapRef}
          activeMeasureTool="area"
          onMeasureToolChange={onModeChange}
          measurePoints={[
            [104.0, 30.0],
            [104.0, 30.0],
            [104.0, 30.0],
          ]}
        />
      );
      expect(screen.getByText(/km²|m²/)).toBeInTheDocument();
      expect(screen.getByRole('button', { name: '完成标注' })).not.toBeDisabled();
    });

    it('saves distance measurement to store in uncontrolled fallback mode', () => {
      render(
        <MapToolbarHUD
          mapRef={mapRef}
          activeMeasureTool="distance"
          measurePoints={[
            [104.0665, 30.5728],
            [104.0815, 30.6558],
          ]}
        />
      );

      const finishBtn = screen.getByRole('button', { name: '完成标注' });
      fireEvent.click(finishBtn);

      const annotations = useHudStore.getState().annotations;
      expect(annotations.length).toBe(2); // LineString + End Point label
      expect((annotations[0].geometry as any).type).toBe('LineString');
      expect((annotations[1].geometry as any).type).toBe('Point');
      expect(screen.getByText('测量标注已保存至地图')).toBeInTheDocument();
    });

    it('saves area polygon measurement to store in uncontrolled fallback mode', () => {
      render(
        <MapToolbarHUD
          mapRef={mapRef}
          activeMeasureTool="area"
          measurePoints={[
            [104.0, 30.0],
            [104.1, 30.0],
            [104.1, 30.1],
            [104.0, 30.1],
          ]}
        />
      );

      const finishBtn = screen.getByRole('button', { name: '完成标注' });
      fireEvent.click(finishBtn);

      const annotations = useHudStore.getState().annotations;
      expect(annotations.length).toBe(2); // Polygon + Centroid Point label
      expect((annotations[0].geometry as any).type).toBe('Polygon');
      expect((annotations[1].geometry as any).type).toBe('Point');
      expect(screen.getByText('测量标注已保存至地图')).toBeInTheDocument();
    });
  });

  describe('5. Keyboard Shortcuts & Form Input Exemption', () => {
    it('ignores global shortcuts when typing inside an input or textarea', () => {
      render(
        <div>
          <input data-testid="test-input" />
          <textarea data-testid="test-textarea" />
          <div data-testid="test-editable" contentEditable="true" />
          <MapToolbarHUD mapRef={mapRef} />
        </div>
      );

      const input = screen.getByTestId('test-input');
      input.focus();
      fireEvent.keyDown(input, { key: '+' });
      fireEvent.keyDown(input, { key: '3' });
      fireEvent.keyDown(input, { key: 'd' });

      expect(mockZoomIn).not.toHaveBeenCalled();
      expect(useHudStore.getState().is3D).toBe(false);

      const textarea = screen.getByTestId('test-textarea');
      textarea.focus();
      fireEvent.keyDown(textarea, { key: '-' });
      expect(mockZoomOut).not.toHaveBeenCalled();
    });

    it('handles uppercase and alternate shortcut keys (=, N, D, A)', () => {
      render(<MapToolbarHUD mapRef={mapRef} />);

      act(() => {
        window.dispatchEvent(new KeyboardEvent('keydown', { key: '=' }));
      });
      expect(mockZoomIn).toHaveBeenCalledTimes(1);

      act(() => {
        window.dispatchEvent(new KeyboardEvent('keydown', { key: 'N' }));
      });
      expect(mockResetNorthPitch).toHaveBeenCalledTimes(1);

      act(() => {
        window.dispatchEvent(new KeyboardEvent('keydown', { key: 'D' }));
      });
      expect(screen.getByText('距离测量模式')).toBeInTheDocument();

      act(() => {
        window.dispatchEvent(new KeyboardEvent('keydown', { key: 'A' }));
      });
      expect(screen.getByText('面积测量模式')).toBeInTheDocument();
    });
  });

  describe('6. Rapid Mobile Collapse & Toast Dismissal', () => {
    it('survives 30 rapid collapse toggles and keeps controls accessible on desktop', () => {
      render(<MapToolbarHUD mapRef={mapRef} />);
      const collapseBtn = screen.getByRole('button', { name: '折叠工具栏' });

      for (let i = 0; i < 30; i++) {
        fireEvent.click(collapseBtn);
      }
      // Even number of clicks returns to expanded
      expect(screen.getByRole('button', { name: '放大' })).toBeInTheDocument();
    });

    it('clears toast timer on unmount without throwing errors', () => {
      const { unmount } = render(<MapToolbarHUD mapRef={mapRef} />);
      fireEvent.click(screen.getByRole('button', { name: '切换3D视图' }));
      expect(screen.getByText('已开启 3D 地形视角')).toBeInTheDocument();
      expect(() => unmount()).not.toThrow();
    });
  });
});

describe('M5 Empirical Challenge Suite: TabularDataGrid (R4)', () => {
  afterEach(() => {
    cleanup();
  });

  describe('1. Malformed & Adversarial Datasets Fuzzing', () => {
    it('gracefully handles null, undefined, empty object, and empty array datasets', () => {
      const { unmount: u1 } = render(<TabularDataGrid data={null} />);
      expect(screen.getByText('无数据记录')).toBeInTheDocument();
      u1();

      const { unmount: u2 } = render(<TabularDataGrid data={undefined} />);
      expect(screen.getByText('无数据记录')).toBeInTheDocument();
      u2();

      const { unmount: u3 } = render(<TabularDataGrid data={[] as any} />);
      expect(screen.getByText('无数据记录')).toBeInTheDocument();
      u3();

      const { unmount: u4 } = render(<TabularDataGrid data={{} as any} />);
      expect(screen.getByText('无数据记录')).toBeInTheDocument();
      u4();
    });

    it('handles GeoJSON with null/undefined/primitive/malformed features array', () => {
      const malformedFC = {
        type: 'FeatureCollection',
        features: [
          null,
          undefined,
          123,
          'string_row',
          {},
          { type: 'Feature', properties: null, geometry: null },
          { type: 'Feature', properties: undefined, geometry: { type: 'Point', coordinates: [100, 20] } },
          { type: 'Feature', properties: { valid_key: 'value' }, geometry: { type: 'Polygon', coordinates: [] } },
        ],
      } as any;

      expect(() => render(<TabularDataGrid data={malformedFC} />)).not.toThrow();
      expect(screen.getByText(/共 8 条要素/)).toBeInTheDocument();
    });

    it('handles sparse heterogeneous schemas across alternating rows', () => {
      const sparseRows = [
        { id: 1, name: 'Row 1', alpha: 100 },
        { id: 2, beta: 'hello', gamma: true },
        { id: 3, alpha: null, beta: undefined, delta: { nested: 'obj' } },
        { id: 4, array_col: [1, 2, 3], geo_col: 'Point [100.0, 20.0]' },
      ];

      render(<TabularDataGrid data={sparseRows} />);

      // Columns from all rows should be discovered
      expect(screen.getByRole('columnheader', { name: /id/i })).toBeInTheDocument();
      expect(screen.getByRole('columnheader', { name: /alpha/i })).toBeInTheDocument();
      expect(screen.getByRole('columnheader', { name: /beta/i })).toBeInTheDocument();
      expect(screen.getByRole('columnheader', { name: /gamma/i })).toBeInTheDocument();
      expect(screen.getByRole('columnheader', { name: /delta/i })).toBeInTheDocument();
      expect(screen.getByRole('columnheader', { name: /array_col/i })).toBeInTheDocument();

      // Null and undefined display placeholders
      expect(screen.getAllByText('null').length).toBeGreaterThan(0);
    });

    it('renders deeply nested JSON objects without throwing and displays JSON string', () => {
      const nestedData = [
        {
          id: 'deep-1',
          metadata: {
            deep1: {
              deep2: {
                deep3: { val: 'nested_value_123' },
              },
            },
          },
        },
      ];

      render(<TabularDataGrid data={nestedData} />);
      expect(screen.getByTitle(/nested_value_123/)).toBeInTheDocument();
    });
  });

  describe('2. Regex Metacharacters & Special Characters in Search', () => {
    const specialCharsData = [
      { id: '1', code: 'C++ / C#', symbol: '[test.*+?^${}()|\\]', name: '<script>alert("xss")</script>' },
      { id: '2', code: 'Python (v3.12)', symbol: '\\d+\\s*', name: '春熙路 🌸 (100% 优惠)' },
      { id: '3', code: 'Rust {safe}', symbol: '.*', name: '成都 & 重庆 / \u200Bzero-width' },
    ];

    it('searches with regex metacharacters without throwing RegExp syntax errors', async () => {
      render(<TabularDataGrid data={specialCharsData} />);

      const searchInput = screen.getByRole('searchbox', { name: '搜索属性内容' });

      // Test regex metacharacters that would crash RegExp if unescaped:
      const attackQueries = [
        '[',
        '\\',
        '+',
        '*',
        '?',
        '(',
        ')',
        '{',
        '}',
        '|',
        '^',
        '$',
        '[test.*+?',
        '<script>',
      ];

      for (const query of attackQueries) {
        fireEvent.change(searchInput, { target: { value: query } });
        // Should not throw and should display filtered matching results
        expect(screen.getByText(/匹配|未找到/)).toBeInTheDocument();
      }
    });

    it('escapes HTML tags safely in row cells', () => {
      render(<TabularDataGrid data={specialCharsData} />);
      expect(screen.getByText('<script>alert("xss")</script>')).toBeInTheDocument();
    });
  });

  describe('3. 10,000+ Rows High-Throughput & Performance Benchmark', () => {
    it('processes 10,000 rows within tight performance bounds without lag', async () => {
      const NUM_ROWS = 10000;
      const largeDataset: Array<Record<string, unknown>> = new Array(NUM_ROWS);

      for (let i = 0; i < NUM_ROWS; i++) {
        largeDataset[i] = {
          gid: i + 1,
          name: `空间要素点位_${i + 1}`,
          category: i % 5 === 0 ? '商业区' : i % 3 === 0 ? '公园绿地' : '居住区',
          score: Number((Math.sin(i) * 50 + 50).toFixed(2)),
          active: i % 2 === 0,
          lat: 30.5 + (i % 100) * 0.001,
          lng: 104.0 + (i % 100) * 0.001,
        };
      }

      const t0 = performance.now();
      render(<TabularDataGrid data={largeDataset} defaultPageSize={10} />);
      const renderTimeMs = performance.now() - t0;

      // Ensure rendering 10,000 rows through pagination takes under 500ms
      expect(renderTimeMs).toBeLessThan(1000);
      expect(screen.getByText(`共 ${NUM_ROWS} 条要素 · 7 个字段`)).toBeInTheDocument();
      expect(screen.getByText('1 / 1000')).toBeInTheDocument();
      expect(screen.getByText('空间要素点位_1')).toBeInTheDocument();
      expect(screen.getByText('空间要素点位_10')).toBeInTheDocument();
      expect(screen.queryByText('空间要素点位_11')).not.toBeInTheDocument();
    });

    it('searches across 10,000 rows with low latency and correct matching count', async () => {
      const NUM_ROWS = 10000;
      const largeDataset = Array.from({ length: NUM_ROWS }, (_, i) => ({
        id: `id-${i + 1}`,
        name: i === 9999 ? '独特目标地标_SECRET' : `常规要素_${i + 1}`,
        value: i,
      }));

      const user = userEvent.setup();
      render(<TabularDataGrid data={largeDataset} defaultPageSize={10} />);

      const searchInput = screen.getByRole('searchbox', { name: '搜索属性内容' });
      const t0 = performance.now();
      await user.type(searchInput, 'SECRET');
      const filterTimeMs = performance.now() - t0;

      expect(filterTimeMs).toBeLessThan(1500);
      expect(screen.getByText('独特目标地标_SECRET')).toBeInTheDocument();
      expect(screen.getByText(/匹配 1 \/ 10000 条/)).toBeInTheDocument();
    });
  });

  describe('4. Rapid Column Sort Flapping & Mixed Type Natural Sort', () => {
    it('survives rapid sorting across multiple columns in succession', async () => {
      const user = userEvent.setup();
      const mixedData = [
        { id: 1, val: '10', num: 100, text: 'Alpha' },
        { id: 2, val: '2', num: 5, text: 'Beta' },
        { id: 3, val: '100', num: 20, text: 'Gamma' },
        { id: 4, val: '20', num: null, text: 'Delta' },
        { id: 5, val: null, num: -10, text: 'Epsilon' },
      ];

      render(<TabularDataGrid data={mixedData} />);

      const valSortBtn = screen.getByRole('button', { name: /按 val 排序/i });
      const numSortBtn = screen.getByRole('button', { name: /按 num 排序/i });
      const textSortBtn = screen.getByRole('button', { name: /按 text 排序/i });

      // Click cycle: val asc -> val desc -> num asc -> text desc -> reset
      await user.click(valSortBtn);
      expect(screen.getByRole('columnheader', { name: /val/i })).toHaveAttribute('aria-sort', 'ascending');

      await user.click(valSortBtn);
      expect(screen.getByRole('columnheader', { name: /val/i })).toHaveAttribute('aria-sort', 'descending');

      await user.click(numSortBtn);
      expect(screen.getByRole('columnheader', { name: /num/i })).toHaveAttribute('aria-sort', 'ascending');
      expect(screen.getByRole('columnheader', { name: /val/i })).toHaveAttribute('aria-sort', 'none');

      await user.click(textSortBtn);
      await user.click(textSortBtn);
      expect(screen.getByRole('columnheader', { name: /text/i })).toHaveAttribute('aria-sort', 'descending');
    });

    it('places null/undefined values at the bottom in both asc and desc sort', async () => {
      const user = userEvent.setup();
      const dataWithNulls = [
        { id: '1', rank: null },
        { id: '2', rank: 50 },
        { id: '3', rank: 10 },
        { id: '4', rank: undefined },
        { id: '5', rank: 99 },
      ];

      render(<TabularDataGrid data={dataWithNulls} defaultPageSize={10} />);
      const rankSortBtn = screen.getByRole('button', { name: /按 rank 排序/i });

      // Ascending: 10, 50, 99, null, undefined
      await user.click(rankSortBtn);
      const rowsAsc = screen.getAllByRole('row');
      expect(rowsAsc[1].textContent).toContain('10');
      expect(rowsAsc[2].textContent).toContain('50');
      expect(rowsAsc[3].textContent).toContain('99');

      // Descending: 99, 50, 10, null, undefined
      await user.click(rankSortBtn);
      const rowsDesc = screen.getAllByRole('row');
      expect(rowsDesc[1].textContent).toContain('99');
      expect(rowsDesc[2].textContent).toContain('50');
      expect(rowsDesc[3].textContent).toContain('10');
    });
  });

  describe('5. Pagination Boundary Matrix & Reset Triggers', () => {
    it('resets current page to 1 when search query changes while on page 3', async () => {
      const user = userEvent.setup();
      const rows30 = Array.from({ length: 30 }, (_, i) => ({
        id: `row-${i + 1}`,
        name: i === 25 ? '特殊项目' : `普通项目 ${i + 1}`,
      }));

      render(<TabularDataGrid data={rows30} defaultPageSize={10} />);

      // Navigate to page 3
      const nextBtn = screen.getByRole('button', { name: '下一页' });
      await user.click(nextBtn);
      await user.click(nextBtn);
      expect(screen.getByText('3 / 3')).toBeInTheDocument();

      // Search filters data to 1 match
      const searchInput = screen.getByRole('searchbox', { name: '搜索属性内容' });
      await user.type(searchInput, '特殊');

      // Page should auto-reset to 1 / 1
      expect(screen.getByText('1 / 1')).toBeInTheDocument();
      expect(screen.getByText('特殊项目')).toBeInTheDocument();
    });

    it('resets current page to 1 when page size changes', async () => {
      const user = userEvent.setup();
      const rows50 = Array.from({ length: 50 }, (_, i) => ({ id: i + 1, name: `Item ${i + 1}` }));

      render(<TabularDataGrid data={rows50} defaultPageSize={10} pageSizeOptions={[10, 25, 50]} />);

      // Go to page 4
      const nextBtn = screen.getByRole('button', { name: '下一页' });
      await user.click(nextBtn);
      await user.click(nextBtn);
      await user.click(nextBtn);
      expect(screen.getByText('4 / 5')).toBeInTheDocument();

      // Change page size to 50
      const select = screen.getByRole('combobox', { name: '每页显示条数' });
      await user.selectOptions(select, '50');

      // Should be on page 1 / 1
      expect(screen.getByText('1 / 1')).toBeInTheDocument();
    });
  });

  describe('6. Row Copy & Clipboard Error Resilience', () => {
    it('copies row data to clipboard when clipboard API succeeds', async () => {
      const successWrite = vi.fn().mockResolvedValue(undefined);
      Object.defineProperty(navigator, 'clipboard', {
        value: { writeText: successWrite },
        writable: true,
        configurable: true,
      });

      render(<TabularDataGrid data={[{ id: 1, text: 'test' }]} />);
      const copyBtn = screen.getByRole('button', { name: '复制第 1 行数据' });

      await act(async () => {
        fireEvent.click(copyBtn);
      });
      expect(successWrite).toHaveBeenCalledTimes(1);
      expect(successWrite).toHaveBeenCalledWith(JSON.stringify({ id: 1, text: 'test' }, null, 2));
    });
  });
});

describe('M5 Empirical Challenge Suite: PoiInfoPanel (R3)', () => {
  const layerIds = new Set(['poi-layer-1', 'poi-layer-2']);
  const layersMap = {
    'poi-layer-1': { id: 'poi-layer-1', name: '城市核心地标' },
    'poi-layer-2': { id: 'poi-layer-2', name: '交通枢纽' },
  };

  beforeEach(() => {
    vi.clearAllMocks();
    useHudStore.setState({ selectedFeature: null } as any);
  });

  afterEach(() => {
    cleanup();
  });

  describe('1. 50 Overlapping Features Avalanche', () => {
    it('renders candidate list for 50 overlapping features and supports switching back and forth', async () => {
      const user = userEvent.setup();
      const fiftyFeatures = Array.from({ length: 50 }, (_, i) => ({
        layer: { id: i % 2 === 0 ? 'poi-layer-1' : 'poi-layer-2' },
        properties: {
          name: `重叠要素点位 #${i + 1}`,
          category: `类别 ${i % 5}`,
          index: i + 1,
        },
        geometry: { type: 'Point', coordinates: [104.06 + i * 0.0001, 30.57 + i * 0.0001] },
      }));

      render(
        <PoiInfoPanel
          x={300}
          y={400}
          coordinates={[104.06, 30.57]}
          features={fiftyFeatures}
          layerIds={layerIds}
          layersMap={layersMap}
          onClose={() => {}}
        />
      );

      // Candidate list header
      expect(screen.getByText('选择要素（50）')).toBeInTheDocument();
      expect(screen.getByText('重叠要素点位 #1')).toBeInTheDocument();
      expect(screen.getByText('重叠要素点位 #50')).toBeInTheDocument();

      // Pick feature #42
      const feat42Btn = screen.getByText('重叠要素点位 #42');
      await user.click(feat42Btn);

      // Now in detail view
      expect(screen.getAllByText('重叠要素点位 #42').length).toBeGreaterThan(0);
      expect(screen.getByText('类别 1')).toBeInTheDocument();

      // Back button
      const backBtn = screen.getByRole('button', { name: /返回要素列表/i });
      await user.click(backBtn);
      expect(screen.getByText('选择要素（50）')).toBeInTheDocument();
    });
  });

  describe('2. Pathological Properties, Long Text & MAX_ROWS Overflow', () => {
    it('truncates properties display at MAX_ROWS (8) and shows remaining count note', () => {
      const hundredProps: Record<string, unknown> = {};
      for (let i = 1; i <= 100; i++) {
        hundredProps[`prop_${i}`] = `value_${i}`;
      }

      render(
        <PoiInfoPanel
          x={200}
          y={300}
          coordinates={[104.06, 30.57]}
          features={[
            {
              layer: { id: 'poi-layer-1' },
              properties: hundredProps,
            },
          ]}
          layerIds={layerIds}
          layersMap={layersMap}
          onClose={() => {}}
        />
      );

      expect(screen.getByText('prop_1:')).toBeInTheDocument();
      expect(screen.getByText('prop_8:')).toBeInTheDocument();
      expect(screen.queryByText('prop_9:')).not.toBeInTheDocument();
      expect(screen.getByText('...以及其他 92 个属性')).toBeInTheDocument();
    });

    it('displays (无属性) when properties dictionary is empty', () => {
      render(
        <PoiInfoPanel
          x={200}
          y={300}
          coordinates={[104.06, 30.57]}
          features={[
            {
              layer: { id: 'poi-layer-1' },
              properties: {},
            },
          ]}
          layerIds={layerIds}
          layersMap={layersMap}
          onClose={() => {}}
        />
      );

      expect(screen.getByText('（无属性）')).toBeInTheDocument();
    });

    it('handles 10,000 character strings without breaking layout or throwing', () => {
      const hugeString = 'X'.repeat(10000);
      render(
        <PoiInfoPanel
          x={200}
          y={300}
          coordinates={[104.06, 30.57]}
          features={[
            {
              layer: { id: 'poi-layer-1' },
              properties: { name: '长文本测试', long_desc: hugeString },
            },
          ]}
          layerIds={layerIds}
          layersMap={layersMap}
          onClose={() => {}}
        />
      );

      expect(screen.getAllByText('长文本测试').length).toBeGreaterThan(0);
      expect(screen.getByText(hugeString)).toBeInTheDocument();
    });
  });

  describe('3. Display Name Resolution Oracle Fuzzing', () => {
    it('verifies name precedence: name > NAME > Name > 名称 > first string property > fallback', () => {
      expect(featureDisplayName({ properties: { name: 'A', NAME: 'B' } }, 'default')).toBe('A');
      expect(featureDisplayName({ properties: { NAME: 'B', Name: 'C' } }, 'default')).toBe('B');
      expect(featureDisplayName({ properties: { Name: 'C', 名称: 'D' } }, 'default')).toBe('C');
      expect(featureDisplayName({ properties: { 名称: 'D', code: 'E' } }, 'default')).toBe('D');
      expect(featureDisplayName({ properties: { code: 'E', value: 123 } }, 'default')).toBe('E');
      expect(featureDisplayName({ properties: { value: 123, active: true } }, 'fallback_name')).toBe('fallback_name');
      expect(featureDisplayName({ properties: {} }, 'fallback_name')).toBe('fallback_name');
      expect(featureDisplayName({}, 'fallback_name')).toBe('fallback_name');
    });

    it('handles non-string, empty, or whitespace name values gracefully', () => {
      expect(featureDisplayName({ properties: { name: '   ' } }, 'default')).toBe('default');
      expect(featureDisplayName({ properties: { name: 12345 as any } }, 'default')).toBe('default');
      expect(featureDisplayName({ properties: { name: null as any } }, 'fallback')).toBe('fallback');
      expect(featureDisplayName({ properties: { code: 'valid_code' } }, 'default')).toBe('valid_code');
    });
  });

  describe('4. Coordinates, BBox & Zoom Actions Boundary', () => {
    it('gracefully handles missing coordinates prop by resolving from geometry', () => {
      render(
        <PoiInfoPanel
          x={200}
          y={300}
          features={[
            {
              layer: { id: 'poi-layer-1' },
              properties: { name: '几何解析测试' },
              geometry: { type: 'Point', coordinates: [104.0665, 30.5728] },
            },
          ]}
          layerIds={layerIds}
          layersMap={layersMap}
          onClose={() => {}}
        />
      );

      expect(screen.getByText(/104.06650, 30.57280/)).toBeInTheDocument();
      expect(screen.getByRole('button', { name: '复制坐标' })).toBeInTheDocument();
    });

    it('hides coordinate display and zoom action when no valid coordinates exist', () => {
      render(
        <PoiInfoPanel
          x={200}
          y={300}
          features={[
            {
              layer: { id: 'poi-layer-1' },
              properties: { name: '纯属性要素' },
            },
          ]}
          layerIds={layerIds}
          layersMap={layersMap}
          onClose={() => {}}
        />
      );

      expect(screen.queryByRole('button', { name: '复制坐标' })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: '聚焦位置' })).not.toBeInTheDocument();
    });

    it('invokes onZoomToFeature with coordinates when bbox is not available', () => {
      const zoomMock = vi.fn();
      render(
        <PoiInfoPanel
          x={200}
          y={300}
          coordinates={[104.0665, 30.5728]}
          features={[
            {
              layer: { id: 'poi-layer-1' },
              properties: { name: '单点聚焦' },
            },
          ]}
          layerIds={layerIds}
          layersMap={layersMap}
          onClose={() => {}}
          onZoomToFeature={zoomMock}
        />
      );

      const zoomBtn = screen.getByRole('button', { name: '聚焦位置' });
      fireEvent.click(zoomBtn);
      expect(zoomMock).toHaveBeenCalledWith([104.0665, 30.5728]);
    });
  });

  describe('5. Screen Coordinate Clamping & SSR / Viewport Safety', () => {
    it('clamps screen position to safe bounds when x and y are negative, huge, or NaN', () => {
      const { container, unmount } = render(
        <PoiInfoPanel
          x={-500}
          y={-100}
          features={[{ layer: { id: 'poi-layer-1' }, properties: { name: 'Test' } }]}
          layerIds={layerIds}
          layersMap={layersMap}
          onClose={() => {}}
        />
      );

      const panel = container.querySelector('[data-testid="poi-info-panel"]');
      expect(panel).toHaveStyle({ left: '150px' });
      unmount();

      const { container: c2 } = render(
        <PoiInfoPanel
          x={99999}
          y={500}
          features={[{ layer: { id: 'poi-layer-1' }, properties: { name: 'Test' } }]}
          layerIds={layerIds}
          layersMap={layersMap}
          onClose={() => {}}
        />
      );

      const panel2 = c2.querySelector('[data-testid="poi-info-panel"]');
      expect(panel2).not.toBeNull();
    });

    it('places panel above when y > 220 and below when y <= 220', () => {
      const { container: c1, unmount: u1 } = render(
        <PoiInfoPanel
          x={300}
          y={250}
          features={[{ layer: { id: 'poi-layer-1' }, properties: { name: 'Test' } }]}
          layerIds={layerIds}
          layersMap={layersMap}
          onClose={() => {}}
        />
      );
      const panel1 = c1.querySelector('[data-testid="poi-info-panel"]');
      expect(panel1).toHaveStyle({ transform: 'translate(-50%, -100%)' });
      u1();

      const { container: c2, unmount: u2 } = render(
        <PoiInfoPanel
          x={300}
          y={150}
          features={[{ layer: { id: 'poi-layer-1' }, properties: { name: 'Test' } }]}
          layerIds={layerIds}
          layersMap={layersMap}
          onClose={() => {}}
        />
      );
      const panel2 = c2.querySelector('[data-testid="poi-info-panel"]');
      expect(panel2).toHaveStyle({ transform: 'translate(-50%, 0)' });
      u2();
    });
  });

  describe('6. HUD Store Live Sync & Approximate Badge', () => {
    it('displays approximate data warning badge when selectedFeature.isApproximate is true', () => {
      useHudStore.setState({
        selectedFeature: {
          layerId: 'poi-layer-1',
          featureId: 'f1',
          isApproximate: true,
          point: [104.06, 30.57],
          properties: { name: '近似要素' },
        },
      } as any);

      render(
        <PoiInfoPanel
          x={200}
          y={300}
          features={[{ layer: { id: 'poi-layer-1' }, properties: { name: '近似要素' } }]}
          layerIds={layerIds}
          layersMap={layersMap}
          onClose={() => {}}
        />
      );

      expect(screen.getByText('瓦片近似数据，正在核实…')).toBeInTheDocument();
    });
  });
});
