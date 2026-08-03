/**
 * Regression tests for the map-scoped error boundary.
 *
 * Before this boundary existed, a MapLibre failure ("Style is not done loading"
 * when the tile CDN is unreachable) bubbled to the top-level ErrorBoundary in
 * client-providers.tsx and replaced the entire app with a full-screen
 * "System Error" page — killing chat and analysis along with the map.
 *
 * These tests pin three properties:
 *   1. A throwing child is contained; the fallback renders instead of crashing.
 *   2. Tile/network failures get a network-specific message, not a raw stack.
 *   3. Retry clears the error locally (no window.location.reload, which would
 *      discard chat history).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MapErrorBoundary } from './map-error-boundary';

function Boom({ message }: { message: string }): never {
  throw new Error(message);
}

describe('MapErrorBoundary', () => {
  beforeEach(() => {
    // React logs caught boundary errors to console.error; silence the expected noise.
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('renders children when nothing throws', () => {
    render(
      <MapErrorBoundary>
        <div>map canvas</div>
      </MapErrorBoundary>
    );

    expect(screen.getByText('map canvas')).toBeInTheDocument();
    expect(screen.queryByText(/map unavailable/i)).not.toBeInTheDocument();
  });

  it('contains a throwing child instead of letting the error escape', () => {
    render(
      <MapErrorBoundary>
        <Boom message='Style is not done loading' />
      </MapErrorBoundary>
    );

    expect(screen.getByText(/map unavailable/i)).toBeInTheDocument();
  });

  it('explains a tile-load failure as a network problem, not a crash', () => {
    render(
      <MapErrorBoundary>
        <Boom message='Style is not done loading' />
      </MapErrorBoundary>
    );

    expect(screen.getByText(/basemap tiles could not be loaded/i)).toBeInTheDocument();
    // The whole point of scoping the boundary: the rest of the app survives.
    expect(screen.getByText(/chat and analysis still work/i)).toBeInTheDocument();
  });

  it('surfaces the raw message for non-network failures', () => {
    render(
      <MapErrorBoundary>
        <Boom message='layer id collision: eq' />
      </MapErrorBoundary>
    );

    expect(screen.getByText('layer id collision: eq')).toBeInTheDocument();
    expect(screen.queryByText(/basemap tiles could not be loaded/i)).not.toBeInTheDocument();
  });

  it('clears the error state on retry so a recovered map can remount', () => {
    // Throw on first render, succeed after retry — mirrors a transient tile failure.
    let shouldThrow = true;
    function Flaky() {
      if (shouldThrow) throw new Error('Style is not done loading');
      return <div>map canvas</div>;
    }

    render(
      <MapErrorBoundary>
        <Flaky />
      </MapErrorBoundary>
    );

    expect(screen.getByText(/map unavailable/i)).toBeInTheDocument();

    shouldThrow = false;
    fireEvent.click(screen.getByRole('button', { name: /retry map/i }));

    expect(screen.getByText('map canvas')).toBeInTheDocument();
    expect(screen.queryByText(/map unavailable/i)).not.toBeInTheDocument();
  });
});
