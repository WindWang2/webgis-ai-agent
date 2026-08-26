/**
 * 组件目录 parity 测试（D7）：后端导出的 component-catalog.generated.json
 * 中每个 rendererRequired 类型在前端 registry 都有已注册渲染器
 * （'./index' side-effect import 之后）—— 前后端组件契约的单向闸。
 */
import { describe, it, expect } from 'vitest';
import catalog from '@/lib/map-components/component-catalog.generated.json';
import './index';
import { getComponentRenderer } from './registry';

interface CatalogEntry {
  type: string;
  rendererRequired: boolean;
}

const required = (catalog.componentTypes as CatalogEntry[]).filter((t) => t.rendererRequired === true);

describe('component catalog ↔ registry parity', () => {
  it('目录里存在 rendererRequired 类型（契约文件非空壳）', () => {
    expect(required.length).toBeGreaterThan(0);
  });

  it('每个 rendererRequired 类型都有注册渲染器', () => {
    for (const entry of required) {
      expect(
        getComponentRenderer(entry.type),
        `renderer missing for catalog type "${entry.type}"`,
      ).toBeDefined();
    }
  });
});
