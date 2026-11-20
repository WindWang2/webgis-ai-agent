import { useLayoutEffect } from 'react';
import { act, renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { computeFitTransform, containerToImagePixel, type ViewTransform } from './coords';
import { useSourceViewTransform } from './use-source-view-transform';

const viewport = { width: 340, height: 240 };
const sourceA = { source_id: 'a', width: 340, height: 240 };
const sourceB = { source_id: 'b', width: 680, height: 480 };

describe('source-owned image view commit ordering', () => {
  function setup(source: typeof sourceA | null = null) {
    const commits: Array<{ source: typeof source; transform: ViewTransform }> = [];
    const hook = renderHook(({ source, scope }) => {
      const view = useSourceViewTransform(source, scope, viewport);
      // Observe committed coordinates before passive effects, not after waiting
      // for a second render. No UI, network, image or timer mocks are involved.
      useLayoutEffect(() => { commits.push({ source, transform: view[0] }); });
      return view;
    }, { initialProps: { source, scope: 'session-a' } });
    return { ...hook, commits };
  }

  it('fits the first source commit before any pointer can be interpreted', () => {
    const { rerender, commits } = setup();
    rerender({ source: sourceA, scope: 'session-a' });
    const first = commits.find((row) => row.source === sourceA)!;
    expect(first.transform).toEqual(computeFitTransform(sourceA, viewport));
    expect(containerToImagePixel(93.5, 66, first.transform, sourceA)).toEqual({ px: 85, py: 60, inside: true });
  });

  it('resets source/session ownership atomically while preserving same-source interaction', () => {
    const { result, rerender, commits } = setup(sourceA);
    act(() => result.current[1]((value) => ({ ...value, zoom: 2, panX: 33 })));
    rerender({ source: { ...sourceA }, scope: 'session-a' });
    expect(result.current[0]).toMatchObject({ zoom: 2, panX: 33 });
    const beforeB = commits.length;
    rerender({ source: sourceB, scope: 'session-a' });
    expect(commits[beforeB].transform).toEqual(computeFitTransform(sourceB, viewport));
    act(() => result.current[1]((value) => ({ ...value, zoom: 3, panX: 99 })));
    const beforeSession = commits.length;
    const staleSetter = result.current[1];
    rerender({ source: sourceB, scope: 'session-b' });
    expect(commits[beforeSession].transform).toEqual(computeFitTransform(sourceB, viewport));
    act(() => staleSetter((value) => ({ ...value, panX: 999 })));
    expect(result.current[0]).toEqual(computeFitTransform(sourceB, viewport));
    rerender({ source: sourceA, scope: 'session-b' });
    expect(result.current[0]).toEqual(computeFitTransform(sourceA, viewport));
  });
});
