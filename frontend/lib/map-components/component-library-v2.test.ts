/**
 * Component Library 2.0 — 前端场景测试（goal §20 A/C/G/H 前端侧）。
 *
 * - geo-anchor：live/export 共享投影（callout/inset 的单一语义源）；
 * - clampFloatingRect：小视口/导出画布的确定性 overflow 钳制（Scenario H）；
 * - buildExportChrome v2：图例族多实例数组 / inset 元素 / annotation
 *   callout+group 元素（Scenario A/C/G 的导出 parity 契约位）。
 */
import { describe, expect, it } from 'vitest';
import {
  anchorFractionInBounds,
  bboxToBounds,
  boundsFromCenterZoom,
} from '@/lib/map-components/geo-anchor';
import { clampFloatingRect } from '@/lib/map-components/resolve-layout';
import { buildExportChrome, type BuildExportChromeOptions } from '@/lib/map-kit/export-chrome';

const CHINA_BBOX = { west: 73, south: 18, east: 135, north: 54 };

describe('geo-anchor（共享投影语义）', () => {
  it('anchor 落在 bounds 内的比例正确（fx 东西向、fy 自南向北）', () => {
    const frac = anchorFractionInBounds([104, 36], CHINA_BBOX);
    expect(frac).not.toBeNull();
    expect(frac!.fx).toBeCloseTo((104 - 73) / (135 - 73), 10);
    expect(frac!.fy).toBeCloseTo((36 - 18) / (54 - 18), 10);
  });

  it('bounds 退化 → null（渲染端自弃，不虚构位置）', () => {
    expect(anchorFractionInBounds([104, 36], { west: 120, south: 0, east: 110, north: 10 })).toBeNull();
  });

  it('boundsFromCenterZoom 与 metersPerPixelAt 同源（512 tile 语义）', () => {
    const b = boundsFromCenterZoom({ lng: 104.06, lat: 30.57 }, 10, 1200, 800);
    expect(b).not.toBeNull();
    // zoom 10 @ lat 30.57：metersPerPixel ≈ 65.8（512 tile）→ 半宽 ≈ 39km
    // ≈ 0.354°，跨度 ≈ 0.709°
    const spanLng = b!.east - b!.west;
    expect(spanLng).toBeGreaterThan(0.6);
    expect(spanLng).toBeLessThan(0.85);
    // 纬度 cos 修正 → 纬度跨度小于经度跨度
    expect(b!.north - b!.south).toBeLessThan(spanLng);
  });

  it('bboxToBounds：无效/退化 bbox → null', () => {
    expect(bboxToBounds([97, 26, 108, 34])).toEqual({ west: 97, south: 26, east: 108, north: 34 });
    expect(bboxToBounds([108, 26, 97, 34])).toBeNull();
    expect(bboxToBounds('x')).toBeNull();
  });
});

describe('clampFloatingRect（Scenario H：确定性 overflow 钳制）', () => {
  it('拖出右/下边界的面板被夹回画布（保留 ≥96px 可见窗口）', () => {
    const clamped = clampFloatingRect(
      { x: 1100, y: 700, width: 320, height: 240 },
      { width: 1200, height: 800 },
    );
    expect(clamped.x).toBeLessThanOrEqual(1200 - 96);
    expect(clamped.y).toBeLessThanOrEqual(800 - 96);
  });

  it('负坐标夹回左/上边缘', () => {
    const clamped = clampFloatingRect({ x: -50, y: -50 }, { width: 1200, height: 800 });
    expect(clamped.x).toBe(8);
    expect(clamped.y).toBe(8);
  });

  it(' oversized 面板宽高被夹进画布', () => {
    const clamped = clampFloatingRect(
      { x: 8, y: 8, width: 2000, height: 1500 },
      { width: 1200, height: 800 },
    );
    expect(clamped.width).toBeLessThanOrEqual(1200 - 8 - 8);
    expect(clamped.height).toBeLessThanOrEqual(800 - 8 - 8);
  });

  it('确定性：同输入同输出', () => {
    const a = clampFloatingRect({ x: 999, y: 999, width: 320 }, { width: 1000, height: 700 });
    const b = clampFloatingRect({ x: 999, y: 999, width: 320 }, { width: 1000, height: 700 });
    expect(a).toEqual(b);
  });
});

// ── buildExportChrome v2 ─────────────────────────────────────────────────

const legendSpecByLayer = {
  'school-heatmap': {
    type: 'continuous',
    field: '密度',
    min: 0,
    max: 42,
    palette_colors: ['#0000ff', '#ff0000'],
    unit: '人/km²',
  },
  'district-choropleth': {
    type: 'graduated',
    field: '学校数',
    breaks: [0, 10, 20, 50],
    palette_colors: ['#edf8e9', '#bae4b3', '#74c476'],
  },
} as unknown as BuildExportChromeOptions['legendSpecsByLayer'];

