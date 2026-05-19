/**
 * Demo / 默认数据 — 从 useHudStore.ts 抽离，避免大 store 文件夹杂常量。
 * 这些都是 UI demo seed 或首装默认，不参与持久化（持久化由 partialize 控制）。
 */
import type { GeoJSONFeatureCollection } from '@/lib/types';

export const DEMO_MESSAGES = [
  {
    id: '1',
    role: 'assistant' as const,
    content:
      '你好！我是 GeoAgent。\n\n我感知地图、分析空间、生成洞察——地图上的一切都是我的一部分。\n\n试着告诉我：\n- 分析北京市学校分布密度\n- 成都市人口热力图\n- 计算各区 POI覆盖率',
    timestamp: '14:30',
  },
];

export const DEMO_LAYERS = [
  { id: 'poi-schools', name: '北京市学校 POI', type: 'vector', visible: true, opacity: 1, color: '#16a34a', group: 'analysis', info: '312 个要素 · query_osm_poi', mockPoints: [[22,18],[28,22],[35,28],[42,15],[50,32],[55,25],[60,18],[38,42],[46,38],[62,45]], style: { color: '#16a34a' }, source: { type: 'FeatureCollection', features: [] } as unknown as GeoJSONFeatureCollection },
  { id: 'heatmap-density', name: '密度热力图', type: 'heatmap', visible: true, opacity: 0.9, color: '#ff5f00', group: 'analysis', info: '核密度估计 · kde_surface', mockPoints: [[30,25],[35,30],[40,28],[38,22],[33,20],[45,35],[50,28],[44,22]], style: { color: '#ff5f00' }, source: { type: 'FeatureCollection', features: [] } as unknown as GeoJSONFeatureCollection },
  { id: 'boundary-districts', name: '北京市行政区划', type: 'vector', visible: false, opacity: 1, color: '#2563eb', group: 'reference', info: '16 个区 · get_district', mockPoints: [], style: { color: '#2563eb' }, source: { type: 'FeatureCollection', features: [] } as unknown as GeoJSONFeatureCollection },
];

export const DEMO_RAG = [
  { id: '1', source: 'GIS空间分析方法论.pdf', score: '0.92', chunks: 4, excerpts: ['核密度估计（KDE）是一种非参数方法，用于估计随机变量的概率密度函数。在 GIS 中，常用于分析点要素的空间分布密度...', '带宽选择是 KDE 的关键参数，过小会造成过拟合，过大则会掩盖局部模式。常用的带宽选择方法包括 Silverman 规则...'] },
  { id: '2', source: '北京市空间数据手册v3.md', score: '0.87', chunks: 2, excerpts: ['北京市共辖 16 个区，总面积 16410 平方公里。核心区包括东城区和西城区...', '2023年北京市常住人口 2185 万人，其中城镇人口 1891 万人，城镇化率为 86.6%...'] },
  { id: '3', source: 'OpenStreetMap POI 分类标准.pdf', score: '0.79', chunks: 1, excerpts: ['教育设施分类标准...'] },
];

export const DEMO_EXPORTS = [
  { id: '1', name: '北京学校密度专题图.png', type: 'png' as const, size: '2.4 MB', date: '刚刚' },
  { id: '2', name: '核密度分析报告.pdf', type: 'pdf' as const, size: '840 KB', date: '刚刚' },
  { id: '3', name: '学校POI数据.geojson', type: 'geojson' as const, size: '156 KB', date: '刚刚' },
];

export const DEMO_OPS_LOG = [
  { id: '1', type: 'add' as const, label: '添加图层 — POI 查询结果', time: '14:30', detail: '123 个要素' },
  { id: '2', type: 'flyto' as const, label: '飞到 — 目标区域', time: '14:31', detail: 'zoom 11.5' },
  { id: '3', type: 'add' as const, label: '添加图层 — 密度热力图', time: '14:32', detail: 'kde_surface 输出' },
];

