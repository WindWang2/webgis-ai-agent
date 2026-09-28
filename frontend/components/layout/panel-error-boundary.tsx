'use client';

import React, { Component, type ReactNode } from 'react';
import { t } from '@/lib/i18n/t';

interface PanelErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

/**
 * Scoped error boundary for one sidebar panel (FE-P3-6).
 *
 * Before this, only the map region and the global boundary existed — a render
 * exception inside any sidebar tab (e.g. a malformed result payload reaching
 * the results workbench) blanked the ENTIRE app via the reload page and
 * destroyed the user's in-flight chat state. Panels are independent surfaces;
 * one failing must not take the others down.
 *
 * Recovery is a local remount: transient render errors (partial payloads,
 * mid-session state) usually clear when the tab is re-entered; a full reload
 * would discard chat/session state.
 */
export class PanelErrorBoundary extends Component<
  { children: ReactNode; label: string },
  PanelErrorBoundaryState
> {
  state: PanelErrorBoundaryState = { hasError: false, error: null };

  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error };
  }

  private handleRetry = () => {
    this.setState({ hasError: false, error: null });
  };

  render() {
    if (!this.state.hasError) return this.props.children;

    return (
      <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
        <div className="text-title font-medium text-ink">
          {t('layout.panelError.title', { label: this.props.label })}
        </div>
        <div className="max-w-[280px] text-body text-ink-muted">
          {this.state.error?.message ?? t('layout.panelError.fallback')}
        </div>
        <button
          type="button"
          onClick={this.handleRetry}
          className="rounded-md border border-edge-subtle bg-surface-raised px-3 py-1.5 text-body font-medium text-ink-secondary transition-colors hover:bg-surface-hover"
        >
          {t('layout.panelError.retry')}
        </button>
      </div>
    );
  }
}
