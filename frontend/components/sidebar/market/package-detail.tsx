'use client';

/**
 * MarketPackageDetail — 扩展包详情（GET packages/{id} + versions/{v}）。
 *
 * 诚实性边界：只展示后端真实返回的字段。certification / trust store 状态
 * 无 HTTP 端点 → 不渲染徽章；安装类操作只有运维 CLI → 固定说明条 +
 * 真实下载（走鉴权 blob 下载，#515 同路）。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowLeft, Download } from 'lucide-react';
import { describeApiError, isApiError } from '@/lib/api/transport';
import { downloadWithAuth } from '@/lib/api/authenticated-download';
import { useT } from '@/lib/i18n/useT';
import {
  getMarketPackage,
  getMarketPackageVersion,
  type PackageSummary,
  type PackageVersionDetail,
} from '@/lib/api/extensions';

function shortHash(hash: string): string {
  return hash.length > 16 ? `${hash.slice(0, 16)}…` : hash;
}

function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${bytes} B`;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-meta font-semibold uppercase tracking-wider text-ink-muted">
        {label}
      </span>
      {children}
    </div>
  );
}

export function MarketPackageDetail({
  packageId,
  onBack,
}: {
  packageId: string;
  onBack: () => void;
}) {
  const [summary, setSummary] = useState<PackageSummary | null>(null);
  const [summaryError, setSummaryError] = useState<string | null>(null);

  const [version, setVersion] = useState<string | null>(null);
  const [detail, setDetail] = useState<PackageVersionDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  const [downloadState, setDownloadState] = useState<
    { status: 'idle' } | { status: 'busy' } | { status: 'done'; filename: string | null } | { status: 'error'; message: string }
  >({ status: 'idle' });

  const seqRef = useRef(0);
  const t = useT('market');

  useEffect(() => {
    const seq = ++seqRef.current;
    getMarketPackage(packageId)
      .then((res) => {
        if (seq !== seqRef.current) return;
        setSummary(res);
      })
      .catch((err: unknown) => {
        if (seq !== seqRef.current) return;
        setSummaryError(describeApiError(err, t('detail.loadFailed')));
      });
  }, [packageId, t]);

  // 摘要到达后默认选中最新版本。
  useEffect(() => {
    if (summary && !version) setVersion(summary.latest_version ?? summary.versions[0] ?? null);
  }, [summary, version]);

  useEffect(() => {
    if (!version) return;
    const seq = ++seqRef.current;
    setDetailLoading(true);
    setDetailError(null);
    getMarketPackageVersion(packageId, version)
      .then((res) => {
        if (seq !== seqRef.current) return;
        setDetail(res);
      })
      .catch((err: unknown) => {
        if (seq !== seqRef.current) return;
        setDetail(null);
        setDetailError(describeApiError(err, t('detail.versionLoadFailed')));
      })
      .finally(() => {
        if (seq === seqRef.current) setDetailLoading(false);
      });
  }, [packageId, version, t]);

  const onDownload = useCallback(async () => {
    if (!detail || downloadState.status === 'busy') return;
    setDownloadState({ status: 'busy' });
    try {
      // downloadWithAuth 自行完成 blob 保存（含鉴权），成功即 void 返回。
      await downloadWithAuth(detail.download);
      setDownloadState({ status: 'done', filename: null });
    } catch (err) {
      setDownloadState({
        status: 'error',
        message: isApiError(err) && err.status === 410
          ? t('detail.revokedDownload')
          : describeApiError(err, t('detail.downloadFailed')),
      });
    }
  }, [detail, downloadState.status, t]);

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-4 py-4">
      <button
        type="button"
        onClick={onBack}
        className="inline-flex w-fit items-center gap-1 rounded-sm px-1 py-0.5 text-meta font-medium text-ink-secondary transition-colors hover:text-ink"
      >
        <ArrowLeft size={12} aria-hidden />
        {t('detail.back')}
      </button>

      {summaryError && (
        <p role="alert" className="text-body font-medium text-status-critical">
          {summaryError}
        </p>
      )}
      {!summary && !summaryError && <p className="text-body text-ink-muted italic">{t('detail.loading')}</p>}

      {summary && (
        <>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="min-w-0 truncate text-title font-bold text-ink">{summary.title}</h3>
              {summary.status !== 'active' && (
                <span
                  className={`shrink-0 rounded-sm bg-surface-sunken px-1.5 py-0.5 text-meta font-medium ${
                    summary.status === 'revoked' ? 'text-status-critical' : 'text-status-warning'
                  }`}
                >
                  {summary.status}
                </span>
              )}
            </div>
            {summary.deprecation_note && (
              <p className="mt-1 text-meta text-status-warning">{summary.deprecation_note}</p>
            )}
            <p className="mt-1 text-meta text-ink-muted">
              {summary.id} · publisher {summary.publisher}
            </p>
            <p className="mt-1.5 text-body leading-relaxed text-ink-secondary">
              {summary.description}
            </p>
            {summary.tags.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1">
                {summary.tags.map((tag) => (
                  <span
                    key={tag}
                    className="rounded-sm bg-surface-sunken px-1.5 py-0.5 text-meta text-ink-muted"
                  >
                    {tag}
                  </span>
                ))}
              </div>
            )}
          </div>

          {/* 安装类操作的诚实说明（后端无 HTTP 安装端点，CLI only —— 协调点） */}
          <div
            data-state="honest-install-note"
            className="rounded-md border border-edge-subtle bg-surface-sunken/60 px-3 py-2"
          >
            <p className="text-meta leading-relaxed text-ink-secondary">
              {t('detail.installNote')}
            </p>
          </div>

          {/* 版本历史 */}
          <Field label={t('detail.versionHistory')}>
            <div className="flex flex-wrap gap-1" role="tablist" aria-label={t('detail.versionListAria')}>
              {summary.versions.map((v) => (
                <button
                  key={v}
                  type="button"
                  role="tab"
                  aria-selected={version === v}
                  onClick={() => {
                    setVersion(v);
                    setDownloadState({ status: 'idle' });
                  }}
                  className={`rounded-sm border px-2 py-0.5 text-meta font-medium transition-colors ${
                    version === v
                      ? 'border-status-accent text-status-accent'
                      : 'border-edge-subtle text-ink-secondary hover:bg-surface-hover'
                  }`}
                >
                  v{v}
                </button>
              ))}
            </div>
          </Field>

          {detailLoading && <p className="text-body text-ink-muted italic">{t('detail.versionLoading')}</p>}
          {detailError && (
            <p role="alert" className="text-body font-medium text-status-critical">
              {detailError}
            </p>
          )}

          {detail && (
            <div className="flex flex-col gap-3 rounded-md border border-edge-subtle bg-surface-raised px-3 py-3">
              {detail.yanked && (
                <p className="text-meta font-semibold text-status-critical">{t('detail.yanked')}</p>
              )}

              <Field label={t('detail.permissions')}>
                {detail.permissions.length > 0 ? (
                  <ul className="flex flex-col gap-0.5">
                    {detail.permissions.map((p) => (
                      <li key={p} className="text-meta text-ink-secondary">
                        {p}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-meta text-ink-muted">{t('detail.noPermissions')}</p>
                )}
              </Field>

              <Field label={t('detail.dependencies', { count: detail.dependencies.length })}>
                {detail.dependencies.length > 0 ? (
                  <ul className="flex flex-col gap-0.5">
                    {detail.dependencies.map((dep, i) => (
                      <li key={`${dep.id ?? i}`} className="text-meta text-ink-secondary">
                        {dep.id ?? JSON.stringify(dep)}
                        {dep.version ? ` @ ${dep.version}` : ''}
                        {dep.optional ? t('detail.optional') : ''}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-meta text-ink-muted">{t('detail.noDependencies')}</p>
                )}
              </Field>

              <div className="grid grid-cols-2 gap-2 text-meta text-ink-secondary">
                <span>{t('detail.size', { size: formatBytes(detail.size_bytes) })}</span>
                <span>
                  {t('detail.builtAt', {
                    date: detail.created_at ? new Date(detail.created_at).toLocaleDateString() : '—',
                  })}
                </span>
                <span>api_version {detail.api_version}</span>
                <span>min_core {detail.min_core_version}</span>
                <span className="truncate" title={detail.digest}>digest {shortHash(detail.digest)}</span>
                <span className="truncate" title={detail.fingerprint}>fingerprint {shortHash(detail.fingerprint)}</span>
                <span className="truncate" title={detail.sbom_digest}>SBOM digest {shortHash(detail.sbom_digest)}</span>
                <span className="truncate">signing key {shortHash(detail.key_id)}</span>
              </div>

              <p className="text-meta text-ink-muted">{t('detail.certificationNote')}</p>

              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => void onDownload()}
                  disabled={downloadState.status === 'busy'}
                  aria-busy={downloadState.status === 'busy'}
                  className="inline-flex items-center gap-1.5 rounded-sm bg-status-accent px-3 py-1.5 text-body font-medium text-ink-on-accent transition-opacity hover:opacity-85 disabled:opacity-50"
                >
                  <Download size={13} aria-hidden />
                  {downloadState.status === 'busy' ? t('detail.downloading') : t('detail.download')}
                </button>
                {downloadState.status === 'done' && (
                  <span role="status" className="text-meta font-medium text-status-success">
                    {downloadState.filename
                      ? t('detail.savedWithName', { filename: downloadState.filename })
                      : t('detail.saved')}
                  </span>
                )}
                {downloadState.status === 'error' && (
                  <span role="alert" className="text-meta font-medium text-status-critical">
                    {downloadState.message}
                  </span>
                )}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

export default MarketPackageDetail;
