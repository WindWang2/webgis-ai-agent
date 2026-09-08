/**
 * 统一瓦片供应商注册表
 *
 * 替代原来的 constants.ts MAP_STYLES 和 map-styles.ts 的底层瓦片定义。
 * 所有 AI 命令的 BASE_LAYER_CHANGE 匹配也走此表的关键字索引。
 */

export type ProviderId =
  | "carto-light"
  | "carto-dark"
  | "carto-positron"
  | "carto-dark-vec"
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
  /** XYZ / WMTS 瓦片 URL 模板或 GL style JSON URL */
  url: string;
  /** 图层类型: raster 瓦片 或 vector 矢量 */
  type: "raster" | "vector";
  /**
   * 瓦片署名（Review R2 MINOR-5）：对比副视图等无法复用 MapLibre 内建
   * attributionControl 的宿主必须可见地展示（OSM/厂商条款红线）。
   */
  attribution: string;
  /**
   * AI 关键字索引 —— BASE_LAYER_CHANGE 处理器通过这些词命中本条目。
   * 如搜索关键词含 "dark" 则命中 carto-dark，含 "卫星"/"影像" 则命中 esri-img。
   */
  keywords: string[];
}

const _TIANDITU_TOKEN = process.env.NEXT_PUBLIC_TIANDITU_TOKEN || "";

export const TILE_PROVIDERS: TileProvider[] = [
  {
    id: "carto-positron",
    name: "Carto Positron 矢量",
    attribution: "© OpenStreetMap contributors © CARTO",
    url: "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
    type: "vector",
    keywords: ["carto-positron", "positron", "浅色矢量", "学术", "矢量底图"],
  },
  {
    id: "carto-dark-vec",
    name: "Carto Dark Matter 矢量",
    attribution: "© OpenStreetMap contributors © CARTO",
    url: "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
    type: "vector",
    keywords: ["carto-dark-vec", "dark-matter", "深色矢量", "夜间矢量", "大屏"],
  },
  {
    id: "carto-light",
    name: "Carto 浅色",
    attribution: "© OpenStreetMap contributors © CARTO",
    url: "https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png",
    type: "raster",
    keywords: ["浅色", "light", "白色", "亮色"],
  },
  {
    id: "carto-dark",
    name: "Carto 深色",
    attribution: "© OpenStreetMap contributors © CARTO",
    url: "https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png",
    type: "raster",
    keywords: ["深色", "dark", "黑色", "暗色"],
  },
  {
    id: "osm",
    name: "OSM 地图",
    attribution: "© OpenStreetMap contributors",
    url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    type: "raster",
    keywords: ["osm", "街道", "地图", "street"],
  },
  {
    id: "esri-img",
    name: "ESRI 影像",
    attribution: "Esri, Maxar, Earthstar Geographics",
    url: "https://services.arcgisonline.com/arcgis/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    type: "raster",
    keywords: ["影像", "卫星", "satellite", "航拍", "鸟瞰"],
  },
  {
    id: "esri-topo",
    name: "ESRI 地形",
    attribution: "Esri, USGS, NOAA | Esri, HERE, Garmin",
    url: "https://services.arcgisonline.com/arcgis/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
    type: "raster",
    keywords: ["地形", "topo", "晕渲", "terrain"],
  },
  {
    id: "opentopomap",
    name: "OpenTopoMap",
    attribution: "© OpenStreetMap contributors, SRTM | © OpenTopoMap (CC-BY-SA)",
    // #536: `{s}`（Leaflet 时代子域占位符）MapLibre 不展开 —— 其
    // CanonicalTileID.url() 只处理 {prefix}/{z}/{x}/{y}/{ratio}/{quadkey}/
    // {bbox-epsg-3857}；`{s}` 会原样进 hostname → DNS 失败 → 底图空白且无
    // 可见错误。展开为具体子域（保留 CDN 分布）。
    url: "https://a.tile.opentopomap.org/{z}/{x}/{y}.png",
    type: "raster",
    keywords: ["opentopomap", "山体", "等高线"],
  },
  {
    id: "amap-img",
    name: "高德影像",
    attribution: "© 高德地图 AutoNavi",
    url: "https://webst02.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}",
    type: "raster",
    keywords: ["高德影像", "amap img", "高德卫"],
  },
  {
    id: "amap-vec",
    name: "高德矢量",
    attribution: "© 高德地图 AutoNavi",
    url: "https://webrd01.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}",
    type: "raster",
    keywords: ["高德矢量", "amap vec", "高德街"],
  },
  {
    id: "tianditu-vec",
    name: "天地图矢量",
    attribution: "© 国家地理信息公共服务平台 天地图",
    url: `https://t0.tianditu.gov.cn/vec_w/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=vec&STYLE=default&TILEMATRIXSET=w&FORMAT=tiles&TILECOL={x}&TILEROW={y}&TILEMATRIX={z}&tk=${_TIANDITU_TOKEN}`,
    type: "raster",
    keywords: ["天地图矢量", "天地图", "tianditu vec", "tianditu"],
  },
  {
    id: "tianditu-img",
    name: "天地图影像",
    attribution: "© 国家地理信息公共服务平台 天地图",
    url: `https://t0.tianditu.gov.cn/img_w/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=img&STYLE=default&TILEMATRIXSET=w&FORMAT=tiles&TILECOL={x}&TILEROW={y}&TILEMATRIX={z}&tk=${_TIANDITU_TOKEN}`,
    type: "raster",
    keywords: ["天地图影像", "天地图卫星", "天地图卫", "tianditu img", "tianditu satellite"],
  },
];