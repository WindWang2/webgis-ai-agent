'use client';

import React, { useState } from 'react';
import { STitle } from '@/components/shared/section-title';
import { API_BASE } from '@/lib/api/config';
import { useOnboardingStore } from '@/lib/onboarding/use-onboarding';
import { SystemHealthPanel } from '@/components/sidebar/ops/system-health-panel';
import { LOCALE_LABELS, SUPPORTED_LOCALES, type AppLocale } from '@/lib/i18n/config';
import { useLocale, useSetLocale, useT } from '@/lib/i18n/useT';

type SystemSettingsTab = 'general' | 'cluster-health';

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
 *
 * ADR-0142（ops-console-v9）：append 唯一一个 tab 入口「集群健康」——
 * 挂载运维控制台的系统健康分区（/health、/version、/status/detailed、
 * durable 队列深度双口径、通道自检）。不改动 settings-panel 的 NAV_ITEMS。
 */
export function SystemSettings() {
  const t = useT('settings');
  const locale = useLocale();
  const setLocale = useSetLocale();
  const [tab, setTab] = useState<SystemSettingsTab>('general');

  return (
    <div className="flex flex-col gap-5">
      <STitle title={t('system.title')} sub={t('system.subtitle')} />

      {/* ADR-0142 唯一 append：二段 tab（常规 / 集群健康） */}
      <div role="tablist" aria-label={t('system.tabs.tablistAria')} className="flex items-center gap-1 border-b border-edge-subtle pb-1">
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'general'}
          onClick={() => setTab('general')}
          className={`rounded-sm px-2 py-1 text-meta font-medium transition-colors ${
            tab === 'general' ? 'bg-status-accent-soft text-status-accent' : 'text-ink-secondary hover:bg-surface-hover'
          }`}
        >
          {t('system.tabs.general')}
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'cluster-health'}
          data-testid="settings-tab-cluster-health"
          onClick={() => setTab('cluster-health')}
          className={`rounded-sm px-2 py-1 text-meta font-medium transition-colors ${
            tab === 'cluster-health' ? 'bg-status-accent-soft text-status-accent' : 'text-ink-secondary hover:bg-surface-hover'
          }`}
        >
          {t('system.tabs.clusterHealth')}
        </button>
      </div>

      {tab === 'cluster-health' && <SystemHealthPanel />}

      {tab === 'general' && (
      <>
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

      {/* ADR-0147（P6）：新手引导重看 / 提示重置入口 */}
      <div>
        <div className="text-title uppercase tracking-wide text-ink-muted font-medium mb-2">
          {t('system.onboarding.title')}
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            data-testid="restart-tour"
            onClick={() => useOnboardingStore.getState().startTour()}
            className="flex-1 rounded-md border px-3 py-2 text-body font-medium transition-colors"
            style={{
              borderColor: 'var(--agent-accent, #16a34a)',
              color: 'var(--agent-accent)',
            }}
          >
            {t('system.onboarding.restartTour')}
          </button>
          <button
            type="button"
            data-testid="reset-hints"
            onClick={() => useOnboardingStore.getState().resetHints()}
            className="rounded-md border border-edge-subtle px-3 py-2 text-body font-medium text-ink-secondary transition-colors hover:bg-surface-hover hover:text-ink"
          >
            {t('system.onboarding.resetHints')}
          </button>
        </div>
        <div className="text-body text-ink-muted mt-1">
          {t('system.onboarding.hint')}
        </div>
      </div>
      </>
      )}
    </div>
  );
}
