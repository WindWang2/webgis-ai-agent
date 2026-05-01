/** @deprecated use TILE_PROVIDERS from ./providers */
export type LayerType = "raster" | "style"

/** @deprecated use TileProvider from ./providers */
export interface MapStyleOption {
  name: string
  url: string
  type: LayerType
}

import { TILE_PROVIDERS } from "./providers";

// Re-export for backward compatibility with existing consumer code
export const MAP_STYLES: MapStyleOption[] = TILE_PROVIDERS.map(
  ({ name, url, type }) => ({ name, url, type }),
);
