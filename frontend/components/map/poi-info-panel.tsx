'use client'

import React, { useMemo, useState, useCallback } from 'react'
import { Copy, Check, Crosshair, X } from 'lucide-react'
import { useHudStore, type HudState } from '@/lib/store/useHudStore'
import { FEATURE_ID_KEYS } from '@/lib/store/layer-data'

/**
 * POI 空间要素信息悬浮窗（点击交互 v3 升级版）。
 *
 * 特性：
 * 1. 纯 DOM 定位，视口自适应边界钳制，不影响 WebGL 画布渲染管线。
 * 2. Framer-motion 平滑出入场微动效 (scale 0.95 -> 1, opacity 0 -> 1)。
 * 3. 一键复制经纬度坐标（带实时 Tooltip 状态反馈）。
 * 4. 「聚焦位置」Zoom-to-feature 快速定位操作。
 * 5. 语义化设计令牌与毛玻璃高对比度样式，严格适配暗色/亮色主题。
 */

export interface PoiPanelFeature {
  layer?: { id?: string }
  properties?: Record<string, unknown>
  geometry?: {
    type?: string
    coordinates?: any
  }
}

export interface PoiInfoPanelProps {
  /** 点击位置（相对地图容器的屏幕像素） */
  x: number
  y: number
  /** 地理坐标 [lng, lat] */
  coordinates?: [number, number]
  features: PoiPanelFeature[]
  layerIds: Set<string>
  layersMap: Record<string, { id?: string; name?: string } | undefined>
  onClose: () => void
  onZoomToFeature?: (target: [number, number] | [number, number, number, number]) => void
}

const MAX_ROWS = 8

/** 解析要素显示名：name 类属性优先，否则首个属性值。 */
export function featureDisplayName(feature: PoiPanelFeature, fallback: string): string {
  const props = (feature.properties || {}) as Record<string, unknown>
  for (const key of ['name', 'NAME', 'Name', '名称']) {
    const v = props[key]
    if (typeof v === 'string' && v.trim()) return v
  }
  const first = Object.values(props)[0]
  if (typeof first === 'string' && first.trim()) return first
  return fallback
}

/** 最长前缀匹配父图层 id（与 map-panel 的 resolveParentLayerId 同语义）。 */
function parentLayerName(
  feature: PoiPanelFeature,
  layerIds: Set<string>,
  layersMap: Record<string, { id?: string; name?: string } | undefined>,
): { id?: string; name?: string } {
  const sub = feature.layer?.id
  if (!sub) return {}
  const sorted = Array.from(layerIds).sort((a, b) => b.length - a.length)
  const parent = sorted.find((id) => sub === id || sub.startsWith(id + '__'))
  if (!parent) return { id: sub }
  return { id: parent, name: layersMap[parent]?.name }
}

