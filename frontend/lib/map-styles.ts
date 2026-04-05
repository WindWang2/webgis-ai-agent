/**
 * 地图底图样式配置 - OSM + 天地图
 */

type StyleSpecification = any;

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

function getTiandituStyle(token: string): StyleSpecification {
  return {
    version: 8,
    name: "天地图",
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

export type BasemapType = "osm" | "tianditu-vec" | "tianditu-img";

export function getBasemapStyle(type: BasemapType, tiandituToken?: string) {
  switch (type) {
    case "osm":
      return osmStyle;
    case "tianditu-vec":
      return getTiandituStyle(tiandituToken || "");
    case "tianditu-img":
      return getTiandituImgStyle(tiandituToken || "");
    default:
      return osmStyle;
  }
}

export const BASEMAP_OPTIONS: { id: BasemapType; name: string }[] = [
  { id: "osm", name: "OSM 标准地图" },
  { id: "tianditu-vec", name: "天地图 矢量" },
  { id: "tianditu-img", name: "天地图 影像" },
];
