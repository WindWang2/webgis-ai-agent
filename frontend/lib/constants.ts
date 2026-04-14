export type LayerType = "raster" | "style"

export interface MapStyleOption {
  name: string
  url: string
  type: LayerType
}

export const MAP_STYLES: MapStyleOption[] = [
  { name: "Carto 深色", url: "https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png", type: "raster" },
  { name: "OSM 地图", url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png", type: "raster" },
  { name: "ESRI 影像", url: "https://server.arcgisonline.com/ArcGIS/Rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", type: "raster" },
  { name: "Carto 浅色", url: "https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png", type: "raster" },
  { name: "ESRI 地形", url: "https://server.arcgisonline.com/ArcGIS/Rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}", type: "raster" },
  { name: "OpenTopoMap", url: "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", type: "raster" },
  { name: "高德影像", url: "https://webst02.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}", type: "raster" },
]
