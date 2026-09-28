'use client';

/**
 * MarketTab — 扩展市场浏览（只读：browse/search/detail/download）。
 *
 * 诚实性边界（frontend/docs/knowledge-market-recon.md §2）：
 * - 安装/启用/停用/卸载/已安装列表无 HTTP 端点（运维 CLI only）→ 不出现
 *   假安装按钮，详情页以固定说明代替（协调点）；
 * - certification / trust store / SBOM 内容无端点 → 信任信息如实展示版本
 *   详情里的 digest / fingerprint / sbom_digest / permissions，不造徽章；
 * - 列表 404 = 市场未启用（EXTENSION_REGISTRY_DIR 未配置），渲染为空态
 *   而非错误。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Package, RefreshCw, Search } from 'lucide-react';
import EmptyState from '@/components/shared/empty-state';
import {
  isApiError,
  describeApiError,
} from '@/lib/api/transport';
import {
  listMarketPackages,
  type PackageSummary,
} from '@/lib/api/extensions';
import { useT } from '@/lib/i18n/useT';
import { MarketPackageDetail } from './package-detail';

interface MarketTabProps {
  sessionId?: string | null;
  ownerToken?: string | null;
}

type ListState =
  | { status: 'loading' }
  | { status: 'ready'; total: number; items: PackageSummary[] }
  | { status: 'disabled' }
  | { status: 'error'; message: string };

function StatusChip({ status }: { status: PackageSummary['status'] }) {
  if (status === 'active') return null;
  const tone =
    status === 'revoked' ? 'text-status-critical' : 'text-status-warning';
  return <span className={`shrink-0 text-meta font-medium ${tone}`}>{status}</span>;
}

export function MarketTab(_props: MarketTabProps) {
  const [query, setQuery] = useState('');
  const [tag, setTag] = useState('');
  const [state, setState] = useState<ListState>({ status: 'loading' });
  const [selected, setSelected] = useState<string | null>(null);
  const t = useT('market');

  const seqRef = useRef(0);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      seqRef.current += 1;
    };
  }, []);

  const search = useCallback(async () => {
    const seq = ++seqRef.current;
    setState({ status: 'loading' });
    try {
      const res = await listMarketPackages({ q: query.trim(), tag: tag.trim(), limit: 50 });
      if (!mountedRef.current || seq !== seqRef.current) return;
      setState({ status: 'ready', total: res.total, items: res.items ?? [] });
    } catch (err) {
      if (!mountedRef.current || seq !== seqRef.current) return;
      if (isApiError(err) && err.status === 404) {
        // 后端契约：EXTENSION_REGISTRY_DIR 未配置 → 404 = 市场未启用。
        setState({ status: 'disabled' });
        return;
      }
      setState({ status: 'error', message: describeApiError(err, t('loadFailed')) });
    }
  }, [query, tag, t]);

  // 首次进入拉取；后续由刷新按钮 / 回车触发（避免每键一请求）。
  useEffect(() => {
    void search();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (selected) {
    return <MarketPackageDetail packageId={selected} onBack={() => setSelected(null)} />;
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-4 py-4">
      <div className="flex items-center justify-between">
        <div className="text-heading uppercase tracking-wider text-ink-muted font-semibold">
          {t('title')}
        </div>
        <button
          type="button"
          onClick={() => void search()}
          aria-label={t('refreshAria')}
          disabled={state.status === 'loading'}
          className="inline-flex items-center gap-1 rounded-sm border border-edge-subtle bg-surface-sunken px-2 py-1 text-meta font-medium text-ink-secondary transition-colors hover:bg-surface-hover disabled:opacity-50"
        >
          <RefreshCw size={12} aria-hidden className={state.status === 'loading' ? 'animate-spin' : ''} />
          {t('refresh')}
        </button>
      </div>

      <form
        role="search"
        className="flex items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void search();
        }}
      >
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label={t('search.packageAria')}
          placeholder={t('search.packagePlaceholder')}
          className="h-8 min-w-0 flex-1 rounded-sm border border-edge-subtle bg-surface-sunken px-2.5 text-body text-ink placeholder:text-ink-muted focus:outline-none focus:ring-1 focus:ring-status-accent"
        />
        <input
          value={tag}
          onChange={(e) => setTag(e.target.value)}
          aria-label={t('search.tagAria')}
          placeholder={t('search.tagPlaceholder')}
          className="h-8 w-16 rounded-sm border border-edge-subtle bg-surface-sunken px-2 text-body text-ink placeholder:text-ink-muted focus:outline-none focus:ring-1 focus:ring-status-accent"
        />
        <button
          type="submit"
          aria-label={t('search.submitAria')}
          className="inline-flex h-8 items-center rounded-sm bg-status-accent px-2.5 text-body font-medium text-ink-on-accent transition-opacity hover:opacity-85"
        >
          <Search size={13} aria-hidden />
        </button>
      </form>

      {state.status === 'loading' && (
        <p className="py-4 text-center text-body text-ink-muted italic">{t('loading')}</p>
      )}
      {state.status === 'error' && (
        <p role="alert" className="text-body font-medium text-status-critical">
          {state.message}
        </p>
      )}
      {state.status === 'disabled' && (
        <EmptyState
          icon={Package}
          title={t('disabled.title')}
          description={t('disabled.description')}
        />
      )}
      {state.status === 'ready' && state.items.length === 0 && (
        <EmptyState
          icon={Package}
          title={t('empty.title')}
          description={t('empty.description')}
        />
      )}
      {state.status === 'ready' && state.items.length > 0 && (
        <>
          <p className="text-meta text-ink-muted">{t('total', { total: state.total })}</p>
          <ul className="flex flex-col gap-2">
            {state.items.map((pkg) => (
              <li key={pkg.id}>
                <button
                  type="button"
                  onClick={() => setSelected(pkg.id)}
                  className="w-full rounded-md border border-edge-subtle bg-surface-raised px-3 py-2 text-left transition-colors hover:bg-surface-hover"
                >
                  <div className="flex items-center gap-2">
                    <span className="min-w-0 flex-1 truncate text-body font-medium text-ink">
                      {pkg.title}
                    </span>
                    <StatusChip status={pkg.status} />
                  </div>
                  <p className="mt-0.5 line-clamp-2 text-meta text-ink-muted">{pkg.description}</p>
                  <div className="mt-1 flex items-center gap-2 text-meta text-ink-muted">
                    <span className="truncate">{pkg.id}</span>
                    <span aria-hidden>·</span>
                    <span className="shrink-0">
                      {pkg.latest_version ? `v${pkg.latest_version}` : t('noVersion')}
                    </span>
                  </div>
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

export default MarketTab;
