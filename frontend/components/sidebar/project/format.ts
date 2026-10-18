/** Shared formatters for the project workspace asset panels (ADR-0143). */

import { t as tNow, currentLocale } from '@/lib/i18n/t';

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null || !Number.isFinite(bytes)) return '—';
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value >= 100 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`;
}

/** Snapshot created_at is epoch seconds and optional — unknown stays unknown. */
export function formatEpoch(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return tNow('sidebar.project.unknownTime');
  try {
    // #1628/#1436：时间戳随应用语言格式化（zh-CN 用户输出不变），不硬编码。
    return new Date(seconds * 1000).toLocaleString(currentLocale(), { hour12: false });
  } catch {
    return tNow('sidebar.project.unknownTime');
  }
}

export function formatIso(iso: string | null | undefined): string {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString(currentLocale(), { hour12: false });
  } catch {
    return iso;
  }
}
