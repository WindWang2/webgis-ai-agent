/**
 * Coverage meta-test for the shared MapLibre mock — issue #404 acceptance
 * criterion.
 *
 * The mock used to be a closed object literal: any `map.<method>(...)` the
 * application code calls that was NOT in the literal TypeError'd at runtime.
 * Tests never walked those paths (they crashed), so entire feature branches —
 * style-loaded gating (runtime.ts), rendered-feature queries (map-panel),
 * bounds/viewport reading (map-panel, runtime-evidence),
 * canvas export (map-kit/exporter), image bookkeeping (map-kit/renderer), DPI
 * management, controls and terrain — ran with zero coverage.
 *
 * This test parses frontend/lib + frontend/components (non-test sources)
 * for member accesses on the application's `map` variables, including bare
 * method references and optional chains. Strings, comments, JSX text, and
 * native collection fields are not MapLibre accesses. Nested map receivers
 * such as `this.map` and `ctx.map` remain covered. This is a
 * syntax-level contract, not whole-program type analysis.
 */
import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { makeMockMaplibreMap } from './__mocks__/maplibre-map';
import { scanMapAccesses } from './helpers/scan-map-accesses';

const FRONTEND_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
// Application code only — tests may use their own stubs; the contract this
// meta-test guards is the app's runtime surface, not test scaffolding.
const SCAN_DIRS = ['lib', 'components'];

// MapLibre handlers are objects rather than methods. Unlike text exclusions,
// these are real API members and must also exist on the mock.
const HANDLER_MEMBERS = new Set([
  'dragPan', 'boxZoom', 'touchZoomRotate', 'touchPitch',
  'scrollZoom', 'dragRotate', 'tapZoom', 'keyboard', 'style',
]);

interface AccessSite {
  file: string;
  line: number;
  kind: 'call' | 'access';
}

function collectAccesses(): Map<string, AccessSite[]> {
  const found = new Map<string, AccessSite[]>();
  const add = (name: string, site: AccessSite) => {
    const list = found.get(name) ?? [];
    list.push(site);
    found.set(name, list);
  };

  for (const dir of SCAN_DIRS) {
    const base = path.join(FRONTEND_ROOT, dir);
    if (!fs.existsSync(base)) continue;
    const walk = (dirPath: string): void => {
      for (const entry of fs.readdirSync(dirPath, { withFileTypes: true })) {
        const full = path.join(dirPath, entry.name);
        if (entry.isDirectory()) {
          walk(full);
        } else if (
          /\.(ts|tsx)$/.test(entry.name) &&
          !/\.(test|spec)\.[cm]?[jt]sx?$/.test(entry.name)
        ) {
          const src = fs.readFileSync(full, 'utf8');
          for (const access of scanMapAccesses(src, full)) {
            add(access.name, { file: full, line: access.line, kind: access.kind });
          }
        }
      }
    };
    walk(base);
  }
  return found;
}

describe('shared MapLibre mock covers the app-level MapLibre surface (#404)', () => {
  it.each(['dragPan', 'scrollZoom', 'dragRotate', 'touchZoomRotate', 'tapZoom', 'keyboard'])(
    '%s models the idle interaction state used by camera commands',
    (name) => {
      const map = makeMockMaplibreMap();
      expect(map[name].isActive()).toBe(false);
      map[name].isActive.mockReturnValue(true);
      expect(map[name].isActive()).toBe(true);
    },
  );

  it('every map.<method> the application source touches exists on the mock', () => {
    const map = makeMockMaplibreMap();
    const accesses = collectAccesses();

    // Sanity: the scan must actually find the known surface, otherwise the
    // meta-test silently guards nothing.
    expect(accesses.size).toBeGreaterThan(10);
    (['addLayer', 'getSource', 'queryRenderedFeatures', 'isStyleLoaded'] as const).forEach(
      (mustFind) => {
        expect(accesses.has(mustFind), `scan must still see ${mustFind}`).toBe(true);
      },
    );

    // Keep class-owned MapLibre maps covered alongside the native cache field.
    for (const name of ['triggerRepaint', 'queryTerrainElevation']) {
      expect(accesses.get(name)?.some((site) =>
        path.relative(FRONTEND_ROOT, site.file) === 'lib/map-kit/scene3d/asset-manager.ts',
      ), `scan must still see this.map.${name} in the asset manager`).toBe(true);
    }

    const missing: string[] = [];
    accesses.forEach((sites, name) => {
      if (HANDLER_MEMBERS.has(name) && map[name] && typeof map[name] === 'object') return;
      if (typeof map[name] !== 'function') {
        const first = sites[0];
        missing.push(
          `${name} (${first.kind} at ${path.relative(FRONTEND_ROOT, first.file)}:${first.line})`,
        );
      }
    });
    expect(missing).toEqual([]);
  });
});
