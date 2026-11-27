/**
 * G40 live 投影回归：类别 icon-image 表达式的 imageId 必须与
 * ensureRegistered 的注册名同源（归一化 params，register.ts:103）。
 *
 * 若表达式用原始 params 算 id，类别规则省略 params 或键序不同 →
 * stableKey 分歧 → match 臂指向未注册图像 → 类别图标静默不渲染。
 * 不变式：表达式引用的每个图像都注册在本 map 实例上。
 */
import { beforeAll, describe, expect, it } from 'vitest';
import type { Map as MapLibreMap } from 'maplibre-gl';

import { projectSymbolAssetLayer } from './live-project';
import {
  CategoryBinding,
  SymbolAssetBinding,
  SymbolAssetDocument,
  normalizePointParams,
  pointImageId,
} from './contracts';
import { BUILTIN_DOCUMENTS } from './builtin-documents';

// test/setup.ts 的 canvas 桩不含 getImageData/scale —— rasterizePointSymbol
// 跑不通；补一个足以完成光栅化的 2d context 桩（只在本文件生效）。
beforeAll(() => {
  const contextStub = function () {
    return {
      getImageData: (_x: number, _y: number, w: number, h: number) => ({
        data: new Uint8ClampedArray(Math.max(1, w) * Math.max(1, h) * 4),
        width: w,
        height: h,
        colorSpace: 'srgb',
      }),
      scale: () => {},
      setTransform: () => {},
      globalAlpha: 1,
      beginPath: () => {},
      closePath: () => {},
      moveTo: () => {},
      lineTo: () => {},
      bezierCurveTo: () => {},
      arc: () => {},
      ellipse: () => {},
      rect: () => {},
      fill: () => {},
      stroke: () => {},
      fillRect: () => {},
      strokeRect: () => {},
      save: () => {},
      restore: () => {},
      translate: () => {},
      rotate: () => {},
      fillStyle: '',
      strokeStyle: '',
      lineWidth: 1,
      font: '',
      textAlign: 'left',
    } as unknown as CanvasRenderingContext2D;
  };
  HTMLCanvasElement.prototype.getContext = contextStub as unknown as typeof HTMLCanvasElement.prototype.getContext;
});

interface FakeMap {
  map: MapLibreMap;
  added: Set<string>;
}

function fakeMap(): FakeMap {
  const added = new Set<string>();
  const map = {
    hasImage: (id: string) => added.has(id),
    addImage: (id: string) => {
      added.add(id);
    },
  };
  return { map: map as unknown as MapLibreMap, added };
}

/** match 表达式里引用的 image id（typed 字面量键不是 cartoasset- 前缀）。 */
function iconImageIds(expr: unknown): string[] {
  return (expr as unknown[]).filter(
    (v): v is string => typeof v === 'string' && v.startsWith('cartoasset-'));
}

function registeredId(
  doc: SymbolAssetDocument,
  raw: Record<string, unknown>,
): string {
  return pointImageId(
    doc.content_digest,
    normalizePointParams(raw, doc) as unknown as Record<string, unknown>,
  );
}

function projectPoint(
  binding: SymbolAssetBinding,
  docs: Map<string, SymbolAssetDocument>,
): { ids: string[]; added: Set<string> } {
  const { map, added } = fakeMap();
  const out = projectSymbolAssetLayer(map, binding, docs);
  if ('failure' in out) {
    throw new Error(
      `unexpected projection failure: ${out.failure.code}: ${out.failure.message}`);
  }
  return { ids: iconImageIds(out.patch.layout['icon-image']), added };
}

const tri = BUILTIN_DOCUMENTS['cartoasset.builtin.triangle'];
const sq = BUILTIN_DOCUMENTS['cartoasset.builtin.square'];
const dia = BUILTIN_DOCUMENTS['cartoasset.builtin.diamond'];
const pointDocs = new Map<string, SymbolAssetDocument>([
  [tri.content_digest, tri],
  [sq.content_digest, sq],
  [dia.content_digest, dia],
]);

function mainBinding(
  cb: CategoryBinding,
  params?: Record<string, unknown>,
): SymbolAssetBinding {
  return {
    schema_version: 1,
    asset_id: 'cartoasset.builtin.triangle',
    revision: 1,
    content_digest: tri.content_digest,
    kind: 'point_symbol',
    ...(params ? { params } : {}),
    category_binding: cb,
  };
}

