export type LayerType = "raster" | "style"

export interface MapStyleOption {
  name: string
  url: string
  type: LayerType
}

const TIANDITU_TK = process.env.NEXT_PUBLIC_TIANDITU_TOKEN || ""

export const MAP_STYLES: MapStyleOption[] = [
  { name: "Carto 浅色", url: "https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png", type: "raster" },
  { name: "Carto 深色", url: "https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png", type: "raster" },
  { name: "OSM 地图", url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png", type: "raster" },
  { name: "ESRI 影像", url: "https://server.arcgisonline.com/ArcGIS/Rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", type: "raster" },
  { name: "ESRI 地形", url: "https://server.arcgisonline.com/ArcGIS/Rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}", type: "raster" },
  { name: "OpenTopoMap", url: "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", type: "raster" },
  // 高德影像 (no key needed for tile access)
  { name: "高德影像", url: "https://webst02.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}", type: "raster" },
  // 高德矢量 (no key needed for tile access)
  { name: "高德矢量", url: "https://webrd01.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}", type: "raster" },
  // 天地图矢量
  { name: "天地图矢量", url: `https://t0.tianditu.gov.cn/vec_w/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=vec&STYLE=default&TILEMATRIXSET=w&FORMAT=tiles&TILECOL={x}&TILEROW={y}&TILEMATRIX={z}&tk=${TIANDITU_TK}`, type: "raster" },
  // 天地图影像
  { name: "天地图影像", url: `https://t0.tianditu.gov.cn/img_w/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=img&STYLE=default&TILEMATRIXSET=w&FORMAT=tiles&TILECOL={x}&TILEROW={y}&TILEMATRIX={z}&tk=${TIANDITU_TK}`, type: "raster" },
]
