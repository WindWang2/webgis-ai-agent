import { describe, it, expect } from 'vitest';
import { hasWorkspaceContent } from './workspace-content';

describe('hasWorkspaceContent (#553 new-session confirm guard)', () => {
  it('returns false for a pristine workspace (welcome bubble only / empty)', () => {
    expect(hasWorkspaceContent([{ id: '1' }], [], [], [], [])).toBe(false);
    expect(hasWorkspaceContent([], [], [], [], [])).toBe(false);
  });

  it('returns true when any layer / annotation / op / result exists', () => {
    expect(hasWorkspaceContent([{ id: '1' }], [{}], [], [], [])).toBe(true);
    expect(hasWorkspaceContent([{ id: '1' }], [], [{}], [], [])).toBe(true);
    expect(hasWorkspaceContent([{ id: '1' }], [], [], [{}], [])).toBe(true);
    expect(hasWorkspaceContent([{ id: '1' }], [], [], [], [{}])).toBe(true);
  });

  it('returns true for any real message beyond the welcome bubble', () => {
    expect(hasWorkspaceContent([{ id: '1' }, { id: 'm1' }], [], [], [], [])).toBe(true);
    // 恢复的会话即使只有一条真实消息也算内容（id !== '1'）
    expect(hasWorkspaceContent([{ id: 'm1' }], [], [], [], [])).toBe(true);
  });
});
