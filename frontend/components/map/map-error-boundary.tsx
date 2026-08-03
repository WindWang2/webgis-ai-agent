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

    return (
      <div className='absolute inset-0 flex items-center justify-center bg-[#dce8f2]'>
        <div className='max-w-sm space-y-3 px-6 text-center'>
          <div className='font-mono text-xs uppercase tracking-widest text-slate-500'>
            Map Unavailable
          </div>
          <p className='text-sm text-slate-600'>
            {isStyleLoadError
              ? 'The basemap tiles could not be loaded. Check your network connection or tile provider.'
              : message}
          </p>
          <p className='text-xs text-slate-400'>
            Chat and analysis still work. Layers will render once the map recovers.
          </p>
          <button
            onClick={this.handleRetry}
            className='rounded-lg border border-slate-300 px-4 py-2 text-sm text-slate-600 transition-colors hover:bg-slate-200/60'
          >
            Retry Map
          </button>
        </div>
      </div>
    );
  }
}