export function PoiInfoPanel({
  x,
  y,
  coordinates: propCoordinates,
  features,
  layerIds,
  layersMap,
  onClose,
  onZoomToFeature,
}: PoiInfoPanelProps) {
  const [picked, setPicked] = useState<number>(features.length === 1 ? 0 : -1)
  const [dismissed, setDismissed] = useState(false)
  const [copiedCoords, setCopiedCoords] = useState(false)
  const [copiedPropKey, setCopiedPropKey] = useState<string | null>(null)

  const selectedFeature = useHudStore((s: HudState) => s?.selectedFeature)

  // 提取有效经纬度坐标用于展示、复制和聚焦
  const resolvedCoordinates = useMemo<[number, number] | null>(() => {
    if (propCoordinates && propCoordinates.length === 2) {
      return propCoordinates
    }
    if (selectedFeature?.point && selectedFeature.point.length === 2) {
      return selectedFeature.point as [number, number]
    }
    const currentFeat = features[picked >= 0 ? picked : 0]
    const geom = currentFeat?.geometry
    if (geom && geom.type === 'Point' && Array.isArray(geom.coordinates) && geom.coordinates.length >= 2) {
      return [geom.coordinates[0], geom.coordinates[1]]
    }
    return null
  }, [propCoordinates, selectedFeature, features, picked])

  const entries = useMemo(
    () =>
      features.map((f, i) => {
        const meta = parentLayerName(f, layerIds, layersMap)
        const parentId = meta.id
        let effectiveProps = (f.properties || {}) as Record<string, unknown>
        let effectiveFeature: PoiPanelFeature = f
        if (selectedFeature && parentId && selectedFeature.layerId === parentId) {
          const fid = selectedFeature.featureId
          const hasUsableFid =
            fid != null && String(fid).trim() !== '' && !String(fid).startsWith('h-')
          const fProps = (f.properties || {}) as Record<string, unknown>
          const candidateIds = FEATURE_ID_KEYS.map((k) => (fProps as Record<string, unknown>)[k]).filter(
            (v) => v != null && v !== '',
          )
          const matchesId = hasUsableFid && candidateIds.some((v) => String(v) === String(fid))
          const isSingleOrFirst = features.length === 1 || i === 0
          const shouldMerge = hasUsableFid ? matchesId : isSingleOrFirst
          if (shouldMerge) {
            effectiveProps = (selectedFeature.properties || {}) as Record<string, unknown>
            effectiveFeature = { ...f, properties: effectiveProps }
          }
        }
        return {
          idx: i,
          layerName: meta.name || meta.id || `要素 ${i + 1}`,
          title: featureDisplayName(effectiveFeature, `要素 ${i + 1}`),
          props: effectiveProps,
          rawFeature: f,
        }
      }),
    [features, layerIds, layersMap, selectedFeature],
  )

  const handleCopyCoords = useCallback(() => {
    if (!resolvedCoordinates) return
    const [lng, lat] = resolvedCoordinates
    const text = `${lng.toFixed(6)}, ${lat.toFixed(6)}`
    if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(text).catch(() => {})
    }
    setCopiedCoords(true)
    setTimeout(() => setCopiedCoords(false), 1800)
  }, [resolvedCoordinates])

  const handleCopyProperty = useCallback((key: string, val: unknown) => {
    if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(String(val)).catch(() => {})
    }
    setCopiedPropKey(key)
    setTimeout(() => setCopiedPropKey(null), 1500)
  }, [])

  const handleZoom = useCallback(() => {
    if (!onZoomToFeature) return
    if (selectedFeature?.bbox) {
      onZoomToFeature(selectedFeature.bbox as [number, number, number, number])
    } else if (resolvedCoordinates) {
      onZoomToFeature(resolvedCoordinates)
    }
  }, [onZoomToFeature, selectedFeature, resolvedCoordinates])

  if (dismissed || entries.length === 0) return null

  const safeX = typeof x === 'number' && !Number.isNaN(x) ? x : 150
  const safeY = typeof y === 'number' && !Number.isNaN(y) ? y : 150
  const above = safeY > 220
  // 右缘与左缘点击钳制——面板 translate(-50%)，保证在移动端与桌面端不超出可视区域
  const maxInnerW = typeof window !== 'undefined' && window.innerWidth ? window.innerWidth : 1920
  const clampedX = Math.min(Math.max(safeX, 150), maxInnerW - 150)

  const style: React.CSSProperties = {
    position: 'absolute',
    left: clampedX,
    top: above ? safeY - 14 : safeY + 14,
    transform: above ? 'translate(-50%, -100%)' : 'translate(-50%, 0)',
    zIndex: 40,
  }

  const stop = (e: React.SyntheticEvent) => e.stopPropagation()
  const close = () => {
    setDismissed(true)
    onClose()
  }

  const current = picked >= 0 ? entries[picked] : null

  return (
    <div
      style={style}
      className="w-72 max-w-[calc(100vw-32px)] overflow-hidden rounded-lg border border-edge-subtle bg-surface-raised/95 dark:bg-surface-overlay/90 backdrop-blur-md shadow-agent-lg text-ink font-sans transition-all animate-in fade-in zoom-in-95 duration-150"
      onPointerDown={stop}
      onClick={stop}
      onDoubleClick={stop}
      data-testid="poi-info-panel"
    >
      {/* Header */}
      <div className="flex items-center justify-between border-b border-edge-subtle bg-surface-panel/80 px-2.5 py-1.5">
        <div
          className="truncate font-sans text-meta font-semibold text-ink"
          title={current ? current.layerName : `同一点 ${entries.length} 个要素`}
        >
          {current ? current.layerName : `选择要素（${entries.length}）`}
        </div>

        <div className="flex items-center gap-1 shrink-0">
          {/* Zoom to feature action */}
          {onZoomToFeature && resolvedCoordinates && (
            <button
              type="button"
              aria-label="聚焦位置"
              title="聚焦到要素所在位置"
              onClick={handleZoom}
              className="flex h-6 w-6 items-center justify-center rounded-xs text-ink-muted hover:bg-surface-hover hover:text-status-accent transition-colors"
            >
              <Crosshair className="h-3.5 w-3.5" />
            </button>
          )}

          {/* Copy Coordinates action */}
          {resolvedCoordinates && (
            <button
              type="button"
              aria-label={copiedCoords ? '已复制经纬度坐标' : '复制坐标'}
              title={copiedCoords ? '已复制经纬度坐标' : '复制经纬度坐标'}
              onClick={handleCopyCoords}
              className={`flex h-6 w-6 items-center justify-center rounded-xs transition-colors ${
                copiedCoords
                  ? 'text-status-accent'
                  : 'text-ink-muted hover:bg-surface-hover hover:text-ink'
              }`}
            >
              {copiedCoords ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
            </button>
          )}

          {/* Close button */}
          <button
            type="button"
            aria-label="关闭"
            title="关闭"
            className="flex h-6 w-6 items-center justify-center rounded-xs text-ink-muted hover:bg-surface-hover hover:text-ink transition-colors"
            onClick={close}
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      {/* Approximate Data Warning */}
      {selectedFeature?.isApproximate === true && (
        <div
          className="border-b border-edge-subtle bg-status-warning-soft px-2.5 py-1 font-sans text-micro text-status-warning"
          role="status"
        >
          瓦片近似数据，正在核实…
        </div>
      )}

      {/* Current Feature Details */}
      {current ? (
        <div className="p-2.5 font-sans text-meta">
          {/* Title & Coordinates Subtitle */}
          <div className="mb-1.5">
            <div className="truncate font-semibold text-ink leading-snug" title={current.title}>
              {current.title}
            </div>
            {resolvedCoordinates && (
              <div className="mt-1 flex items-center justify-between rounded bg-surface-sunken/60 px-1.5 py-0.5 text-micro font-mono text-ink-muted">
                <span>
                  {resolvedCoordinates[0].toFixed(5)}, {resolvedCoordinates[1].toFixed(5)}
                </span>
                <button
                  type="button"
                  onClick={handleCopyCoords}
                  className="hover:text-ink transition-colors flex items-center gap-0.5"
                  title="复制经纬度"
                >
                  {copiedCoords ? (
                    <span className="text-status-accent flex items-center gap-0.5">
                      <Check className="h-3 w-3" /> 已复制
                    </span>
                  ) : (
                    <Copy className="h-3 w-3" />
                  )}
                </button>
              </div>
            )}
          </div>

          {/* Properties Table */}
          <div className="max-h-52 space-y-1 overflow-y-auto pr-0.5">
            {Object.entries(current.props).slice(0, MAX_ROWS).map(([k, v]) => (
              <div
                key={k}
                onClick={() => handleCopyProperty(k, v)}
                title="点击复制属性值"
                className="group flex justify-between items-baseline gap-2.5 rounded px-1 py-0.5 hover:bg-surface-hover transition-colors cursor-pointer"
              >
                <span className="shrink-0 font-mono text-micro text-ink-muted">{k}:</span>
                <div className="flex items-center gap-1 overflow-hidden">
                  <span className="break-all text-right font-mono text-micro text-ink">
                    {String(v)}
                  </span>
                  {copiedPropKey === k && (
                    <Check className="h-3 w-3 shrink-0 text-status-accent" />
                  )}
                </div>
              </div>
            ))}
            {Object.keys(current.props).length > MAX_ROWS && (
              <div className="text-micro italic text-ink-muted pt-0.5">
                ...以及其他 {Object.keys(current.props).length - MAX_ROWS} 个属性
              </div>
            )}
            {Object.keys(current.props).length === 0 && (
              <div className="text-micro italic text-ink-muted">（无属性）</div>
            )}
          </div>

          {/* Back Button for multi-feature hits */}
          {entries.length > 1 && (
            <button
              type="button"
              className="mt-2 flex w-full items-center gap-1 rounded-sm px-1.5 py-1 text-left text-micro font-medium text-ink-muted hover:bg-surface-hover hover:text-ink transition-colors"
              onClick={() => setPicked(-1)}
            >
              ← 返回要素列表
            </button>
          )}
        </div>
      ) : (
        /* Multi-Feature Candidate List */
        <div className="p-1 font-sans text-meta">
          {entries.map((e) => (
            <button
              key={e.idx}
              type="button"
              className="block w-full rounded-xs px-1 py-0.5 text-left hover:bg-surface-hover transition-colors"
              onClick={() => setPicked(e.idx)}
            >
              <span className="block truncate font-semibold text-ink" title={e.title}>
                {e.title}
              </span>
              <span className="block truncate font-mono text-micro text-ink-muted">
                {e.layerName}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
