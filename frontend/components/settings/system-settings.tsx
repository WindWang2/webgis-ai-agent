'use client';

import React from 'react';
import { STitle } from '@/components/shared/section-title';
import { API_BASE } from '@/lib/api/config';

/**
 * 系统设置面板。
 *
 * 审计 findings.md：之前此面板是一个 mock —— handleSave 只切 saved 状态不持久化，
 * API URL 字段无法覆盖构建时 NEXT_PUBLIC_API_URL，语言切换器无 i18n 后端。
 *
 * 现改为只读的真实系统信息展示：
 * - API URL 从 lib/api/config.ts 的 API_BASE 读取（构建时确定），只读展示
 * - 语言切换器标记为"规划中"并禁用（项目暂无 i18n 系统）
 * - 版本号从 package.json 同步（通过构建时注入）
 * - 移除 fake Save 按钮 —— 没有可持久化的状态
 */
export function SystemSettings() {
  return (
    <div className="flex flex-col gap-5">
      <STitle title="系统设置" sub="System Settings" />

      {/* Backend API URL — read-only, determined at build time */}
      <div>
        <div className="text-[14px] uppercase tracking-wide text-[var(--theme-text-muted)] font-medium mb-2">
          Backend API URL
        </div>
        <div
          className="rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-input)] px-3 py-2 text-[14px] font-mono text-[var(--theme-text-secondary)]"
          aria-label="后端 API 地址（只读，由构建时环境变量决定）"
        >
          {API_BASE}
        </div>
        <div className="text-[15px] text-[var(--theme-text-muted)] mt-1">
          由 <code className="text-[15px] text-[var(--theme-text-secondary)]">NEXT_PUBLIC_API_URL</code> 环境变量在构建时确定，运行时不可修改。
        </div>
      </div>

      {/* Language selection — disabled, i18n not yet implemented */}
      <div>
        <div className="flex items-center gap-2 mb-2">
          <span className="text-[14px] uppercase tracking-wide text-[var(--theme-text-muted)] font-medium">
            Language / 语言
          </span>
          <span
            className="text-[14px] rounded-full px-1.5 py-0.5 bg-[var(--theme-bg-muted)] text-[var(--theme-text-muted)] font-medium"
            title="国际化系统尚未实现"
          >
            规划中
          </span>
        </div>
        <div className="flex gap-2 opacity-50" aria-disabled="true">
          <button
            disabled
            className="flex-1 rounded-lg border-2 py-2 text-[14px] font-medium cursor-not-allowed"
            style={{
              borderColor: 'var(--agent-accent, #16a34a)',
              backgroundColor: 'color-mix(in srgb, var(--agent-accent, #16a34a) 4%, transparent)',
              color: 'var(--agent-accent, #16a34a)',
            }}
          >
            中文
          </button>
          <button
            disabled
            className="flex-1 rounded-lg border-2 py-2 text-[14px] font-medium cursor-not-allowed"
            style={{
              borderColor: 'var(--theme-border)',
              backgroundColor: 'var(--theme-bg-subtle)',
              color: 'var(--theme-text-secondary)',
            }}
          >
            English
          </button>
        </div>
      </div>

      {/* About section — version from build-time injection */}
      <div className="rounded-xl border border-[var(--theme-border)] bg-[var(--theme-bg-subtle)] px-4 py-3">
        <div className="flex items-center gap-2.5 mb-2">
          <div
            className="flex items-center justify-center rounded-lg"
            style={{
              width: 28,
              height: 28,
              background:
                'linear-gradient(135deg, var(--agent-accent, #16a34a) 0%, color-mix(in srgb, var(--agent-accent, #16a34a) 72%, #ffffff) 100%)',
            }}
          >
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="white"
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <circle cx="12" cy="12" r="10" />
              <path d="M12 6v6l4 2" />
            </svg>
          </div>
          <div>
            <div className="text-[15px] font-bold text-[var(--theme-text-primary)]">
              GeoAgent
            </div>
            <div className="text-[14px] text-[var(--theme-text-muted)] font-mono">
              v{process.env.NEXT_PUBLIC_APP_VERSION || '0.1.2'}
            </div>
          </div>
        </div>
        <div className="text-[15px] text-[var(--theme-text-muted)] italic">
          &quot;All is Agent&quot;
        </div>
      </div>
    </div>
  );
}
