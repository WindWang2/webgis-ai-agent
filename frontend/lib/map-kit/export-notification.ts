import { t } from '@/lib/i18n/t';
/** Treat authored export titles as bounded data inside system notifications. */
export function exportNotificationTitle(value: unknown): string {
  return String(value ?? t('map.export.unnamed')).replace(/[\u0000-\u001f\u007f`<>\[\]]/g, ' ').slice(0, 80);
}
