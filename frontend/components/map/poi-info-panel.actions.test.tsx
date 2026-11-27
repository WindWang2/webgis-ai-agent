import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import '@testing-library/jest-dom'
import { PoiInfoPanel } from './poi-info-panel'
import { useHudStore } from '@/lib/store/useHudStore'

const layerIds = new Set(['ref:poi-layer'])
const layersMap = { 'ref:poi-layer': { id: 'ref:poi-layer', name: '成都热门地标' } }

function makeFeature(props: Record<string, unknown>, coords?: [number, number]) {
  return {
    layer: { id: 'ref:poi-layer__point' },
    properties: props,
    geometry: coords ? { type: 'Point', coordinates: coords } : undefined,
  }
}

describe('PoiInfoPanel — Elevated Spatial Inspection Popover', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useHudStore.setState({ selectedFeature: null } as any)
  })

  it('renders one-click copy coordinates button and copies formatted lng/lat to clipboard', async () => {
    const writeTextMock = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, {
      clipboard: {
        writeText: writeTextMock,
      },
    })

    render(
      <PoiInfoPanel
        x={200}
        y={300}
        coordinates={[104.0665, 30.5728]}
        features={[makeFeature({ name: '天府广场', category: '城市地标' }, [104.0665, 30.5728])]}
        layerIds={layerIds}
        layersMap={layersMap}
        onClose={() => {}}
      />,
    )

    expect(screen.getAllByText('天府广场').length).toBeGreaterThan(0)
    expect(screen.getByText(/104.06650, 30.57280/)).toBeInTheDocument()

    const copyBtn = screen.getByRole('button', { name: '复制坐标' })
    expect(copyBtn).toBeInTheDocument()

    await act(async () => {
      fireEvent.click(copyBtn)
    })

    expect(writeTextMock).toHaveBeenCalledWith('104.066500, 30.572800')
    expect(screen.getByRole('button', { name: '已复制经纬度坐标' })).toBeInTheDocument()
  })

  it('renders "聚焦位置" action button and invokes onZoomToFeature with coordinates', () => {
    const onZoomToFeature = vi.fn()

    render(
      <PoiInfoPanel
        x={200}
        y={300}
        coordinates={[104.0665, 30.5728]}
        features={[makeFeature({ name: '春熙路' }, [104.0665, 30.5728])]}
        layerIds={layerIds}
        layersMap={layersMap}
        onClose={() => {}}
        onZoomToFeature={onZoomToFeature}
      />,
    )

    const zoomBtn = screen.getByRole('button', { name: '聚焦位置' })
    expect(zoomBtn).toBeInTheDocument()

    fireEvent.click(zoomBtn)
    expect(onZoomToFeature).toHaveBeenCalledWith([104.0665, 30.5728])
  })

  it('invokes onZoomToFeature with bbox when authoritative bbox exists on selectedFeature', () => {
    const onZoomToFeature = vi.fn()
    useHudStore.setState({
      selectedFeature: {
        layerId: 'ref:poi-layer',
        featureId: 'poi-1',
        point: [104.06, 30.57],
        bbox: [104.05, 30.56, 104.07, 30.58],
        properties: { id: 'poi-1', name: '成都大熊猫繁育研究基地' },
      },
    } as any)

    render(
      <PoiInfoPanel
        x={200}
        y={300}
        features={[makeFeature({ id: 'poi-1', name: '熊猫基地' })]}
        layerIds={layerIds}
        layersMap={layersMap}
        onClose={() => {}}
        onZoomToFeature={onZoomToFeature}
      />,
    )

    const zoomBtn = screen.getByRole('button', { name: '聚焦位置' })
    fireEvent.click(zoomBtn)
    expect(onZoomToFeature).toHaveBeenCalledWith([104.05, 30.56, 104.07, 30.58])
  })

  it('copies property value on clicking individual attribute rows', async () => {
    const writeTextMock = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, {
      clipboard: {
        writeText: writeTextMock,
      },
    })

    render(
      <PoiInfoPanel
        x={200}
        y={300}
        features={[makeFeature({ name: '宽窄巷子', code: 'POI-9988' })]}
        layerIds={layerIds}
        layersMap={layersMap}
        onClose={() => {}}
      />,
    )

    const codeRow = screen.getByText('POI-9988')
    await act(async () => {
      fireEvent.click(codeRow)
    })

    expect(writeTextMock).toHaveBeenCalledWith('POI-9988')
  })

  it('clipboard 写入拒绝时不虚报「已复制」', async () => {
    const writeTextMock = vi.fn().mockRejectedValue(new Error('denied'))
    Object.assign(navigator, { clipboard: { writeText: writeTextMock } })

    render(
      <PoiInfoPanel
        x={200}
        y={300}
        coordinates={[104.0665, 30.5728]}
        features={[makeFeature({ name: '天府广场' }, [104.0665, 30.5728])]}
        layerIds={layerIds}
        layersMap={layersMap}
        onClose={() => {}}
      />,
    )

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '复制坐标' }))
    })

    expect(writeTextMock).toHaveBeenCalled()
    // 权限拒绝/非安全上下文：保持未复制反馈，不谎报成功。
    expect(screen.queryByRole('button', { name: '已复制经纬度坐标' })).toBeNull()
    expect(screen.getByRole('button', { name: '复制坐标' })).toBeInTheDocument()
  })

  it('卸载时清除复制反馈定时器（坐标 1800ms / 属性 1500ms）', async () => {
    vi.useFakeTimers()
    try {
      const writeTextMock = vi.fn().mockResolvedValue(undefined)
      Object.assign(navigator, { clipboard: { writeText: writeTextMock } })
      const { unmount } = render(
        <PoiInfoPanel
          x={200}
          y={300}
          coordinates={[104.0665, 30.5728]}
          features={[makeFeature({ name: '宽窄巷子', code: 'POI-9988' }, [104.0665, 30.5728])]}
          layerIds={layerIds}
          layersMap={layersMap}
          onClose={() => {}}
        />,
      )
      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: '复制坐标' }))
        fireEvent.click(screen.getByText('POI-9988'))
      })
      expect(vi.getTimerCount()).toBeGreaterThan(0)
      unmount()
      // 修复前：在途 1800/1500 定时器未清理，卸载后仍留在 timer 队列。
      expect(vi.getTimerCount()).toBe(0)
    } finally {
      vi.useRealTimers()
    }
  })
})
