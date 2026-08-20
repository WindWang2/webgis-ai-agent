import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import '@testing-library/jest-dom'
import { PoiInfoPanel } from './poi-info-panel'
import { useHudStore } from '@/lib/store/useHudStore'

const layerIds = new Set(['ref:big-1'])
const layersMap = { 'ref:big-1': { id: 'ref:big-1', name: 'Big MVT' } }

function makeFeature(props: Record<string, unknown>, sub = 'ref:big-1__point') {
  return { layer: { id: sub }, properties: props }
}

describe('A1 TDD — PoiInfoPanel live sync to authoritative backfill', () => {
  beforeEach(() => {
    useHudStore.setState({ selectedFeature: null } as any)
    vi.clearAllMocks()
  })

  it('after backfill, panel reflects corrected properties from selectedFeature (not stale tile props)', () => {
    // Initial tile-derived feature (stale)
    const staleFeature = makeFeature({ id: 'feat-42', name: 'clip', pop: 10 })
    // Authoritative correction now in store (as commitSelection would merge)
    useHudStore.setState({
      selectedFeature: {
        layerId: 'ref:big-1',
        layerName: 'Big MVT',
        point: [116.4, 39.9] as [number, number],
        properties: { id: 'feat-42', name: 'full-truth', pop: 999, extra: 'yes' },
        selectedAt: Date.now(),
        featureId: 'feat-42',
        bbox: [116.4, 39.9, 116.41, 39.91] as [number, number, number, number],
        isApproximate: false,
      },
    } as any)

    render(
      <PoiInfoPanel
        x={100}
        y={300}
        features={[staleFeature]}
        layerIds={layerIds}
        layersMap={layersMap}
        onClose={() => {}}
      />,
    )

    // Panel must show corrected truth, not stale tile props
    expect(screen.getAllByText('full-truth').length).toBeGreaterThan(0)
    expect(screen.getByText('999')).toBeInTheDocument()
    // extra prop should be visible (authoritative adds it)
    expect(screen.getByText('yes')).toBeInTheDocument()
    // stale values must NOT be the rendered ones as title
    expect(screen.queryByText('clip')).not.toBeInTheDocument()
    // also old pop 10 should not be present as standalone value (999 is new)
    // we check that at least one full-truth exists
    const titles = screen.getAllByText(/full-truth|clip/)
    expect(titles.some(el => el.textContent === 'full-truth')).toBe(true)
  })

  it('without selectedFeature correction, panel still shows tile props (fallback)', () => {
    useHudStore.setState({ selectedFeature: null } as any)
    render(
      <PoiInfoPanel
        x={100}
        y={300}
        features={[makeFeature({ name: 'clip' })]}
        layerIds={layerIds}
        layersMap={layersMap}
        onClose={() => {}}
      />,
    )
    expect(screen.getAllByText('clip').length).toBeGreaterThan(0)
  })

  it('multi-entry with mismatched id does NOT merge authoritative props onto wrong entry', async () => {
    const { fireEvent } = await import('@testing-library/react')
    // Two tile features at same point: A and B
    const tileA = makeFeature({ id: 'feat-A', name: 'A-tile', pop: 1 })
    const tileB = makeFeature({ id: 'feat-B', name: 'B-tile', pop: 2 })
    // Only B was selected and backfilled to B-auth
    useHudStore.setState({
      selectedFeature: {
        layerId: 'ref:big-1',
        layerName: 'Big MVT',
        point: [116.4, 39.9] as [number, number],
        properties: { id: 'feat-B', name: 'B-auth', pop: 999 },
        selectedAt: Date.now(),
        featureId: 'feat-B',
        bbox: null,
        isApproximate: false,
      },
    } as any)
    render(
      <PoiInfoPanel
        x={100}
        y={300}
        features={[tileA, tileB]}
        layerIds={layerIds}
        layersMap={layersMap}
        onClose={() => {}}
      />,
    )
    // List shows both tile titles (B-auth title only after merge for entry 1)
    // Click entry A (first) — detail must show A-tile, NOT B-auth
    const entryABtn = screen.getByText('A-tile')
    fireEvent.click(entryABtn)
    expect(screen.getAllByText('A-tile').length).toBeGreaterThan(0)
    expect(screen.queryByText('B-auth')).not.toBeInTheDocument()
    expect(screen.getByText('1')).toBeInTheDocument() // pop 1 from tile, not 999
    // Go back and pick B — now should show B-auth
    fireEvent.click(screen.getByText('← 返回要素列表'))
    const entryBBtn = screen.getByText('B-auth')
    fireEvent.click(entryBBtn)
    expect(screen.getAllByText('B-auth').length).toBeGreaterThan(0)
    expect(screen.getByText('999')).toBeInTheDocument()
  })
})