const baseOpts = (
  components: unknown[],
  extra: Partial<BuildExportChromeOptions> = {},
): BuildExportChromeOptions => ({
  spec: { layout: { components: components as never } },
  viewport: { width: 1200, height: 800 },
  legendSpecsByLayer: legendSpecByLayer,
  ...extra,
});

describe('buildExportChrome v2（Scenario A/C/G 导出 parity）', () => {
  it('Scenario A：heatmap+choropleth 双图例 → colorbars+legends 数组各一条，绑定各自 legend_spec', async () => {
    const model = await buildExportChrome(baseOpts([
      { id: 'cb-main', type: 'continuous_colorbar', enabled: true, options: { layerId: 'school-heatmap' } },
      { id: 'lg-dist', type: 'legend', enabled: true, options: { layerId: 'district-choropleth' } },
    ]), { width: 2400, height: 1600 });
    expect(model.colorbars).toHaveLength(1);
    expect(model.legends).toHaveLength(1);
    expect(model.colorbars[0].legendSpec!.type).toBe('continuous');
    expect(model.legends[0].legendSpec!.type).toBe('graduated');
  });

  it('Scenario C：inset 组件 → insets 元素（bbox + 指示框 + 边界折线）', async () => {
    const model = await buildExportChrome(baseOpts([
      {
        id: 'inset-1', type: 'inset_map', enabled: true, variant: 'overview',
        options: {
          bbox: [97, 26, 108, 34],
          mainBbox: [103.9, 30.6, 104.2, 30.8],
          boundary: [[97, 30], [100, 33], [104, 34], [108, 30], [104, 26.5], [100, 26]],
          label: '中国范围',
        },
      },
    ]), { width: 2400, height: 1600 });
    expect(model.insets).toHaveLength(1);
    const inset = model.insets[0];
    expect(inset.insetBbox).toEqual({ west: 97, south: 26, east: 108, north: 34 });
    expect(inset.insetMainBbox).toEqual({ west: 103.9, south: 30.6, east: 104.2, north: 30.8 });
    expect(inset.insetBoundary).toHaveLength(6);
    expect(inset.text).toBe('中国范围');
  });

  it('Scenario C（自动指示框）：mainBbox 缺省 → viewportBounds 兜底', async () => {
    const model = await buildExportChrome(
      baseOpts(
        [{ id: 'inset-1', type: 'inset_map', enabled: true, options: { bbox: [97, 26, 108, 34] } }],
        { viewportBounds: { west: 103.5, south: 30.2, east: 104.5, north: 31.0 } },
      ),
      { width: 2400, height: 1600 },
    );
    expect(model.insets[0].insetMainBbox).toEqual({
      west: 103.5, south: 30.2, east: 104.5, north: 31.0,
    });
  });

  it('Scenario G：annotation callout/group 导出语义（anchorCoordinate/items 同链）', async () => {
    const model = await buildExportChrome(baseOpts([
      {
        id: 'callout-1', type: 'annotation', enabled: true,
        options: { variant: 'callout', text: '汶川震中 M8.0', anchor: [103.4, 31.0] },
      },
      {
        id: 'group-1', type: 'annotation', enabled: true, position: 'top-left',
        options: { variant: 'group', items: [
          { text: '映秀镇', anchor: [103.48, 31.06] },
          { text: '注记说明：烈度分布依据中国地震局通报' },
        ] },
      },
    ]), { width: 2400, height: 1600 });
    const annotations = model.panels.filter((p) => p.kind === 'annotation');
    expect(annotations).toHaveLength(2);
    const callout = annotations.find((p) => p.anchorCoordinate);
    expect(callout!.anchorCoordinate).toEqual([103.4, 31.0]);
    const group = annotations.find((p) => p.items);
    expect(group!.items).toHaveLength(2);
    expect(group!.items![0].anchor).toEqual([103.48, 31.06]);
    expect(group!.items![1].anchor).toBeUndefined();
  });

  it('无效 inset（缺 bbox）→ 自弃；disabled 图例 → 不导出', async () => {
    const model = await buildExportChrome(baseOpts([
      { id: 'inset-bad', type: 'inset_map', enabled: true, options: {} },
      { id: 'lg-off', type: 'legend', enabled: false, options: { layerId: 'district-choropleth' } },
    ]), { width: 2400, height: 1600 });
    expect(model.insets).toHaveLength(0);
    expect(model.legends).toHaveLength(0);
  });
});