describe('projectSymbolAssetLayer：类别 icon-image 表达式与注册名同源', () => {
  it('类别规则省略 params：arm 命中已注册图像（非原始 {} 的 id）', () => {
    const { ids, added } = projectPoint(mainBinding({
      field: 'kind',
      key_type: 'number',
      map: [{
        key: 1, asset_id: 'cartoasset.builtin.square', revision: 1,
        content_digest: sq.content_digest,
      }],
      other_mode: 'keep_current',
    }), pointDocs);
    expect(ids.length).toBeGreaterThan(0);
    for (const id of ids) expect(added.has(id)).toBe(true);
    expect(ids).toContain(registeredId(sq, {}));
    expect(ids).not.toContain(pointImageId(sq.content_digest, {}));
  });

  it('类别规则 params 键序不同：arm 仍命中注册 id（stableKey 键序敏感）', () => {
    const raw = { opacity: 1, rotation_deg: 30, anchor: 'left', size_px: 20 };
    const { ids, added } = projectPoint(mainBinding({
      field: 'kind',
      key_type: 'number',
      map: [{
        key: 2, asset_id: 'cartoasset.builtin.square', revision: 1,
        content_digest: sq.content_digest, params: raw,
      }],
      other_mode: 'keep_current',
    }), pointDocs);
    for (const id of ids) expect(added.has(id)).toBe(true);
    expect(ids).toContain(registeredId(sq, raw));
    expect(ids).not.toContain(pointImageId(sq.content_digest, raw));
  });

  it('keep_current 回落臂：主绑定省略 params → 回落主绑定注册图像', () => {
    const { ids, added } = projectPoint(mainBinding({
      field: 'kind',
      key_type: 'number',
      map: [{
        key: 7, asset_id: 'cartoasset.builtin.square', revision: 1,
        content_digest: sq.content_digest, params: { size_px: 24 },
      }],
      other_mode: 'keep_current',
    }), pointDocs);
    for (const id of ids) expect(added.has(id)).toBe(true);
    expect(ids).toContain(registeredId(tri, {}));
    expect(ids).not.toContain(pointImageId(tri.content_digest, {}));
  });

  it('fallback_asset 回落臂：other 省略 params → 命中 other 注册图像', () => {
    const { ids, added } = projectPoint(mainBinding({
      field: 'kind',
      key_type: 'number',
      map: [{
        key: 3, asset_id: 'cartoasset.builtin.square', revision: 1,
        content_digest: sq.content_digest, params: { size_px: 20 },
      }],
      other_mode: 'fallback_asset',
      other_asset: 'cartoasset.builtin.diamond',
      other_revision: 1,
      other_content_digest: dia.content_digest,
    }), pointDocs);
    for (const id of ids) expect(added.has(id)).toBe(true);
    expect(ids).toContain(registeredId(dia, {}));
    expect(ids).not.toContain(pointImageId(dia.content_digest, {}));
  });

  it('doc.default_params 非内置默认：省略 params 的规则臂仍命中注册 id', () => {
    const custom: SymbolAssetDocument = {
      schema_version: 1,
      kind: 'point_symbol',
      viewbox: [24, 24],
      primitives: [],
      default_params: { size_px: 32, anchor: 'bottom', rotation_deg: 15 },
      content_digest: 'sha256:' + 'c'.repeat(64),
    };
    const docs = new Map<string, SymbolAssetDocument>([
      [tri.content_digest, tri],
      [custom.content_digest, custom],
    ]);
    const { ids, added } = projectPoint(mainBinding({
      field: 'kind',
      key_type: 'string',
      map: [{
        key: 'big', asset_id: 'custom', revision: 1,
        content_digest: custom.content_digest,
      }],
      other_mode: 'keep_current',
    }), docs);
    for (const id of ids) expect(added.has(id)).toBe(true);
    expect(ids).toContain(registeredId(custom, {}));
    expect(ids).not.toContain(pointImageId(custom.content_digest, {}));
  });

  it('回归护栏：无类别绑定时 icon-image 即主绑定注册图像', () => {
    const raw = { size_px: 18, anchor: 'center', rotation_deg: 90, opacity: 1 };
    const { map, added } = fakeMap();
    const out = projectSymbolAssetLayer(map, {
      schema_version: 1,
      asset_id: 'cartoasset.builtin.triangle',
      revision: 1,
      content_digest: tri.content_digest,
      kind: 'point_symbol',
      params: raw,
    }, pointDocs);
    if ('failure' in out) {
      throw new Error(`unexpected projection failure: ${out.failure.code}`);
    }
    const iconImage = out.patch.layout['icon-image'] as string;
    expect(iconImage).toBe(registeredId(tri, raw));
    expect(added.has(iconImage)).toBe(true);
  });
});

describe('projectSymbolAssetLayer：参数非法 → typed failure（不逃逸异常）', () => {
  it('点符号 params 越界 → failure（PARAM_OUT_OF_RANGE），不抛 SymbolAssetParamError', () => {
    const { map } = fakeMap();
    const out = projectSymbolAssetLayer(map, {
      schema_version: 1,
      asset_id: 'cartoasset.builtin.triangle',
      revision: 1,
      content_digest: tri.content_digest,
      kind: 'point_symbol',
      params: { size_px: 0 },
    }, pointDocs);
    expect('failure' in out).toBe(true);
    if ('failure' in out) {
      expect(out.failure.code).toBe('PARAM_OUT_OF_RANGE');
      expect(out.failure.message).toContain('size_px');
    }
  });

  it('面纹理 params 未知键 → failure（UNKNOWN_PARAM），不抛 SymbolAssetParamError', () => {
    const hatch = BUILTIN_DOCUMENTS['cartoasset.builtin.hatch'];
    const { map } = fakeMap();
    const out = projectSymbolAssetLayer(map, {
      schema_version: 1,
      asset_id: 'cartoasset.builtin.hatch',
      revision: 1,
      content_digest: hatch.content_digest,
      kind: 'area_texture',
      params: { bogus: 1 },
    }, new Map<string, SymbolAssetDocument>([[hatch.content_digest, hatch]]));
    expect('failure' in out).toBe(true);
    if ('failure' in out) {
      expect(out.failure.code).toBe('UNKNOWN_PARAM');
    }
  });
});