export const DEMO_CAUSAL_CHAIN = [
  { id: '1', tool: 'geocode_cn', mapAction: 'fly_to', time: '14:30', toolInput: '北京市', mapEffect: '地图飞至目标位置', mapState: { center: [116.40,39.90], zoom: 10 } },
  { id: '2', tool: 'query_osm_poi', mapAction: 'add_layer', time: '14:31', toolInput: 'category=school, city=北京市', mapEffect: '新增 POI 图层', mapState: { layer_id: 'poi-schools', feature_count: 312 } },
  { id: '3', tool: 'kde_surface', mapAction: 'add_layer', time: '14:32', toolInput: 'layer_id=poi-schools, bandwidth=500m', mapEffect: '新增热力图图层', mapState: { layer_id: 'heatmap-density', render_type: 'native_heatmap' } },
];

export const DEFAULT_MCP_SERVERS = [
  { id: 'gdal-raster', name: 'gdal-raster', transport: 'stdio' as const, cmd: 'python mcp_servers/gdal_raster.py', status: 'active' as const, desc: '栅格数据处理（重采样、裁切、投影）' },
  { id: 'spatial-analysis', name: 'spatial-analysis', transport: 'stdio' as const, cmd: 'python mcp_servers/spatial_analysis.py', status: 'active' as const, desc: '空间分析算法库（缓冲区、叠加、统计）' },
  { id: 'gdal-vector', name: 'gdal-vector', transport: 'stdio' as const, cmd: 'python mcp_servers/gdal_vector.py', status: 'active' as const, desc: '矢量数据读写与格式转换' },
  { id: 'gdal-dem-source', name: 'gdal-dem-source', transport: 'stdio' as const, cmd: 'python mcp_servers/gdal_dem_source.py', status: 'inactive' as const, desc: '高程数据源接入（需 OpenTopography Key）', warn: true },
];

export const DEFAULT_SKILLS = [
  { id: 'poi', name: 'POI 查询', desc: '通过 Overpass 查询兴趣点，支持多种分类', enabled: true, calls: 0, category: '数据获取' },
  { id: 'ndvi', name: 'NDVI 植被分析', desc: '基于遥感影像计算归一化植被指数', enabled: true, calls: 0, category: '遥感分析' },
  { id: 'heatmap', name: '人口密度热力图', desc: '核密度估计 (KDE) 生成连续密度表面', enabled: true, calls: 0, category: '空间分析' },
  { id: 'buffer', name: '缓冲区分析', desc: '生成点线面的等距缓冲区', enabled: true, calls: 0, category: '空间分析' },
  { id: 'network', name: '路网分析', desc: '最短路径、服务区、行驶时间等图论分析', enabled: false, calls: 0, category: '网络分析' },
  { id: 'overlay', name: '叠加分析', desc: '空间相交、联合、裁切等拓扑运算', enabled: true, calls: 0, category: '空间分析' },
  { id: 'viewshed', name: '可视域分析', desc: '基于 DEM 计算观测点可见范围', enabled: false, calls: 0, category: '地形分析' },
  { id: 'grid', name: '格网统计', desc: '渔网格网生成与属性聚合统计', enabled: true, calls: 0, category: '空间分析' },
  { id: 'report', name: '报告生成', desc: '一键生成带图表的 PDF/HTML 分析报告', enabled: true, calls: 0, category: '输出' },
  { id: 'choropleth', name: '专题地图', desc: '分位数、自然断点等方法的分级设色', enabled: true, calls: 0, category: '制图' },
];

export const DEFAULT_MAP_STYLES = [
  { id: 0, name: 'OSM Voyager', desc: '清晰浅色地图' },
  { id: 1, name: 'OSM Dark', desc: '深色街道地图' },
  { id: 2, name: 'Satellite', desc: '卫星影像底图' },
  { id: 3, name: 'Topo', desc: '地形晕渲底图' },
  { id: 4, name: 'Blank White', desc: '空白白色画布' },
];
