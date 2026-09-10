"use client"
import React, { useEffect } from "react"
import { Popup, type MapRef } from "react-map-gl/maplibre"
import { useHoverTooltip } from "@/lib/hooks/use-hover-tooltip"
import type { Layer } from "@/lib/types/layer"

export interface HoverTooltipOverlayProps {
  mapRef: React.RefObject<MapRef | null>
  interactiveIdsRef: React.RefObject<string[]>
  layerIdsSetRef: React.RefObject<Set<string>>
  layersMapRef: React.RefObject<Record<string, Layer>>
  visible?: boolean
}

/**
 * FRONT-04: Isolated hover tooltip overlay.
 * Encapsulates the rAF-throttled hoverInfo state and event listeners so that
 * mousemove/mouseout events at 60fps do not trigger re-render cascades on the
 * top-level MapPanel component.
 */
export function HoverTooltipOverlay({
  mapRef,
  interactiveIdsRef,
  layerIdsSetRef,
  layersMapRef,
  visible = true,
}: HoverTooltipOverlayProps) {
  const { hoverInfo, handleMapMouseMove, handleMapMouseOut } = useHoverTooltip({
    mapRef,
    interactiveIdsRef,
    layerIdsSetRef,
    layersMapRef,
  })

  useEffect(() => {
    const map = mapRef.current?.getMap()
    if (!map) return
    map.on("mousemove", handleMapMouseMove)
    map.on("mouseout", handleMapMouseOut)
    return () => {
      map.off("mousemove", handleMapMouseMove)
      map.off("mouseout", handleMapMouseOut)
    }
  }, [mapRef, handleMapMouseMove, handleMapMouseOut])

  if (!visible || !hoverInfo) return null

  return (
    <Popup
      longitude={hoverInfo.point[0]}
      latitude={hoverInfo.point[1]}
      anchor="bottom"
      closeOnClick={false}
      closeButton={false}
    >
      <div className="p-1 font-sans text-meta">
        <div
          className="mb-1 truncate border-b border-map-chrome-border pb-1 font-semibold text-map-chrome-ink"
          title={hoverInfo.layerName}
        >
          {hoverInfo.layerName}
        </div>
        <div className="min-w-[120px] space-y-0.5">
          {Object.entries(hoverInfo.props).map(([k, v]) => (
            <div key={k} className="flex justify-between gap-4">
              <span className="font-mono text-map-chrome-ink-muted">{k}:</span>
              <span className="break-all font-mono text-map-chrome-ink">{String(v)}</span>
            </div>
          ))}
        </div>
      </div>
    </Popup>
  )
}
