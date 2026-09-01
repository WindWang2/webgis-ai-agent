import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import '@testing-library/jest-dom'
import { MapToolbarHUD } from './map-toolbar-hud'
import { useHudStore } from '@/lib/store/useHudStore'

describe('MapToolbarHUD — Floating GIS Navigation & Measurement Toolbar', () => {
  const mockZoomIn = vi.fn()
  const mockZoomOut = vi.fn()
  const mockResetNorthPitch = vi.fn()
  const mockEaseTo = vi.fn()
  const mockGetZoom = vi.fn(() => 5)
  const mockZoomTo = vi.fn()

  const fakeMapInstance = {
    zoomIn: mockZoomIn,
    zoomOut: mockZoomOut,
    resetNorthPitch: mockResetNorthPitch,
    easeTo: mockEaseTo,
    getZoom: mockGetZoom,
    zoomTo: mockZoomTo,
  }

  const mapRef = {
    current: {
      getMap: () => fakeMapInstance,
    },
  } as any

  beforeEach(() => {
    vi.clearAllMocks()
    useHudStore.setState({
      is3D: false,
      annotations: [],
    } as any)
  })

  it('renders all essential GIS toolbar navigation & measurement buttons', () => {
    render(<MapToolbarHUD mapRef={mapRef} />)

    expect(screen.getByRole('button', { name: '放大' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '缩小' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重置指北与俯仰角' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '切换3D视图' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '距离测量' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '面积测量' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '清除标注与测量' })).toBeInTheDocument()
  })

  it('triggers map zoom in and zoom out when clicked', () => {
    render(<MapToolbarHUD mapRef={mapRef} />)

    fireEvent.click(screen.getByRole('button', { name: '放大' }))
    expect(mockZoomIn).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByRole('button', { name: '缩小' }))
    expect(mockZoomOut).toHaveBeenCalledTimes(1)
  })

  it('triggers reset north & pitch when orientation button is clicked', () => {
    render(<MapToolbarHUD mapRef={mapRef} bearing={45} pitch={30} />)

    fireEvent.click(screen.getByRole('button', { name: '重置指北与俯仰角' }))
    expect(mockResetNorthPitch).toHaveBeenCalledTimes(1)
    expect(screen.getByText('已重置正北与俯仰角')).toBeInTheDocument()
  })

  it('toggles 2D / 3D terrain mode and updates useHudStore', () => {
    render(<MapToolbarHUD mapRef={mapRef} />)

    expect(useHudStore.getState().is3D).toBe(false)
    const btn = screen.getByRole('button', { name: '切换3D视图' })
    fireEvent.click(btn)

    expect(useHudStore.getState().is3D).toBe(true)
    expect(screen.getByText('已开启 3D 地形视角')).toBeInTheDocument()

    fireEvent.click(btn)
    expect(useHudStore.getState().is3D).toBe(false)
    expect(screen.getByText('已切换为 2D 平面视角')).toBeInTheDocument()
  })

  it('activates distance measurement mode and computes live distance', () => {
    const onMeasureToolChange = vi.fn()
    const { rerender } = render(
      <MapToolbarHUD
        mapRef={mapRef}
        activeMeasureTool="distance"
        onMeasureToolChange={onMeasureToolChange}
        measurePoints={[
          [116.4, 39.9],
          [116.45, 39.95],
        ]}
      />,
    )

    expect(screen.getByTestId('measurement-active-hud')).toBeInTheDocument()
    expect(screen.getByText('距离测量模式')).toBeInTheDocument()
    expect(screen.getByText('已采集点数:')).toBeInTheDocument()
    expect(screen.getByText('2 个')).toBeInTheDocument()
    expect(screen.getByText(/km|m/)).toBeInTheDocument()

    // Add a 3rd point
    rerender(
      <MapToolbarHUD
        mapRef={mapRef}
        activeMeasureTool="distance"
        onMeasureToolChange={onMeasureToolChange}
        measurePoints={[
          [116.4, 39.9],
          [116.45, 39.95],
          [116.5, 39.9],
        ]}
      />,
    )
    expect(screen.getByText('3 个')).toBeInTheDocument()
  })

  it('activates area measurement mode and computes live polygon area', () => {
    render(
      <MapToolbarHUD
        mapRef={mapRef}
        activeMeasureTool="area"
        measurePoints={[
          [116.4, 39.9],
          [116.45, 39.9],
          [116.45, 39.95],
          [116.4, 39.95],
        ]}
      />,
    )

    expect(screen.getByTestId('measurement-active-hud')).toBeInTheDocument()
    expect(screen.getByText('面积测量模式')).toBeInTheDocument()
    expect(screen.getByText('4 个')).toBeInTheDocument()
    expect(screen.getByText(/km²|m²/)).toBeInTheDocument()
  })

  it('saves completed measurement annotation to useHudStore', () => {
    const onComplete = vi.fn()
    render(
      <MapToolbarHUD
        mapRef={mapRef}
        activeMeasureTool="distance"
        measurePoints={[
          [116.4, 39.9],
          [116.45, 39.95],
        ]}
        onCompleteMeasurement={onComplete}
      />,
    )

    const finishBtn = screen.getByRole('button', { name: '完成标注' })
    expect(finishBtn).not.toBeDisabled()
    fireEvent.click(finishBtn)

    expect(onComplete).toHaveBeenCalledTimes(1)
  })

  it('clears all annotations and measurement points when clear button is clicked', () => {
    useHudStore.setState({
      annotations: [
        {
          type: 'Feature',
          geometry: { type: 'Point', coordinates: [116.4, 39.9] },
          properties: { label: 'Test Marker' },
        },
      ],
    } as any)

    const onClearPoints = vi.fn()
    render(<MapToolbarHUD mapRef={mapRef} onClearMeasurePoints={onClearPoints} />)

    const clearBtn = screen.getByRole('button', { name: '清除标注与测量' })
    expect(clearBtn).not.toBeDisabled()

    fireEvent.click(clearBtn)
    expect(useHudStore.getState().annotations).toEqual([])
    expect(onClearPoints).toHaveBeenCalledTimes(1)
    expect(screen.getByText('已清除测量与标注')).toBeInTheDocument()
  })

  it('responds to keyboard shortcuts (+, -, 0, 3, d, a, Escape)', () => {
    render(<MapToolbarHUD mapRef={mapRef} />)

    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: '+' }))
    })
    expect(mockZoomIn).toHaveBeenCalledTimes(1)

    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: '-' }))
    })
    expect(mockZoomOut).toHaveBeenCalledTimes(1)

    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: '0' }))
    })
    expect(mockResetNorthPitch).toHaveBeenCalledTimes(1)

    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: '3' }))
    })
    expect(useHudStore.getState().is3D).toBe(true)

    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'd' }))
    })
    expect(screen.getByTestId('measurement-active-hud')).toBeInTheDocument()

    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    })
    expect(screen.queryByTestId('measurement-active-hud')).not.toBeInTheDocument()
  })

  it('toggles collapse state on compact mobile viewports', () => {
    render(<MapToolbarHUD mapRef={mapRef} />)

    const collapseBtn = screen.getByRole('button', { name: '折叠工具栏' })
    fireEvent.click(collapseBtn)

    expect(screen.queryByRole('button', { name: '放大' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '展开工具栏' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '展开工具栏' }))
    expect(screen.getByRole('button', { name: '放大' })).toBeInTheDocument()
  })
})
