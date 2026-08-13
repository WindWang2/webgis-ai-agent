'use client';

import React, { Component, type ReactNode } from 'react';

interface MapErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

/**
 * Scoped error boundary for the map region.
 *
 * Without this, a MapLibre failure (most commonly "Style is not done loading"
 * when the tile CDN is unreachable) propagates to the top-level ErrorBoundary in
 * client-providers.tsx and takes down the whole app — chat, layers, and analysis
 * panels included. The map is the least essential part of the page for a text
 * conversation, so it should fail alone.
 *
 * Recovery is a local remount (`key` bump) rather than `window.location.reload()`:
 * transient tile/network failures usually clear on retry, and a full reload would
 * discard the user's chat history and session state.
 */
export class MapErrorBoundary extends Component<{ children: ReactNode }, MapErrorBoundaryState> {
  state: MapErrorBoundaryState = { hasError: false, error: null };

  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error };
  }

  private handleRetry = () => {
    this.setState({ hasError: false, error: null });
  };

  render() {
    if (!this.state.hasError) return this.props.children;

    const message = this.state.error?.message ?? 'Unknown map error';
    // MapLibre surfaces an unreachable tile/style endpoint as "Style is not done
    // loading". Name the network cause so the user doesn't read it as data loss.
    const isStyleLoadError = /style is not done loading|failed to fetch|networkerror/i.test(message);

    // Tokenized (bg-surface-canvas / text-ink-*): the fallback used hardcoded
    // light colours and would render as a white block over the map under the
    // dark theme.
    return (
      <div className='absolute inset-0 flex items-center justify-center bg-surface-canvas'>
        <div className='max-w-sm space-y-3 px-6 text-center'>
          <div className='font-mono text-xs uppercase tracking-widest text-ink-muted'>
            Map Unavailable
          </div>
          <p className='text-body text-ink-secondary'>
            {isStyleLoadError
              ? 'The basemap tiles could not be loaded. Check your network connection or tile provider.'
              : message}
          </p>
          <p className='text-meta text-ink-muted'>
            Chat and analysis still work. Layers will render once the map recovers.
          </p>
          <button
            onClick={this.handleRetry}
            className='rounded-md border border-edge px-4 py-2 text-body text-ink-secondary transition-colors hover:bg-surface-hover'
          >
            Retry Map
          </button>
        </div>
      </div>
    );
  }
}
