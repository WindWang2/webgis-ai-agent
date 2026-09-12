'use client';

import React from 'react';
import { STitle } from '@/components/shared/section-title';
import { API_BASE } from '@/lib/api/config';
import { LOCALE_LABELS, SUPPORTED_LOCALES, type AppLocale } from '@/lib/i18n/config';
import { useLocale, useSetLocale, useT } from '@/lib/i18n/useT';

/**
 * 系统设置面板。
 *
 * 审计 findings.md：之前此面板是一个 mock —— handleSave 只切 saved 状态不持久化，
 * API URL 字段无法覆盖构建时 NEXT_PUBLIC_API_URL，语言切换器无 i18n 后端。
 *
 * 现改为只读的真实系统信息展示：
 * - API URL 从 lib/api/config.ts 的 API_BASE 读取（构建时确定），只读展示
 * - 语言切换器接入 i18n 框架（ADR-0144）：即时生效、persist + cookie 记忆；
 *   append-only：保留原有行位置与双按钮形态，仅启用交互
 * - 版本号从 package.json 同步（通过构建时注入）
 * - 移除 fake Save 按钮 —— 没有可持久化的状态
 */
export function SystemSettings() {
  const t = useT('settings');
  const locale = useLocale();
  const setLocale = useSetLocale();

  return (
    <div className="flex flex-col gap-5">
      <STitle title={t('system.title')} sub={t('system.subtitle')} />

      {/* Backend API URL — read-only, determined at build time */}
      <div>
        <div className="text-title uppercase tracking-wide text-ink-muted font-medium mb-2">
          {t('system.backendApiUrl')}
        </div>
        <div
          className="rounded-md border border-edge-subtle bg-surface-sunken px-3 py-2 text-body font-mono text-ink-secondary"
          aria-label={t('system.backendApiReadonlyAria')}
        >
          {API_BASE}
        </div>
        <div className="text-body text-ink-muted mt-1">
          {t('system.backendApiHint')}
        </div>
      </div>

      {/* Language selection — i18n（ADR-0144）：切换即时生效，persist + cookie 记忆 */}
      <div>
        <div className="text-title uppercase tracking-wide text-ink-muted font-medium mb-2">
          {t('system.language')}
        </div>
        <div className="flex gap-2" role="radiogroup" aria-label={t('system.language')}>
          {SUPPORTED_LOCALES.map((l: AppLocale) => {
            const active = l === locale;
            return (
              <button
                key={l}
                role="radio"
                aria-checked={active}
                onClick={() => setLocale(l)}
                className="flex-1 cursor-pointer rounded-md border-2 py-2 text-body font-medium transition-colors"
                style={
                  active
                    ? {
                        borderColor: 'var(--agent-accent, #16a34a)',
                        backgroundColor:
                          'color-mix(in srgb, var(--agent-accent, #16a34a) 4%, transparent)',
                        color: 'var(--agent-accent)',
                      }
                    : {
                        borderColor: 'var(--edge-subtle, #e5e7eb)',
                        backgroundColor: 'var(--surface-raised, #ffffff)',
                        color: 'var(--text-secondary, #4b5563)',
                      }
                }
              >
                {LOCALE_LABELS[l]}
              </button>
            );
          })}
        </div>
        <div className="text-body text-ink-muted mt-1">{t('system.languageHint')}</div>
      </div>

      {/* About section — version from build-time injection */}
      <div className="rounded-md border border-edge-subtle bg-surface-raised px-4 py-3">
        <div className="flex items-center gap-2.5 mb-2">
          <div
            className="flex h-7 w-7 items-center justify-center rounded-md"
            style={{
              background:
                'linear-gradient(135deg, var(--agent-accent, #16a34a) 0%, color-mix(in srgb, var(--agent-accent, #16a34a) 72%, #ffffff) 100%)',
            }}
          >
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="var(--text-on-accent)"
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <circle cx="12" cy="12" r="10" />
              <path d="M12 6v6l4 2" />
            </svg>
          </div>
          <div>
            <div className="text-heading font-bold text-ink">
              GeoAgent
            </div>
            <div className="text-body font-mono text-ink-muted">
              v{process.env.NEXT_PUBLIC_APP_VERSION || '0.1.2'}
            </div>
          </div>
        </div>
        <div className="text-body text-ink-muted italic">
          &quot;All is Agent&quot;
        </div>
      </div>
    </div>
  );
}
