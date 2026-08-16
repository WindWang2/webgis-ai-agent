import type { ApiFetchOptions } from './transport';
import { apiFetchBlob } from './transport';
import { isFirstPartyUrl, toApiPath } from './first-party';

/**
 * Authenticated download + preview of protected file endpoints.
 *
 * The backend's file-download routes (the map / report route modules'
 * `get_current_user` dependency) require `Authorization: Bearer`. A bare
 * `<a href>` navigation or `<img src>`/`<iframe>` fetch is a browser-native
 * request that can never attach that header, so every such link/image was a
 * hard 401. These helpers route those fetches through the transport so the
 * Bearer (and refresh-on-401) machinery applies — same credential channel as
 * the upload side.
 */

function isProtectedPath(path: string): boolean {
  // /api/v1/export/download/{filename}
  if (path.startsWith('/api/v1/export/download/')) return true;
  // /api/v1/reports/{id}/download
  if (/^\/api\/v1\/reports\/[^/]+\/download$/.test(path)) return true;
  return false;
}

/** True when `url` points at a first-party protected download route. */
export function isProtectedDownloadUrl(url: string): boolean {
  if (!url) return false;
  if (!isFirstPartyUrl(url)) return false;
  const clean = url.split('#')[0] ?? url;
  const path = clean.split('?')[0] ?? clean;
  // isFirstPartyUrl already rejected protocol-relative and foreign hosts;
  // strip any origin so the path predicates see only the API path.
  return isProtectedPath(toApiPath(path));
}

/** Extract a file name from a download URL path (last non-empty segment). */
export function filenameFromUrl(url: string): string {
  const cleaned = url.split('?')[0] ?? url;
  const path = toApiPath(cleaned);
  const segments = path.split('/').filter(Boolean);
  const last = segments[segments.length - 1] ?? 'download';
  return decodeURIComponent(last);
}

/**
 * Download a protected file through the transport and trigger the browser
 * download. The filename comes from the server's Content-Disposition when
 * present, otherwise from the URL path, otherwise the explicit fallback.
 */
export async function downloadWithAuth(
  url: string,
  options: ApiFetchOptions & { filename?: string } = {},
): Promise<void> {
  // apiFetchBlob's buildRequest prepends API_BASE — hand it the origin-
  // relative path, never the absolute URL (double-prefix bug).
  const path = toApiPath(url);
  const { blob, filename: dispositionName } = await apiFetchBlob(path, options);
  const finalName =
    options.filename ??
    dispositionName ??
    filenameFromUrl(url);
  triggerBlobDownload(blob, finalName);
}

/**
 * Trigger a browser save for a Blob. Mirrors `downloadBlob` in map-kit/exporter
 * without pulling that heavy module into the transport layer.
 */
export function triggerBlobDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.style.display = 'none';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
