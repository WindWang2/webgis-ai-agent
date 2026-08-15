/**
 * Coverage meta-test for the shared MapLibre mock — issue #404 acceptance
 * criterion.
 *
 * The mock used to be a closed object literal: any `map.<method>(...)` the
 * application code calls that was NOT in the literal TypeError'd at runtime.
 * Tests never walked those paths (they crashed), so entire feature branches —
 * style-loaded gating (runtime.ts), rendered-feature queries (map-panel,
 * map-kit/state), bounds/viewport reading (map-panel, runtime-evidence),
 * canvas export (map-kit/exporter), image bookkeeping (map-kit/renderer), DPI
 * management, controls and terrain — ran with zero coverage.
 *
 * This test statically scans frontend/lib + frontend/components (non-test
 * sources) for every `map.<ident>` access (call sites and bare accesses, e.g.
 * `typeof map.hasImage === 'function'` or passing `map.getLayoutProperty` as a
 * value), and asserts the identifier exists as a function on the shared mock.
 * The scan is deliberately a cheap regex over source text (per issue #404): it
 * fails loudly the day someone calls a new MapLibre method without adding it
 * to the mock, and it is trivially auditable.
 *
 * All map receivers in the app are named `map` (function params, or
 * `mapRef.current?.getMap()` / `mapInstance.getMap()` results), so the regex
 * `\bmap\.` captures the full runtime surface. False positives from the word
 * boundary (e.g. `"map.png"` output paths) are covered by NON_API_WHITELIST
 * below — every entry must state why it is not a MapLibre member.
 */
import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { makeMockMaplibreMap } from './__mocks__/maplibre-map';

const FRONTEND_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
// Application code only — tests may use their own stubs; the contract this
// meta-test guards is the app's runtime surface, not test scaffolding.
const SCAN_DIRS = ['lib', 'components'];

/**
 * Identifiers the scan can match that are NOT MapLibre members. Every entry
 * must explain why it is exempt — adding entries without a real reason is how
 * mock drift sneaks back in.
 */
const NON_API_WHITELIST: Record<string, string> = {
  // Filesystem output path string literal — `path.join(args.outDir, "map.png")`
  // in lib/mapspec-compiler/runtime-validate.ts. The word boundary before
  // `map` holds inside the quoted string, so the access regex matches it;
  // it is not a MapLibre member (unlike `map.getCanvas()` etc.).
  png: 'string literal "map.png" (output file path) in lib/mapspec-compiler/runtime-validate.ts',
};

const ACCESS_RE = /\bmap\.([A-Za-z_][A-Za-z0-9_]*)/g;

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
          ACCESS_RE.lastIndex = 0;
          let m: RegExpExecArray | null;
          while ((m = ACCESS_RE.exec(src)) !== null) {
            const name = m[1];
            const after = src.slice(ACCESS_RE.lastIndex);
            const kind: AccessSite['kind'] =
              after.startsWith('(') || after.startsWith('?.(') ? 'call' : 'access';
            const line = src.slice(0, m.index).split('\n').length;
            add(name, { file: full, line, kind });
          }
        }
      }
    };
    walk(base);
  }
  return found;
}

describe('shared MapLibre mock covers the app-level MapLibre surface (#404)', () => {
  it('every map.<method> the application source touches exists on the mock', () => {
    const map = makeMockMaplibreMap();
    const accesses = collectAccesses();

    // Sanity: the scan must actually find the known surface, otherwise the
    // meta-test silently guards nothing.
    expect(accesses.size).toBeGreaterThan(10);
    // (forEach rather than for-of: the test tsconfig targets ES3, where only
    // array iteration is allowed without downlevelIteration.)
    (['addLayer', 'getSource', 'queryRenderedFeatures', 'isStyleLoaded'] as const).forEach(
      (mustFind) => {
        expect(accesses.has(mustFind), `scan must still see ${mustFind}`).toBe(true);
      },
    );

    const missing: string[] = [];
    accesses.forEach((sites, name) => {
      if (NON_API_WHITELIST[name]) return;
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
