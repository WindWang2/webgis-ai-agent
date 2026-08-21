import { describe, it, expect, beforeEach } from 'vitest';
import { commitMapSpecDocument, getCommittedMapSpec, subscribeMapSpecLive, resetLiveState } from './session-cursor';

const spec = { version: '1.0', sources: {}, layers: [] } as any;

describe('commitMapSpecDocument — #692 same-spec early exit', () => {
  beforeEach(() => { resetLiveState(); });

  it('does not bump generation when the same object re-commits', () => {
    let fires = 0;
    const off = subscribeMapSpecLive(() => { fires += 1; });
    commitMapSpecDocument(spec);
    const first = fires;
    expect(first).toBe(1);
    commitMapSpecDocument(spec); // 同对象再提交：不 emit
    expect(fires).toBe(1);
    commitMapSpecDocument({ ...spec }); // 等值新对象：仍 emit（无深比较）
    expect(fires).toBe(2);
    off();
  });

  it('committed getter reflects the latest accepted spec', () => {
    commitMapSpecDocument(spec);
    expect(getCommittedMapSpec()).toBe(spec);
  });
});
