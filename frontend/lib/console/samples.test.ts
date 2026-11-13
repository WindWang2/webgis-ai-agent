import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { clearAuth, setAuth } from '@/lib/auth/tokenStore';
import { installInMemoryLocalStorage } from '../../test/in-memory-local-storage';
import { appendHistory, loadHistory, type QueryHistoryEntry } from './samples';

const USER_A = { id: 'user-a', username: 'a', email: 'a@x', full_name: 'A', role: 'user' };
const USER_B = { id: 'user-b', username: 'b', email: 'b@x', full_name: 'B', role: 'user' };

function entry(title: string): QueryHistoryEntry {
  return {
    id: `h-${title}`,
    ts: 1,
    targetId: 't',
    targetTitle: title,
    spec: { where: `${title} = 1`, fields: '', limit: 10, order: '', resultMode: 'features' },
    status: 'ok',
  };
}

beforeEach(() => {
  installInMemoryLocalStorage();
  clearAuth();
});

afterEach(() => {
  clearAuth();
  localStorage.clear();
});

describe('query console history isolation', () => {
  it('scopes history by user id and drops it on logout', () => {
    localStorage.setItem('geoagent-query-console-history-v1', JSON.stringify([entry('legacy')]));
    setAuth({ accessToken: 'a', refreshToken: 'ra' }, USER_A);
    appendHistory(entry('alpha'));
    expect(loadHistory().map((row) => row.targetTitle)).toEqual(['alpha']);
    expect(localStorage.getItem('geoagent-query-console-history-v1:user-a')).toContain('alpha');

    setAuth({ accessToken: 'b', refreshToken: 'rb' }, USER_B);
    expect(loadHistory()).toEqual([]);
    expect(localStorage.getItem('geoagent-query-console-history-v1:user-a')).toBeNull();

    appendHistory(entry('beta'));
    clearAuth();
    expect(localStorage.getItem('geoagent-query-console-history-v1:user-b')).toBeNull();
    expect(loadHistory()).toEqual([]);
  });
});
