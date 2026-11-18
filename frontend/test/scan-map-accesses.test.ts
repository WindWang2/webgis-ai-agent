import ts from 'typescript';
import { describe, expect, it } from 'vitest';
import { scanMapAccesses } from './helpers/scan-map-accesses';

describe('MapLibre mock surface scanner', () => {
  it('ignores translation keys, paths, comments, template text and JSX text', () => {
    const source = [
      `t('map.futureTranslation.title');`,
      `const path = "map.png";`,
      '// map.commentedCall()',
      '/* map.anotherComment */',
      'const text = `map.templateText ${map.getZoom()}`;',
      'const label = <span>map.jsxText {map.getPitch()}</span>;',
    ].join('\n');
    expect(scanMapAccesses(source)).toEqual([
      { name: 'getZoom', line: 5, kind: 'call' },
      { name: 'getPitch', line: 6, kind: 'call' },
    ]);
  });

  it('excludes native Map fields without dropping MapLibre fields in other classes', () => {
    expect(scanMapAccesses(`
      class FrameCache {
        private map = new Map<string, number>();
        size() { return this.map.size; }
        get(key: string) { return this.map.get(key); }
        clear() { this.map.clear(); }
      }
      class SceneManager {
        private map: MaplibreMap;
        repaint() { this.map.triggerRepaint(); }
        elevation() { this.map?.queryTerrainElevation?.([0, 0]); }
      }
      ctx.map.getLayer('layer');
      map.getSource('layer');
    `)).toEqual([
      { name: 'triggerRepaint', line: 10, kind: 'call' },
      { name: 'queryTerrainElevation', line: 11, kind: 'call' },
      { name: 'getLayer', line: 13, kind: 'call' },
      { name: 'getSource', line: 14, kind: 'call' },
    ]);
  });

  it('does not exclude fields constructed with an imported Map class', () => {
    expect(scanMapAccesses(`
      import { Map } from 'maplibre-gl';
      class SceneManager {
        private map = new Map();
        repaint() { this.map.futureMethod(); }
      }
    `)).toEqual([{ name: 'futureMethod', line: 5, kind: 'call' }]);
  });

  it('finds calls, bare accesses, optional chains and literal computed members', () => {
    expect(scanMapAccesses([
      'map.addLayer({});',
      "typeof map.hasImage === 'function';",
      'const getter = map.getLayoutProperty;',
      'map?.getSource?.("layer");',
      'map.getStyle /* comment */ ();',
      "map['queryRenderedFeatures']();",
      'map?.["isStyleLoaded"]?.();',
      'map.dragPan.disable();',
    ].join('\n'))).toEqual([
      { name: 'addLayer', line: 1, kind: 'call' },
      { name: 'hasImage', line: 2, kind: 'access' },
      { name: 'getLayoutProperty', line: 3, kind: 'access' },
      { name: 'getSource', line: 4, kind: 'call' },
      { name: 'getStyle', line: 5, kind: 'call' },
      { name: 'queryRenderedFeatures', line: 6, kind: 'call' },
      { name: 'isStyleLoaded', line: 7, kind: 'call' },
      { name: 'dragPan', line: 8, kind: 'access' },
    ]);
  });

  it('still reports unknown methods, including names previously whitelisted as text', () => {
    expect(scanMapAccesses('map.futureMethod(); map.toolbar();')).toEqual([
      { name: 'futureMethod', line: 1, kind: 'call' },
      { name: 'toolbar', line: 1, kind: 'call' },
    ]);
  });
});

// Array-valued fields named map are not MapLibre maps; unknown receivers remain guarded.
it('uses proven array types without suppressing unknown MapLibre methods', () => {
  const name = '/virtual/map-surface.ts';
  const source = `interface Binding { map: string[] }
declare const binding: Binding;
binding.map.find(() => true);
binding.map.slice();
binding.map.length;
declare const ctx: { map: { futureMethod(): void } };
ctx.map.futureMethod();`;
  const options = { target: ts.ScriptTarget.ES2022, types: [] };
  const host = ts.createCompilerHost(options);
  const getSource = host.getSourceFile.bind(host);
  host.getSourceFile = (file, version, onError, fresh) => file === name
    ? ts.createSourceFile(file, source, version, true)
    : getSource(file, version, onError, fresh);
  const program = ts.createProgram([name], options, host);
  expect(scanMapAccesses(source, name, program)).toEqual([
    { name: 'futureMethod', line: 7, kind: 'call' },
  ]);
});

it('honors positive array guards without suppressing the else or unguarded object branch', () => {
  const source = `if (cb && Array.isArray(cb.map) && cb.map.length) { cb.map.slice(); }
else { cb.map.futureMethod(); }
cb.map.unknownMethod();`;
  expect(scanMapAccesses(source)).toEqual([
    { name: 'futureMethod', line: 2, kind: 'call' },
    { name: 'unknownMethod', line: 3, kind: 'call' },
  ]);
});

it('does not mistake a shadowed Array or an overwritten guarded field for proof', () => {
  expect(scanMapAccesses('const Array = { isArray: () => true }; if (Array.isArray(ctx.map)) { ctx.map.futureMethod(); }'))
    .toEqual([{ name: 'futureMethod', line: 1, kind: 'call' }]);
  expect(scanMapAccesses('if (Array.isArray(cb.map)) { cb.map = replacement; cb.map.futureMethod(); }'))
    .toEqual([{ name: 'futureMethod', line: 1, kind: 'call' }]);
});
