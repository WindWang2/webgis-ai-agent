'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, Download, File, FileText, FolderOpen, LoaderCircle, Map, Package, Pin, RefreshCw, Search } from 'lucide-react';
import { EmptyState } from '@/components/shared/empty-state';
import { IconButton } from '@/components/shared/icon-button';
import { InlineNotice } from '@/components/shared/inline-notice';
import { downloadWithAuth } from '@/lib/api/authenticated-download';
import { filesFromMessages, generatedFileKey, listGeneratedFiles, mergeGeneratedFiles, type GeneratedFile } from '@/lib/api/generated-files';
import { useLocale, useT } from '@/lib/i18n/useT';

type GeneratedFilesPanelProps = {
  sessionId: string | null;
  ownerToken?: string | null;
  messages: readonly { id: string; role: string; content: string; timestamp?: Date | number | null }[];
  onBack: () => void;
};

type CatalogResult = { revision: number; files: GeneratedFile[]; failed: boolean };
const SOURCE_ICONS = { export: Map, report: FileText, chat: File, delivery: Package };
const fieldClass = 'min-h-9 min-w-0 rounded-md border border-edge-subtle bg-surface-raised px-2 text-meta text-ink focus-visible:ring-2 focus-visible:ring-status-accent';

export function GeneratedFilesPanel(props: GeneratedFilesPanelProps) {
  // Drop the old list and any active downloads before rendering another session.
  return <SessionFiles key={JSON.stringify([props.sessionId, props.ownerToken ?? null])} {...props} />;
}

