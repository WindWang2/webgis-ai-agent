/**
 * 地图底图样式配置 - OSM + 天地图 + 高德
 */

type StyleSpecification = any;

const TIANDITU_TOKEN = process.env.NEXT_PUBLIC_TIANDITU_TOKEN || "";

const osmStyle: StyleSpecification = {
  version: 8,
  name: "OSM Standard",
  sources: {
    osm: {
      type: "raster",
      tiles: [
        "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
      ],
      tileSize: 256,
      attribution: "© OpenStreetMap contributors",
    },
  },
  layers: [
    {
      id: "osm-tiles",
      type: "raster",
      source: "osm",
      minzoom: 0,
      maxzoom: 19,
    },
  ],
};

function getTiandituVecStyle(token: string): StyleSpecification {
  return {
    version: 8,
    name: "天地图矢量",
    sources: {
      tianditu_vec: {
        type: "raster",
        tiles: [
          `https://t{s}.tianditu.gov.cn/vec_w/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=vec&STYLE=default&TILEMATRIXSET=w&FORMAT=tiles&TILECOL={x}&TILEROW={y}&TILEMATRIX={z}&tk=${token}`,
        ],
        tileSize: 256,
        attribution: "© 天地图",
      },
      tianditu_cva: {
        type: "raster",
        tiles: [
          `https://t{s}.tianditu.gov.cn/cva_w/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=cva&STYLE=default&TILEMATRIXSET=w&FORMAT=tiles&TILECOL={x}&TILEROW={y}&TILEMATRIX={z}&tk=${token}`,
        ],
        tileSize: 256,
      },
    },
    layers: [
      {
        id: "tianditu-vec",
        type: "raster",
        source: "tianditu_vec",
        minzoom: 0,
        maxzoom: 18,
      },
      {
        id: "tianditu-cva",
        type: "raster",
        source: "tianditu_cva",
        minzoom: 0,
        maxzoom: 18,
      },
    ],
  };
}

function getTiandituImgStyle(token: string): StyleSpecification {
  return {
    version: 8,
    name: "天地图影像",
    sources: {
      tianditu_img: {
        type: "raster",
        tiles: [
          `https://t{s}.tianditu.gov.cn/img_w/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=img&STYLE=default&TILEMATRIXSET=w&FORMAT=tiles&TILECOL={x}&TILEROW={y}&TILEMATRIX={z}&tk=${token}`,
        ],
        tileSize: 256,
        attribution: "© 天地图",
      },
      tianditu_cia: {
        type: "raster",
        tiles: [
          `https://t{s}.tianditu.gov.cn/cia_w/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=cia&STYLE=default&TILEMATRIXSET=w&FORMAT=tiles&TILECOL={x}&TILEROW={y}&TILEMATRIX={z}&tk=${token}`,
        ],
        tileSize: 256,
      },
    },
    layers: [
      {
        id: "tianditu-img",
        type: "raster",
        source: "tianditu_img",
        minzoom: 0,
        maxzoom: 18,
      },
      {
        id: "tianditu-cia",
        type: "raster",
        source: "tianditu_cia",
        minzoom: 0,
        maxzoom: 18,
      },
    ],
  };
}

const amapVecStyle: StyleSpecification = {
  version: 8,
  name: "高德矢量",
  sources: {
    amap_vec: {
      type: "raster",
      tiles: [
        "https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}",
      ],
      tileSize: 256,
      attribution: "© 高德地图",
    },
  },
  layers: [
    {
      id: "amap-vec",
      type: "raster",
      source: "amap_vec",
      minzoom: 0,
      maxzoom: 18,
    },
  ],
};

const amapImgStyle: StyleSpecification = {
  version: 8,
  name: "高德影像",
  sources: {
    amap_img: {
      type: "raster",
      tiles: [
        "https://webst0{s}.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}",
      ],
      tileSize: 256,
      attribution: "© 高德地图",
    },
    amap_cia: {
      type: "raster",
      tiles: [
        "https://webst0{s}.is.autonavi.com/appmaptile?style=8&x={x}&y={y}&z={z}",
      ],
      tileSize: 256,
    },
  },
  layers: [
    {
      id: "amap-img",
      type: "raster",
      source: "amap_img",
      minzoom: 0,
      maxzoom: 18,
    },
    {
      id: "amap-cia",
      type: "raster",
      source: "amap_cia",
      minzoom: 0,
      maxzoom: 18,
    },
  ],
};

export type BasemapType = "osm" | "tianditu-vec" | "tianditu-img" | "amap-vec" | "amap-img";

export function getBasemapStyle(type: BasemapType, tiandituToken?: string) {
  const token = tiandituToken || TIANDITU_TOKEN;
  switch (type) {
    case "osm":
      return osmStyle;
    case "tianditu-vec":
      return getTiandituVecStyle(token);
    case "tianditu-img":
      return getTiandituImgStyle(token);
    case "amap-vec":
      return amapVecStyle;
    case "amap-img":
      return amapImgStyle;
    default:
      return osmStyle;
  }
}

export const BASEMAP_OPTIONS: { id: BasemapType; name: string }[] = [
  { id: "osm", name: "OSM 标准地图" },
  { id: "tianditu-vec", name: "天地图 矢量" },
  { id: "tianditu-img", name: "天地图 影像" },
  { id: "amap-vec", name: "高德 矢量" },
  { id: "amap-img", name: "高德 影像" },
];
