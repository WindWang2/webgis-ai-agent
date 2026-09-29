/**
 * C11 CartoIR contract corpus — 双侧共享 fixture 的前端消费面 +
 * contract_manifest.json 的前端漂移闸。
 *
 * fixture 与 backend（tests/cartography/test_carto_ir_corpus_v1.py）共用：
 * tests/cartography/carto_ir_corpus/*.json。backend 锁 parse 语义，本文件
 * 锁 compileMapSpec 产物 golden（zoom 门/降级决策）。
 */
import { describe, expect, it } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { resolve } from 'node:path';

// vitest 以 frontend/ 为 cwd（package.json 根）；corpus 在仓库级 tests/。
const REPO_ROOT = resolve(process.cwd(), '..');
import { compileMapSpec } from '@/lib/mapspec-compiler/compiler';
import type { MapSpec } from '@/lib/mapspec-compiler/types';
// 前端镜像常量（被 manifest 锁定的一方）
import {
  CONTRACT_BIVARIATE_CLASS_FIELD_DEFAULT,
  CONTRACT_BIVARIATE_MATRIX_SIZES,
  CONTRACT_COMPONENT_ABI_VERSION,
  CONTRACT_DATA_BINDING_FIELD_TYPES,
  CONTRACT_LATEST_MAPSPEC_VERSION,
  CONTRACT_MAPSPEC_VERSIONS,
  CONTRACT_VISIBILITY_HINT_KEYS,
} from './contract-manifest';
import { checkBivariateLayer } from './bivariate';

const CORPUS_DIR = resolve(REPO_ROOT, 'tests/cartography/carto_ir_corpus');
const MANIFEST_PATH = resolve(CORPUS_DIR, 'contract_manifest.json');

interface CorpusFixture {
  name: string;
  spec: MapSpec;
  expect: {
    effective_version: string;
    frontend_compile?: Record<string, unknown>;
  };
}

function loadFixtures(): CorpusFixture[] {
  return readdirSync(CORPUS_DIR)
    .filter((f) => f.endsWith('.json') && f !== 'contract_manifest.json')
    .sort()
    .map((f) => JSON.parse(readFileSync(resolve(CORPUS_DIR, f), 'utf-8')) as CorpusFixture);
}

describe('contract manifest 双向漂移闸（前端侧）', () => {
  const manifest = JSON.parse(readFileSync(MANIFEST_PATH, 'utf-8')) as Record<string, unknown>;

  it('manifest ≡ 前端镜像常量（改词表不更新 manifest 即红）', () => {
    expect(manifest['mapspec_versions']).toEqual([...CONTRACT_MAPSPEC_VERSIONS]);
    expect(manifest['latest_mapspec_version']).toBe(CONTRACT_LATEST_MAPSPEC_VERSION);
    expect(manifest['visibility_hint_keys']).toEqual([...CONTRACT_VISIBILITY_HINT_KEYS]);
    expect(manifest['bivariate_matrix_sizes']).toEqual([...CONTRACT_BIVARIATE_MATRIX_SIZES]);
    expect(manifest['bivariate_class_field_default']).toBe(CONTRACT_BIVARIATE_CLASS_FIELD_DEFAULT);
    expect(manifest['data_binding_field_types']).toEqual([...CONTRACT_DATA_BINDING_FIELD_TYPES]);
    expect(manifest['component_abi_version']).toBe(CONTRACT_COMPONENT_ABI_VERSION);
  });
});

describe('corpus fixtures → compileMapSpec golden', () => {
  const fixtures = loadFixtures();

  it('corpus 非空（fixture 目录漂移即红）', () => {
    expect(fixtures.length).toBeGreaterThanOrEqual(5);
  });

  it.each(fixtures.map((f) => [f.name, f] as const))(
    '%s：编译产物与 expect.frontend_compile 一致',
    (_name, fixture) => {
      const result = compileMapSpec(fixture.spec);
      const exp = fixture.expect.frontend_compile;
      if (!exp) return;
      expect(result.report.success).toBe(true);

      const layer = result.style.layers.find((l) => l.id === exp['layer_id']) as
        | Record<string, unknown>
        | undefined;
      expect(layer).toBeDefined();

      if (exp['minzoom'] !== undefined) expect(layer!['minzoom']).toBe(exp['minzoom']);
      if (exp['maxzoom'] !== undefined) expect(layer!['maxzoom']).toBe(exp['maxzoom']);
      if (exp['paint_key'] !== undefined) {
        const paint = layer!['paint'] as Record<string, unknown>;
        expect(paint[exp['paint_key'] as string]).toBeDefined();
        if (exp['paint_head'] !== undefined) {
          expect((paint[exp['paint_key'] as string] as unknown[])[0]).toBe(exp['paint_head']);
        }
      }
    },
  );

  it('raster_bivariate_degraded：bivariate 声明与 paint 漂移 → 编译产物降级 + warning', () => {
    const fixture = loadFixtures().find((f) => f.name === 'raster_bivariate_degraded')!;
    const result = compileMapSpec(fixture.spec);
    expect(
      result.report.warnings.some((w) => w.startsWith('bivariate_degraded[paint_not_match]')),
    ).toBe(true);
    // 降级常量色 = paint 首色（fixture 的既有 fill-color）
    const layer = result.style.layers.find((l) => l.id === 'broken_bivariate') as
      | Record<string, unknown>
      | undefined;
    const paint = layer!['paint'] as Record<string, unknown>;
    expect(paint['fill-color']).toBe('#3b82f6');
    // 独立校验器同判（checkBivariateLayer 与编译器降级决策同源）
    const specLayer = fixture.spec.layers![1];
    expect(checkBivariateLayer(specLayer)?.status).toBe('degraded');
  });
});
