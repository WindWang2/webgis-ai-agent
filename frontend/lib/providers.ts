/**
 * 统一瓦片供应商注册表
 *
 * 替代原来的 constants.ts MAP_STYLES 和 map-styles.ts 的底层瓦片定义。
 * 所有 AI 命令的 BASE_LAYER_CHANGE 匹配也走此表的关键字索引。
 */

export type ProviderId =
  | "carto-light"
  | "carto-dark"
  | "osm"
  | "esri-img"
  | "esri-topo"
  | "opentopomap"
  | "amap-vec"
  | "amap-img"
  | "tianditu-vec"
  | "tianditu-img";

export interface TileProvider {
  /** 唯一标识，如 "amap-vec" */
  id: ProviderId;
  /** 中文展示名，同时也是 AI 指令中的自然语言目标 */
  name: string;
  /** XYZ / WMTS 瓦片 URL 模板 */
  url: string;
  /** 图层类型，当前全为 raster */
  type: "raster";
  /**
   * AI 关键字索引 —— BASE_LAYER_CHANGE 处理器通过这些词命中本条目。
   * 如搜索关键词含 "dark" 则命中 carto-dark，含 "卫星"/"影像" 则命中 esri-img。
   */
  keywords: string[];
}

const _TIANDITU_TOKEN = process.env.NEXT_PUBLIC_TIANDITU_TOKEN || "";

export const TILE_PROVIDERS: TileProvider[] = [
  {
    id: "carto-light",
    name: "Carto 浅色",
    url: "https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png",
    type: "raster",
    keywords: ["浅色", "light", "白色", "亮色"],
  },
  {
    id: "carto-dark",
    name: "Carto 深色",
    url: "https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png",
    type: "raster",
    keywords: ["深色", "dark", "黑色", "暗色"],
  },
  {
    id: "osm",
    name: "OSM 地图",
    url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    type: "raster",
    keywords: ["osm", "街道", "地图", "street"],
  },
  {
    id: "esri-img",
    name: "ESRI 影像",
    url: "https://services.arcgisonline.com/arcgis/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    type: "raster",
    keywords: ["影像", "卫星", "satellite", "航拍", "鸟瞰"],
  },
  {
    id: "esri-topo",
    name: "ESRI 地形",
    url: "https://services.arcgisonline.com/arcgis/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
    type: "raster",
    keywords: ["地形", "topo", "晕渲", "terrain"],
  },
  {
    id: "opentopomap",
    name: "OpenTopoMap",
    url: "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
    type: "raster",
    keywords: ["opentopomap", "山体", "等高线"],
  },
  {
    id: "amap-img",
    name: "高德影像",
    url: "https://webst02.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}",
    type: "raster",
    keywords: ["高德影像", "amap img", "高德卫"],
  },
  {
    id: "amap-vec",
    name: "高德矢量",
    url: "https://webrd01.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}",
    type: "raster",
    keywords: ["高德矢量", "amap vec", "高德街"],
  },
  {
    id: "tianditu-vec",
    name: "天地图矢量",
    url: `https://t0.tianditu.gov.cn/vec_w/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=vec&STYLE=default&TILEMATRIXSET=w&FORMAT=tiles&TILECOL={x}&TILEROW={y}&TILEMATRIX={z}&tk=${_TIANDITU_TOKEN}`,
    type: "raster",
    keywords: ["天地图矢量", "天地图", "tianditu vec", "tianditu"],
  },
  {
    id: "tianditu-img",
    name: "天地图影像",
    url: `https://t0.tianditu.gov.cn/img_w/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=img&STYLE=default&TILEMATRIXSET=w&FORMAT=tiles&TILECOL={x}&TILEROW={y}&TILEMATRIX={z}&tk=${_TIANDITU_TOKEN}`,
    type: "raster",
    keywords: ["天地图影像", "天地图卫星", "天地图卫", "tianditu img", "tianditu satellite"],
  },
];