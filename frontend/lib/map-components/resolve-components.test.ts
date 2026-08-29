/**
 * Shared component resolution（ADR-0081 Export Parity）—— live 与 export
 * 共用解析层的契约锁定。
 */
import { describe, expect, it } from 'vitest';
import {
  DEFAULT_COMPONENT_ANCHOR,
  findEnabled,
  findOfType,
  resolveMapComponent,
  resolveMapComponents,
  scaleFloatingRect,
} from './resolve-components';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';

function comp(partial: Partial<MapSpecComponent> & { id: string; type: MapSpecComponent['type'] }): MapSpecComponent {
  return partial as MapSpecComponent;
}

describe('resolveMapComponent — anchor 解析优先级', () => {
  it('placement.mode=anchor 的 anchor 优先', () => {
    const c = resolveMapComponent(
      comp({ id: 't', type: 'title', position: 'top-left', placement: { mode: 'anchor', anchor: 'bottom-center' } }),
    );
    expect(c.anchor).toBe('bottom-center');
  });

  it('无 placement 时回退旧 position 字段', () => {
    const c = resolveMapComponent(comp({ id: 't', type: 'title', position: 'top-left' }));
    expect(c.anchor).toBe('top-left');
  });

  it('position 缺省时回退类型默认槽位（与 live DEFAULT_POSITION 同表）', () => {
    expect(resolveMapComponent(comp({ id: 't', type: 'title' })).anchor).toBe('top-center');
    expect(resolveMapComponent(comp({ id: 's', type: 'scale_bar' })).anchor).toBe('bottom-right');
    expect(resolveMapComponent(comp({ id: 'l', type: 'legend' })).anchor).toBe('bottom-left');
    expect(DEFAULT_COMPONENT_ANCHOR['north_arrow']).toBe('top-right');
  });

  it('非法 anchor 字符串回退类型默认', () => {
    const c = resolveMapComponent(
      comp({ id: 't', type: 'title', placement: { mode: 'anchor', anchor: 'middle' } }),
    );
    expect(c.anchor).toBe('top-center');
  });
});

describe('resolveMapComponent — floating/enabled/text/variant', () => {
  it('floating 坐标完整保留（视口像素语义）', () => {
    const c = resolveMapComponent(
      comp({
        id: 'chart', type: 'chart_panel',
        placement: { mode: 'floating', x: 120, y: 80, width: 320, height: 240, zIndex: 55 },
      }),
    );
    expect(c.floating).toBe(true);
    expect(c.floatingRect).toMatchObject({ x: 120, y: 80, width: 320, height: 240, zIndex: 55 });
  });

  it('enabled=false 保留原值（过滤归消费端）', () => {
    expect(resolveMapComponent(comp({ id: 't', type: 'title', enabled: false })).enabled).toBe(false);
    expect(resolveMapComponent(comp({ id: 't', type: 'title' })).enabled).toBe(true);
  });

  it('text 取 options.text；variant 取 options.variant > component.variant', () => {
    expect(resolveMapComponent(comp({ id: 't', type: 'title', options: { text: '成都小学' } })).text).toBe('成都小学');
    expect(
      resolveMapComponent(comp({ id: 'n', type: 'north_arrow', variant: 'glyph', options: { variant: 'compass' } })).variant,
    ).toBe('compass');
    expect(resolveMapComponent(comp({ id: 'n', type: 'north_arrow', variant: 'glyph' })).variant).toBe('glyph');
  });

  it('layerId 取 options.layerId（图例绑定）', () => {
    expect(resolveMapComponent(comp({ id: 'l', type: 'legend', options: { layerId: 'poi-main' } })).layerId).toBe('poi-main');
  });
});

describe('resolveMapComponents — 列表解析', () => {
  it('空/缺 spec 返回空数组', () => {
    expect(resolveMapComponents(null)).toEqual([]);
    expect(resolveMapComponents({})).toEqual([]);
    expect(resolveMapComponents({ layout: {} })).toEqual([]);
  });

  it('findEnabled / findOfType 区分在场与启用', () => {
    const list = resolveMapComponents({
      layout: {
        components: [
          comp({ id: 't', type: 'title', enabled: false, options: { text: 'x' } }),
          comp({ id: 's', type: 'scale_bar' }),
        ],
      },
    });
    expect(findEnabled(list, 'title')).toBeUndefined();
    expect(findOfType(list, 'title')?.id).toBe('t');
    expect(findEnabled(list, 'scale_bar')?.id).toBe('s');
  });
});

describe('scaleFloatingRect — 视口→画布换算', () => {
  it('按比例缩放（2x 画布）', () => {
    const out = scaleFloatingRect(
      { x: 100, y: 50, width: 200, height: 100 },
      { width: 800, height: 600 },
      { width: 1600, height: 1200 },
    );
    expect(out).toEqual({ x: 200, y: 100, width: 400, height: 200 });
  });

  it('1:1 视口（未知尺寸）不缩放', () => {
    const out = scaleFloatingRect(
      { x: 100, y: 50 },
      { width: 0, height: 0 },
      { width: 1600, height: 1200 },
    );
    expect(out).toEqual({ x: 100, y: 50 });
  });
});
