/** @deprecated use TILE_PROVIDERS from ./providers */
export type LayerType = "raster" | "vector" | "style"

/** @deprecated use TileProvider from ./providers */
export interface MapStyleOption {
  name: string
  url: string
  type: LayerType
  /** G3D01 D7：per-provider 署名（此前在 map-panel 被硬编码 OSM 覆盖） */
  attribution?: string
}

import { TILE_PROVIDERS } from "./providers";

// Re-export for backward compatibility with existing consumer code
export const MAP_STYLES: MapStyleOption[] = TILE_PROVIDERS.map(
  ({ name, url, type, attribution }) => ({ name, url, type, attribution }),
);
