/**
 * V6（ADR-0120 W4）：canonical render scene 跨语言 parity。
 *
 * 消费与后端 pytest 相同的 golden fixtures
 * （tests/cartography/golden_corpus/render_scene/*.json，expected 为手写
 * oracle）。双侧同断言 = describeRenderScene 语义跨语言锁定。
 */
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { describeRenderScene, serializeRenderScene } from '@/lib/map-kit/render-scene';

interface SceneFixture {
  name: string;
  spec: Record<string, unknown>;
  degradations?: Array<{ code: string }>;
  expected: {
    layers: unknown[];
    components: unknown[];
    legends: unknown[];
    degradationCodes: string[];
  };
}

const fixtureDir = join(__dirname, '../../../tests/cartography/golden_corpus/render_scene');

function loadFixtures(): SceneFixture[] {
  return readdirSync(fixtureDir)
    .filter((f) => f.endsWith('.json'))
    .sort()
    .map((f) => JSON.parse(readFileSync(join(fixtureDir, f), 'utf-8')) as SceneFixture);
}

const fixtures = loadFixtures();

describe('render scene 跨语言 parity（W4）', () => {
  it('fixtures 数量不缩水', () => {
    expect(fixtures.length).toBeGreaterThanOrEqual(4);
  });

  it.each(fixtures.map((f) => [f.name, f] as const))('%s 与 golden 一致', (_name, fixture) => {
    const snapshot = describeRenderScene(
      fixture.spec as never,
      { degradations: fixture.degradations as never },
    );
    expect(snapshot.layers).toEqual(fixture.expected.layers);
    expect(snapshot.components).toEqual(fixture.expected.components);
    expect(snapshot.legends).toEqual(fixture.expected.legends);
    expect(snapshot.degradationCodes).toEqual(fixture.expected.degradationCodes);
  });

  it('serializeRenderScene 确定性', () => {
    const fixture = fixtures[0];
    const s1 = serializeRenderScene(describeRenderScene(fixture.spec as never));
    const s2 = serializeRenderScene(describeRenderScene(fixture.spec as never));
    expect(s1).toBe(s2);
  });
});
