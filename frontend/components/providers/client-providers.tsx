"use client"

import { MapProvider } from "react-map-gl/maplibre"
import { MapActionProvider } from "@/lib/contexts/map-action-context"

export function ClientProviders({ children }: { children: React.ReactNode }) {
  return (
    <MapProvider>
      <MapActionProvider>{children}</MapActionProvider>
    </MapProvider>
  )
}
