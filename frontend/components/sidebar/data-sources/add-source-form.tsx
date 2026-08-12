'use client';

import { useId, useState } from 'react';
import { dataFabricApi } from '@/lib/api/data-fabric';
import { useToastStore } from '@/components/ui/toast';

const SOURCE_TYPES = [
  { value: 'ogc_api', label: 'OGC API Features' },
  { value: 'postgis', label: 'PostGIS 数据库' },
  { value: 'wfs', label: 'OGC WFS' },
  { value: 'wms', label: 'OGC WMS' },
  { value: 'wmts', label: 'OGC WMTS' },
  { value: 'arcgis', label: 'ArcGIS REST' },
  { value: 'pmtiles', label: 'PMTiles' },
];

export interface AddSourceFormProps {
  /** 注册成功后回调（父级负责关闭表单 + 刷新 sources/catalog，保持既有时序） */
  onCreated: () => void;
}

/**
 * 注册新数据源表单（自包含：字段状态 + 校验 + createDataSource 调用 +
 * 成功/失败 toast）。原 data-sources-tab 内联表单原样拆出。
 */
export function AddSourceForm({ onCreated }: AddSourceFormProps) {
  const [newName, setNewName] = useState('');
  const [newType, setNewType] = useState('ogc_api');
  const [newUrl, setNewUrl] = useState('');
  const [newAllowPrivate, setNewAllowPrivate] = useState(false);
  const [submittingSource, setSubmittingSource] = useState(false);
  const addToast = useToastStore((s) => s.addToast);

  const nameId = useId();
  const typeId = useId();
  const urlId = useId();
  const privateId = useId();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newName.trim() || !newUrl.trim()) {
      addToast('请填写数据源名称和 Endpoint URL', 'warning');
      return;
    }
    setSubmittingSource(true);
    try {
      await dataFabricApi.createDataSource({
        name: newName.trim(),
        source_type: newType,
        endpoint_url: newUrl.trim(),
        allow_private: newAllowPrivate,
      });
      addToast('数据源注册成功', 'success');
      setNewName('');
      setNewUrl('');
      onCreated();
    } catch (err) {
      addToast(err instanceof Error ? err.message : '注册失败', 'error');
    } finally {
      setSubmittingSource(false);
    }
  };

  const inputClass =
    'w-full rounded border border-[var(--theme-border)] bg-[var(--theme-bg-input)] px-2 py-1 text-[11px] text-[var(--theme-text-primary)]';
  const labelClass = 'text-[11px] text-[var(--theme-text-muted)]';

  return (
    <form
      onSubmit={handleSubmit}
      className="shrink-0 space-y-2 border-b border-[var(--theme-border)] bg-[var(--theme-bg-subtle)] p-3"
    >
      <h5 className="text-[12px] font-semibold text-[var(--theme-text-primary)]">注册新数据源</h5>
      <div>
        <label htmlFor={nameId} className={labelClass}>
          数据源名称
        </label>
        <input
          id={nameId}
          type="text"
          placeholder="例如: 国家地理 WFS 服务"
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          className={inputClass}
        />
      </div>
      <div className="flex gap-2">
        <div className="w-1/2">
          <label htmlFor={typeId} className={labelClass}>
            协议类型
          </label>
          <select
            id={typeId}
            value={newType}
            onChange={(e) => setNewType(e.target.value)}
            className={inputClass}
          >
            {SOURCE_TYPES.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
        </div>
        <div className="flex w-1/2 items-center pt-4">
          <label htmlFor={privateId} className="flex cursor-pointer items-center gap-1.5 text-[11px] text-[var(--theme-text-secondary)]">
            <input
              id={privateId}
              type="checkbox"
              checked={newAllowPrivate}
              onChange={(e) => setNewAllowPrivate(e.target.checked)}
              className="rounded border-[var(--theme-border-strong)]"
              style={{ accentColor: 'var(--agent-accent, #16a34a)' }}
            />
            <span>允许内网 (SSRF)</span>
          </label>
        </div>
      </div>
      <div>
        <label htmlFor={urlId} className={labelClass}>
          Endpoint URL / 连接地址
        </label>
        <input
          id={urlId}
          type="text"
          placeholder="https://..."
          value={newUrl}
          onChange={(e) => setNewUrl(e.target.value)}
          className={`${inputClass} font-mono`}
        />
      </div>
      <button
        type="submit"
        disabled={submittingSource}
        className="w-full rounded py-1.5 text-[12px] font-medium text-white transition-opacity hover:opacity-85 disabled:opacity-50"
        style={{ background: 'var(--agent-accent, #16a34a)' }}
      >
        {submittingSource ? '提交中...' : '提交注册并同步'}
      </button>
    </form>
  );
}
