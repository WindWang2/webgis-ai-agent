'use client'

import React, { useState, useEffect, useCallback, useMemo } from 'react'
import {
  ZoomIn,
  ZoomOut,
  Compass,
  Layers,
  Box,
  Ruler,
  Square,
  Trash2,
  Check,
  X,
  ChevronUp,
  ChevronDown,
  RotateCcw,
  SquareDashedMousePointer,
} from 'lucide-react'
import type { MapRef } from 'react-map-gl/maplibre'
import { useHudStore, type HudState } from '@/lib/store/useHudStore'
import {
  haversineDistance,
  polygonAreaKm2,
  formatDistance,
  formatArea,
} from '@/lib/map-kit/navigation'

export type MeasureMode = 'none' | 'distance' | 'area'

export interface MapToolbarHUDProps {
  mapRef?: React.RefObject<MapRef | null>
  bearing?: number
  pitch?: number
  onFitExtent?: () => void
  activeMeasureTool?: MeasureMode
  onMeasureToolChange?: (mode: MeasureMode) => void
  measurePoints?: [number, number][]
  onClearMeasurePoints?: () => void
  onCompleteMeasurement?: () => void
  /** Runtime V4：矩形框选模式（跨视图 SelectionContext 发布）。 */
  brushSelectActive?: boolean
  onToggleBrushSelect?: () => void
  className?: string
}

const EMPTY_ANNOTATIONS: any[] = []
const EMPTY_POINTS: [number, number][] = []

