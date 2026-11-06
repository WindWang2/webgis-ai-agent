/** Bounded diagnostics owned by one map/session/source generation. */
export interface TileMissAccount {
  sourceId: string;
  count: number;
  samples: string[];
}
interface AccountState { count: number; samples: string[]; source?: object }
const DEFAULT_SCOPE = {};
const SOURCES_MAX = 8;
const SAMPLES_MAX = 5;
let scopes = new WeakMap<object, Map<string, AccountState>>();

export function recordTileMiss(sourceId: string, tileCoord: string, scope: object = DEFAULT_SCOPE, source?: object): void {
  let counts = scopes.get(scope);
  if (!counts) { counts = new Map(); scopes.set(scope, counts); }
  let account = counts.get(sourceId);
  if (!account || (source && source !== account.source)) {
    account = { count: 0, samples: [], source };
    counts.delete(sourceId);
    if (counts.size >= SOURCES_MAX) counts.delete(counts.keys().next().value!);
    counts.set(sourceId, account);
  }
  account.count = Math.min(Number.MAX_SAFE_INTEGER, account.count + 1);
  if (tileCoord && account.samples.length < SAMPLES_MAX) account.samples.push(tileCoord);
}

export function tileMissSnapshot(scope: object = DEFAULT_SCOPE, sourceForId?: (id: string) => object | undefined): TileMissAccount[] {
  const counts = scopes.get(scope);
  if (!counts) return [];
  if (sourceForId) {
    for (const [id, account] of counts) {
      const source = sourceForId(id);
      if (!source || (account.source && source !== account.source)) counts.delete(id);
    }
  }
  return Array.from(counts, ([sourceId, account]) => ({ sourceId, count: account.count, samples: [...account.samples] }))
    .sort((a, b) => a.sourceId < b.sourceId ? -1 : a.sourceId > b.sourceId ? 1 : 0);
}

export function resetTileMiss(scope: object, sourceId?: string): void {
  if (sourceId === undefined) scopes.delete(scope);
  else scopes.get(scope)?.delete(sourceId);
}

export function tileMissDetail(account: TileMissAccount): string {
  return `src=${account.sourceId} count=${account.count} sample=${account.samples.join(';')}`.slice(0, 200);
}

export function _resetTileMissForTests(): void { scopes = new WeakMap(); }