function SessionFiles({ sessionId, ownerToken, messages, onBack }: GeneratedFilesPanelProps) {
  const t = useT('chat.generatedFiles');
  const locale = useLocale();
  const back = useRef<HTMLButtonElement>(null);
  const currentDownload = useRef<AbortController | null>(null);
  const [catalog, setCatalog] = useState<CatalogResult | null>(null);
  const [revision, setRevision] = useState(0);
  const [query, setQuery] = useState('');
  const [format, setFormat] = useState('');
  const [sort, setSort] = useState('newest');
  const [downloading, setDownloading] = useState<string | null>(null);
  const [failedDownload, setFailedDownload] = useState<string | null>(null);
  const pinStorageKey = sessionId ? 'webgis.generated-files.pins.v1:' + encodeURIComponent(sessionId) : null;
  const [pinned, setPinned] = useState<Set<string>>(() => {
    try {
      const saved: unknown = pinStorageKey && JSON.parse(localStorage.getItem(pinStorageKey) ?? '[]');
      return new Set(Array.isArray(saved) ? saved.filter((key): key is string => typeof key === 'string') : []);
    } catch {
      return new Set();
    }
  });

  useEffect(() => {
    back.current?.focus();
    return () => currentDownload.current?.abort();
  }, []);

  useEffect(() => {
    if (!sessionId) return;
    const controller = new AbortController();
    void listGeneratedFiles(sessionId, { ownerToken, signal: controller.signal }).then(
      files => {
        if (!controller.signal.aborted) setCatalog({ revision, files, failed: false });
      },
      () => {
        if (!controller.signal.aborted) {
          setCatalog(previous => ({ revision, files: previous?.files ?? [], failed: true }));
        }
      },
    );
    return () => controller.abort();
  }, [sessionId, ownerToken, revision]);

  const loading = !!sessionId && catalog?.revision !== revision;
  const messageFiles = useMemo(() => filesFromMessages(messages), [messages]);
  const files = useMemo(() => mergeGeneratedFiles(catalog?.files ?? [], messageFiles), [catalog, messageFiles]);
  const formats = [...new Set(files.map(file => file.format.toUpperCase()).filter(Boolean))].sort();
  const needle = query.trim().toLocaleLowerCase(locale);
  const visible = files.filter(file =>
    (!format || file.format.toUpperCase() === format) && file.name.toLocaleLowerCase(locale).includes(needle),
  ).sort((a, b) =>
    Number(pinned.has(generatedFileKey(b))) - Number(pinned.has(generatedFileKey(a)))
    || (sort === 'newest' ? (b.created_at ?? -Infinity) - (a.created_at ?? -Infinity) : 0)
    || a.name.localeCompare(b.name, locale, { numeric: true }),
  );
  const dateFormat = useMemo(() => new Intl.DateTimeFormat(locale, { dateStyle: 'medium' }), [locale]);

  function togglePin(key: string) {
    const next = new Set(pinned);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    setPinned(next);
    if (!pinStorageKey) return;
    try {
      if (next.size) localStorage.setItem(pinStorageKey, JSON.stringify([...next]));
      else localStorage.removeItem(pinStorageKey);
    } catch {
      // The current view still supports pinning when browser storage is unavailable.
    }
  }

  async function download(file: GeneratedFile) {
    if (!file.download_url || currentDownload.current) return;
    const controller = new AbortController();
    currentDownload.current = controller;
    setFailedDownload(null);
    setDownloading(file.id);
    try {
      await downloadWithAuth(file.download_url, { fallbackFilename: file.name, ownerToken, signal: controller.signal });
    } catch {
      if (!controller.signal.aborted) setFailedDownload(file.name);
    } finally {
      if (!controller.signal.aborted) setDownloading(null);
      currentDownload.current = null;
    }
  }

  return (
    <section aria-label={t('title')} className="flex h-full min-h-0 min-w-0 flex-1 flex-col bg-surface-panel">
      <header className="flex shrink-0 items-center gap-1 border-b border-edge-subtle px-2 py-1">
        <IconButton ref={back} icon={ArrowLeft} label={t('back')} onClick={onBack} className="min-h-11 min-w-11" />
        <h2 className="min-w-0 flex-1 text-title font-semibold text-ink">{t('title')}</h2>
        <IconButton icon={RefreshCw} label={t('refresh')} disabled={!sessionId || loading}
          onClick={() => setRevision(value => value + 1)} className="min-h-11 min-w-11" />
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
        {files.length > 0 && (
          <div className="space-y-2 border-b border-edge-subtle px-3 py-3">
            <label className="relative block">
              <Search size={15} aria-hidden className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-muted" />
              <input type="search" aria-label={t('search')} placeholder={t('search')} value={query}
                onChange={event => setQuery(event.target.value)} className={fieldClass + ' w-full ps-8 placeholder:text-ink-muted'} />
            </label>
            <div className="grid grid-cols-2 gap-2">
              <select aria-label={t('format')} value={format} onChange={event => setFormat(event.target.value)} className={fieldClass}>
                <option value="">{t('allFormats')}</option>
                {format && !formats.includes(format) && <option value={format}>{format}</option>}
                {formats.map(item => <option key={item} value={item}>{item}</option>)}
              </select>
              <select aria-label={t('sort')} value={sort} onChange={event => setSort(event.target.value)} className={fieldClass}>
                <option value="newest">{t('newest')}</option>
                <option value="name">{t('name')}</option>
              </select>
            </div>
          </div>
        )}
        {loading && <p role="status" className="px-3 py-2 text-meta text-ink-muted">{t('loading')}</p>}
        {catalog?.failed && !loading && <InlineNotice variant="error" className="m-3">{t('loadError')}</InlineNotice>}
        {failedDownload && <InlineNotice variant="error" className="m-3">{t('downloadError', { name: failedDownload })}</InlineNotice>}
        {visible.length > 0 ? (
          <>
            <p role="status" className="px-3 pt-3 text-meta font-medium text-ink-muted">{t('count', { count: visible.length })}</p>
            <ul className="divide-y divide-edge-subtle px-3">
              {visible.map(file => {
                const Icon = SOURCE_ICONS[file.source];
                const date = file.created_at == null ? null : new Date(file.created_at * 1000);
                const pending = downloading === file.id;
                const key = generatedFileKey(file);
                const isPinned = pinned.has(key);
                return (
                  <li key={key} className="flex items-center gap-2 py-2">
                    <span aria-hidden className="grid size-8 shrink-0 place-items-center rounded-md bg-surface-sunken text-ink-secondary">
                      <Icon size={16} />
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="break-words text-body font-medium text-ink [overflow-wrap:anywhere]">{file.name}</p>
                      <p className="mt-0.5 flex flex-wrap gap-x-2 text-meta text-ink-muted">
                        {file.format && <span>{file.format.toUpperCase()}</span>}
                        <span>{t(`source.${file.source}`)}</span>
                        {date && !Number.isNaN(date.getTime()) && <time dateTime={date.toISOString()}>{dateFormat.format(date)}</time>}
                        {!file.download_url && <span>{t('unavailable')}</span>}
                      </p>
                    </div>
                    <div className="flex shrink-0 items-center">
                      <IconButton icon={Pin} active={isPinned} aria-pressed={isPinned}
                        label={t(isPinned ? 'unpin' : 'pin', { name: file.name })}
                        onClick={() => togglePin(key)} className="min-h-11 min-w-11" />
                      {file.download_url && <IconButton icon={pending ? LoaderCircle : Download}
                        label={t(pending ? 'downloading' : 'download', { name: file.name })} disabled={downloading !== null}
                        onClick={() => void download(file)} className="min-h-11 min-w-11" />}
                    </div>
                  </li>
                );
              })}
            </ul>
          </>
        ) : files.length > 0 ? (
          <div role="status"><EmptyState icon={Search} title={t('noMatches')} action={{
            label: t('clearFilters'), onClick: () => { setQuery(''); setFormat(''); },
          }} /></div>
        ) : !loading && !catalog?.failed && (
          <div role="status"><EmptyState icon={FolderOpen} title={t('empty')} description={t('emptyHint')} /></div>
        )}
      </div>
    </section>
  );
}