export function MapToolbarHUD({
  mapRef,
  bearing = 0,
  pitch = 0,
  activeMeasureTool: controlledMeasureTool,
  onMeasureToolChange,
  measurePoints: controlledMeasurePoints,
  onClearMeasurePoints,
  onCompleteMeasurement,
  brushSelectActive,
  onToggleBrushSelect,
  className = '',
}: MapToolbarHUDProps) {
  // Local state fallbacks if uncontrolled
  const [uncontrolledMeasureTool, setUncontrolledMeasureTool] = useState<MeasureMode>('none')
  const [uncontrolledMeasurePoints, setUncontrolledMeasurePoints] = useState<[number, number][]>(EMPTY_POINTS)
  const [collapsed, setCollapsed] = useState(false)
  const [feedbackToast, setFeedbackToast] = useState<string | null>(null)

  const is3D = useHudStore((s: HudState) => Boolean(s?.is3D))
  const annotations = useHudStore((s: HudState) => s?.annotations ?? EMPTY_ANNOTATIONS)

  const activeMode = controlledMeasureTool !== undefined ? controlledMeasureTool : uncontrolledMeasureTool
  const points = useMemo(() => {
    if (Array.isArray(controlledMeasurePoints)) return controlledMeasurePoints
    if (Array.isArray(uncontrolledMeasurePoints)) return uncontrolledMeasurePoints
    return EMPTY_POINTS
  }, [controlledMeasurePoints, uncontrolledMeasurePoints])

  const showToast = useCallback((msg: string) => {
    setFeedbackToast(msg)
    const t = setTimeout(() => setFeedbackToast(null), 2000)
    return () => clearTimeout(t)
  }, [])

  const setMeasureMode = useCallback(
    (mode: MeasureMode) => {
      if (onMeasureToolChange) {
        onMeasureToolChange(mode)
      } else {
        setUncontrolledMeasureTool(mode)
      }
      if (mode === 'none') {
        if (onClearMeasurePoints) onClearMeasurePoints()
        else setUncontrolledMeasurePoints([])
      }
    },
    [onMeasureToolChange, onClearMeasurePoints],
  )

  // Map camera actions
  const handleZoomIn = useCallback(() => {
    const map = mapRef?.current?.getMap()
    if (map) {
      if (typeof map.zoomIn === 'function') {
        map.zoomIn({ duration: 250 })
      } else if (typeof map.getZoom === 'function' && typeof map.zoomTo === 'function') {
        map.zoomTo(map.getZoom() + 1)
      }
    }
  }, [mapRef])

  const handleZoomOut = useCallback(() => {
    const map = mapRef?.current?.getMap()
    if (map) {
      if (typeof map.zoomOut === 'function') {
        map.zoomOut({ duration: 250 })
      } else if (typeof map.getZoom === 'function' && typeof map.zoomTo === 'function') {
        map.zoomTo(map.getZoom() - 1)
      }
    }
  }, [mapRef])

  const handleResetNorthPitch = useCallback(() => {
    const map = mapRef?.current?.getMap()
    if (map) {
      if (typeof map.resetNorthPitch === 'function') {
        map.resetNorthPitch({ duration: 400 })
      } else if (typeof map.easeTo === 'function') {
        map.easeTo({ bearing: 0, pitch: 0, duration: 400 })
      }
      showToast('已重置正北与俯仰角')
    }
  }, [mapRef, showToast])

  const handleToggle3D = useCallback(() => {
    const next = !is3D
    useHudStore.getState().setIs3D(next)
    showToast(next ? '已开启 3D 地形视角' : '已切换为 2D 平面视角')
  }, [is3D, showToast])

  const handleClearAnnotations = useCallback(() => {
    useHudStore.getState().clearAnnotations()
    if (onClearMeasurePoints) onClearMeasurePoints()
    else setUncontrolledMeasurePoints([])
    showToast('已清除测量与标注')
  }, [onClearMeasurePoints, showToast])

  // Calculated live measurement values
  const measurementSummary = useMemo(() => {
    if (activeMode === 'distance') {
      if (points.length < 2) return null
      let totalKm = 0
      for (let i = 0; i < points.length - 1; i++) {
        totalKm += haversineDistance(points[i], points[i + 1])
      }
      return {
        type: 'distance' as const,
        rawValue: totalKm,
        formatted: formatDistance(totalKm),
        count: points.length,
      }
    }
    if (activeMode === 'area') {
      if (points.length < 3) return null
      const ring = points.slice()
      if (ring[0][0] !== ring[ring.length - 1][0] || ring[0][1] !== ring[ring.length - 1][1]) {
        ring.push([ring[0][0], ring[0][1]])
      }
      const areaKm2 = polygonAreaKm2(ring)
      return {
        type: 'area' as const,
        rawValue: areaKm2,
        formatted: formatArea(areaKm2),
        count: points.length,
      }
    }
    return null
  }, [activeMode, points])

  const handleSaveMeasurement = useCallback(() => {
    if (!measurementSummary) return
    if (onCompleteMeasurement) {
      onCompleteMeasurement()
    } else {
      // Self-contained fallback commit to useHudStore annotations
      const store = useHudStore.getState()
      if (measurementSummary.type === 'distance') {
        store.addAnnotation({
          type: 'Feature',
          geometry: { type: 'LineString', coordinates: points.slice() },
          properties: { label: `距离: ${measurementSummary.formatted}`, kind: 'measure_line' },
        })
        const end = points[points.length - 1]
        store.addAnnotation({
          type: 'Feature',
          geometry: { type: 'Point', coordinates: end.slice() },
          properties: { label: measurementSummary.formatted, color: 'transparent', kind: 'measure_label' },
        })
      } else if (measurementSummary.type === 'area') {
        const ring = points.slice()
        if (ring[0][0] !== ring[ring.length - 1][0] || ring[0][1] !== ring[ring.length - 1][1]) {
          ring.push([ring[0][0], ring[0][1]])
        }
        store.addAnnotation({
          type: 'Feature',
          geometry: { type: 'Polygon', coordinates: [ring] },
          properties: { label: `面积: ${measurementSummary.formatted}`, kind: 'measure_polygon' },
        })
        const cx = ring.reduce((s, p) => s + p[0], 0) / ring.length
        const cy = ring.reduce((s, p) => s + p[1], 0) / ring.length
        store.addAnnotation({
          type: 'Feature',
          geometry: { type: 'Point', coordinates: [cx, cy] },
          properties: { label: measurementSummary.formatted, color: 'transparent', kind: 'measure_label' },
        })
      }
      setMeasureMode('none')
    }
    showToast('测量标注已保存至地图')
  }, [measurementSummary, onCompleteMeasurement, points, setMeasureMode, showToast])

  // Keyboard shortcut listener
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const activeElement = document.activeElement
      const tagName = activeElement?.tagName.toLowerCase()
      if (
        tagName === 'input' ||
        tagName === 'textarea' ||
        activeElement?.getAttribute('contenteditable') === 'true'
      ) {
        return
      }

      if (e.key === '+' || e.key === '=') {
        e.preventDefault()
        handleZoomIn()
      } else if (e.key === '-' || e.key === '_') {
        e.preventDefault()
        handleZoomOut()
      } else if (e.key === '0' || e.key === 'n' || e.key === 'N') {
        e.preventDefault()
        handleResetNorthPitch()
      } else if (e.key === '3') {
        e.preventDefault()
        handleToggle3D()
      } else if (e.key === 'd' || e.key === 'D') {
        e.preventDefault()
        setMeasureMode(activeMode === 'distance' ? 'none' : 'distance')
      } else if (e.key === 'a' || e.key === 'A') {
        e.preventDefault()
        setMeasureMode(activeMode === 'area' ? 'none' : 'area')
      } else if (e.key === 'b' || e.key === 'B') {
        e.preventDefault()
        onToggleBrushSelect?.()
      } else if (e.key === 'Escape') {
        if (activeMode !== 'none') {
          e.preventDefault()
          setMeasureMode('none')
        }
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [activeMode, handleResetNorthPitch, handleToggle3D, handleZoomIn, handleZoomOut, setMeasureMode, onToggleBrushSelect])

  const compassRotation = -bearing

  return (
    <div
      className={`absolute right-4 top-4 z-30 flex flex-col items-end gap-2 font-sans select-none pointer-events-none md:top-4 ${className}`}
      data-testid="map-toolbar-hud"
    >
      {/* Toast Feedback */}
      {feedbackToast && (
        <div
          role="status"
          className="pointer-events-auto rounded-md bg-surface-raised/95 dark:bg-surface-overlay/95 px-2.5 py-1 text-micro font-medium text-ink shadow-agent-md border border-edge-subtle backdrop-blur-md animate-in fade-in slide-in-from-top-1"
        >
          {feedbackToast}
        </div>
      )}

      {/* Measurement Mode Live Floating HUD Panel */}
      {activeMode !== 'none' && (
        <div
          className="pointer-events-auto flex flex-col gap-1.5 rounded-lg border border-edge-subtle bg-surface-raised/95 dark:bg-surface-overlay/90 p-3 text-ink shadow-agent-lg backdrop-blur-md w-64 max-w-[calc(100vw-32px)]"
          data-testid="measurement-active-hud"
        >
          <div className="flex items-center justify-between border-b border-edge-subtle pb-1.5">
            <div className="flex items-center gap-1.5 font-semibold text-meta">
              {activeMode === 'distance' ? (
                <>
                  <Ruler className="h-4 w-4 text-status-accent" />
                  <span>距离测量模式</span>
                </>
              ) : (
                <>
                  <Square className="h-4 w-4 text-status-accent" />
                  <span>面积测量模式</span>
                </>
              )}
            </div>
            <button
              type="button"
              aria-label="退出测量"
              onClick={() => setMeasureMode('none')}
              className="rounded-xs p-0.5 text-ink-muted hover:bg-surface-hover hover:text-ink transition-colors"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>

          <div className="space-y-1 text-micro">
            <div className="flex justify-between text-ink-muted">
              <span>已采集点数:</span>
              <span className="font-mono font-medium text-ink">{points.length} 个</span>
            </div>
            <div className="flex justify-between items-baseline">
              <span className="text-ink-muted">当前测算结果:</span>
              <span className="font-mono text-meta font-bold text-status-accent">
                {measurementSummary?.formatted ?? (activeMode === 'distance' ? '需至少 2 点' : '需至少 3 点')}
              </span>
            </div>
            <p className="text-micro text-ink-disabled pt-0.5 leading-tight">
              在地图上点击添加测量点，双击或点击下方完成保存标注。
            </p>
          </div>

          <div className="flex items-center gap-2 pt-1">
            <button
              type="button"
              disabled={!measurementSummary}
              onClick={handleSaveMeasurement}
              className="flex-1 inline-flex items-center justify-center gap-1 rounded-sm bg-status-accent px-2 py-1 text-micro font-medium text-ink-on-accent transition-opacity hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              <Check className="h-3 w-3" />
              <span>完成标注</span>
            </button>
            <button
              type="button"
              onClick={() => {
                if (onClearMeasurePoints) onClearMeasurePoints()
                else setUncontrolledMeasurePoints([])
              }}
              disabled={points.length === 0}
              className="inline-flex items-center justify-center gap-1 rounded-sm border border-edge-subtle bg-surface-panel px-2 py-1 text-micro font-medium text-ink-secondary hover:bg-surface-hover disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              <RotateCcw className="h-3 w-3" />
              <span>重置</span>
            </button>
          </div>
        </div>
      )}

      {/* Main Floating Toolstrip */}
      <div className="pointer-events-auto flex flex-col items-center rounded-lg border border-edge-subtle bg-surface-raised/90 dark:bg-surface-overlay/85 p-1 shadow-agent-md backdrop-blur-md transition-all">
        {/* Collapse Toggle for compact viewports */}
        <button
          type="button"
          aria-label={collapsed ? '展开工具栏' : '折叠工具栏'}
          title={collapsed ? '展开工具栏' : '折叠工具栏'}
          onClick={() => setCollapsed(!collapsed)}
          className="flex h-7 w-7 items-center justify-center rounded-sm text-ink-muted hover:bg-surface-hover hover:text-ink md:hidden transition-colors"
        >
          {collapsed ? <ChevronDown className="h-4 w-4" /> : <ChevronUp className="h-4 w-4" />}
        </button>

        {!collapsed && (
          <div className="flex flex-col items-center gap-1">
            {/* Group 1: Navigation Controls */}
            <button
              type="button"
              aria-label="放大"
              title="放大 (快捷键: +)"
              onClick={handleZoomIn}
              className="flex h-8 w-8 items-center justify-center rounded-sm text-ink-secondary hover:bg-surface-hover hover:text-ink active:bg-surface-selected transition-colors"
            >
              <ZoomIn className="h-4 w-4" />
            </button>

            <button
              type="button"
              aria-label="缩小"
              title="缩小 (快捷键: -)"
              onClick={handleZoomOut}
              className="flex h-8 w-8 items-center justify-center rounded-sm text-ink-secondary hover:bg-surface-hover hover:text-ink active:bg-surface-selected transition-colors"
            >
              <ZoomOut className="h-4 w-4" />
            </button>

            <button
              type="button"
              aria-label="重置指北与俯仰角"
              title="重置正北与俯仰角 (快捷键: 0)"
              onClick={handleResetNorthPitch}
              className="group relative flex h-8 w-8 items-center justify-center rounded-sm text-ink-secondary hover:bg-surface-hover hover:text-ink transition-colors"
            >
              <Compass
                className="h-4 w-4 transition-transform duration-300 ease-out"
                style={{ transform: `rotate(${compassRotation}deg)` }}
              />
              {pitch > 0 && (
                <span className="absolute bottom-1 right-1 h-1.5 w-1.5 rounded-full bg-status-accent" />
              )}
            </button>

            <button
              type="button"
              aria-label="切换3D视图"
              title={is3D ? '切换为 2D 视图 (快捷键: 3)' : '切换为 3D 视图 (快捷键: 3)'}
              aria-pressed={is3D}
              onClick={handleToggle3D}
              className={`flex h-8 w-8 items-center justify-center rounded-sm transition-colors ${
                is3D
                  ? 'bg-status-accent-soft text-status-accent font-bold'
                  : 'text-ink-secondary hover:bg-surface-hover hover:text-ink'
              }`}
            >
              {is3D ? <Box className="h-4 w-4" /> : <Layers className="h-4 w-4" />}
            </button>

            <div className="my-0.5 h-px w-5 bg-edge-subtle" />

            {/* Group 2: Measurement & Selection Tools */}
            <button
              type="button"
              aria-label="矩形框选"
              title="矩形框选（框选地图要素联动图表/表格，快捷键: B）"
              aria-pressed={brushSelectActive}
              onClick={() => onToggleBrushSelect?.()}
              className={`flex h-8 w-8 items-center justify-center rounded-sm transition-colors ${
                brushSelectActive
                  ? 'bg-status-accent-soft text-status-accent font-bold ring-1 ring-status-accent'
                  : 'text-ink-secondary hover:bg-surface-hover hover:text-ink'
              }`}
            >
              <SquareDashedMousePointer className="h-4 w-4" />
            </button>

            <button
              type="button"
              aria-label="距离测量"
              title="距离测量 (快捷键: D)"
              aria-pressed={activeMode === 'distance'}
              onClick={() => setMeasureMode(activeMode === 'distance' ? 'none' : 'distance')}
              className={`flex h-8 w-8 items-center justify-center rounded-sm transition-colors ${
                activeMode === 'distance'
                  ? 'bg-status-accent-soft text-status-accent font-bold ring-1 ring-status-accent'
                  : 'text-ink-secondary hover:bg-surface-hover hover:text-ink'
              }`}
            >
              <Ruler className="h-4 w-4" />
            </button>

            <button
              type="button"
              aria-label="面积测量"
              title="面积测量 (快捷键: A)"
              aria-pressed={activeMode === 'area'}
              onClick={() => setMeasureMode(activeMode === 'area' ? 'none' : 'area')}
              className={`flex h-8 w-8 items-center justify-center rounded-sm transition-colors ${
                activeMode === 'area'
                  ? 'bg-status-accent-soft text-status-accent font-bold ring-1 ring-status-accent'
                  : 'text-ink-secondary hover:bg-surface-hover hover:text-ink'
              }`}
            >
              <Square className="h-4 w-4" />
            </button>

            <button
              type="button"
              aria-label="清除标注与测量"
              title="清除地图上的所有测量与标注"
              onClick={handleClearAnnotations}
              disabled={annotations.length === 0 && points.length === 0}
              className="flex h-8 w-8 items-center justify-center rounded-sm text-ink-muted hover:bg-status-critical-soft hover:text-status-critical disabled:opacity-30 disabled:hover:bg-transparent disabled:hover:text-ink-muted transition-colors"
            >
              <Trash2 className="h-4 w-4" />
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
