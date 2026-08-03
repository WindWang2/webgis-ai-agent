"use client";

import React, { useState } from "react";
import { Sparkles, Map, Palette, Layout, Eye, Sliders } from "lucide-react";

export interface MapCombinatorProps {
  onApplyCombination?: (combination: {
    preset?: string;
    basemap?: string;
    symbology?: string;
    thematic?: string;
    layout?: string;
  }) => void;
}

const PRESETS = [
  { id: "academic_research", name: "🎓 学术论文风", desc: "Positron 底图 + YlGnBu 分位数 + A4 打印版式" },
  { id: "cyber_dark", name: "🌃 深色科技风", desc: "Dark Matter 底图 + 霓虹发光 + Viridis 色板" },
  { id: "natural_terra", name: "🌲 自然地理风", desc: "Esri 遥感影像 + 柔和轮廓 + Spectral 7 级" },
  { id: "heat_density", name: "📊 热力密度风", desc: "Dark 底图 + 核密度热力渐变 + 汇报版式" },
  { id: "engineering_survey", name: "📐 工程勘测风", desc: "OSM 标准底图 + 坐标网格 + Set1 定性色板" },
];

const BASEMAP_OPTIONS = [
  { id: "carto-positron", name: "Carto Positron (极简白)" },
  { id: "carto-dark", name: "Carto Dark Matter (深色)" },
  { id: "esri-imagery", name: "Esri World Imagery (遥感影像)" },
  { id: "osm-standard", name: "OpenStreetMap (标准地理)" },
];

const SYMBOLOGY_OPTIONS = [
  { id: "single", name: "单色矢量符号" },
  { id: "categorical", name: "数据驱动分类符号" },
];

const THEMATIC_OPTIONS = [
  { id: "choropleth", name: "Choropleth 分级色彩" },
  { id: "heatmap", name: "Density 热力密度" },
  { id: "none", name: "无专题配色" },
];

const LAYOUT_OPTIONS = [
  { id: "tmpl_ly_academic", name: "A4 学术论文版式" },
  { id: "tmpl_ly_dark_report", name: "16:9 深色汇报版式" },
  { id: "tmpl_ly_minimal", name: "极简纯享版式" },
  { id: "tmpl_ly_engineering", name: "工程勘测网格版式" },
];

const VIEWPORT_OPTIONS = [
  { id: "auto", name: "自动聚焦要素范围 (Auto Extent)" },
  { id: "china", name: "中国全境视图 (China Overview)" },
  { id: "global", name: "全球视角 (Global View)" },
];

export const MapCombinator: React.FC<MapCombinatorProps> = ({ onApplyCombination }) => {
  const [selectedPreset, setSelectedPreset] = useState<string>("");
  const [basemap, setBasemap] = useState<string>("carto-positron");
  const [symbology, setSymbology] = useState<string>("single");
  const [thematic, setThematic] = useState<string>("choropleth");
  const [layout, setLayout] = useState<string>("tmpl_ly_academic");
  const [viewport, setViewport] = useState<string>("auto");

  const handleApplyPreset = (presetId: string) => {
    setSelectedPreset(presetId);
    if (presetId === "academic_research") {
      setBasemap("carto-positron");
      setSymbology("single");
      setThematic("choropleth");
      setLayout("tmpl_ly_academic");
    } else if (presetId === "cyber_dark") {
      setBasemap("carto-dark");
      setSymbology("single");
      setThematic("choropleth");
      setLayout("tmpl_ly_dark_report");
    } else if (presetId === "natural_terra") {
      setBasemap("esri-imagery");
      setSymbology("single");
      setThematic("choropleth");
      setLayout("tmpl_ly_minimal");
    } else if (presetId === "heat_density") {
      setBasemap("carto-dark");
      setSymbology("single");
      setThematic("heatmap");
      setLayout("tmpl_ly_dark_report");
    } else if (presetId === "engineering_survey") {
      setBasemap("osm-standard");
      setSymbology("single");
      setThematic("choropleth");
      setLayout("tmpl_ly_engineering");
    }
    onApplyCombination?.({ preset: presetId });
  };


  const handleApplyCustom = () => {
    const vpData =
      viewport === "china"
        ? { center: [104.195, 35.861], zoom: 4.5 }
        : viewport === "global"
        ? { center: [0.0, 0.0], zoom: 2.0 }
        : undefined;

    onApplyCombination?.({
      basemap,
      symbology,
      thematic,
      layout,
      ...(vpData ? { viewport: vpData } : {}),
    });
  };

  return (
    <div className="p-4 rounded-xl bg-white/80 dark:bg-slate-900/80 backdrop-blur-md border border-slate-200/80 dark:border-slate-800 shadow-sm text-sm space-y-4">
      <div className="flex items-center justify-between border-b border-slate-100 dark:border-slate-800 pb-2">
        <div className="flex items-center gap-2 font-semibold text-slate-800 dark:text-slate-200">
          <Sliders className="w-4 h-4 text-blue-500" />
          <span>模块化地图自由组合器 (Map Combinator)</span>
        </div>
        <span className="text-xs text-slate-500 dark:text-slate-400">5 正交槽位</span>
      </div>

      {/* Preset Quick Selection */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium text-slate-600 dark:text-slate-400 flex items-center gap-1">
          <Sparkles className="w-3.5 h-3.5 text-amber-500" />
          快捷推荐主题组合
        </label>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
          {PRESETS.map((p) => (
            <button
              key={p.id}
              onClick={() => handleApplyPreset(p.id)}
              className={`p-2 rounded-lg border text-left transition-all ${
                selectedPreset === p.id
                  ? "border-blue-500 bg-blue-50/50 dark:bg-blue-950/40 text-blue-700 dark:text-blue-300"
                  : "border-slate-200 dark:border-slate-800 hover:border-slate-300 dark:hover:border-slate-700 text-slate-700 dark:text-slate-300"
              }`}
            >
              <div className="font-medium text-xs">{p.name}</div>
              <div className="text-[11px] text-slate-500 dark:text-slate-400 truncate">{p.desc}</div>
            </button>
          ))}
        </div>
      </div>

      {/* Custom Component Slot Dropdowns */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-2">
        <div>
          <label className="text-xs font-medium text-slate-600 dark:text-slate-400 flex items-center gap-1 mb-1">
            <Map className="w-3.5 h-3.5 text-indigo-500" />
            1. 底图件 (Basemap)
          </label>
          <select
            value={basemap}
            onChange={(e) => setBasemap(e.target.value)}
            className="w-full text-xs p-2 rounded-lg border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-900 text-slate-800 dark:text-slate-200"
          >
            {BASEMAP_OPTIONS.map((o) => (
              <option key={o.id} value={o.id}>
                {o.name}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="text-xs font-medium text-slate-600 dark:text-slate-400 flex items-center gap-1 mb-1">
            <Eye className="w-3.5 h-3.5 text-emerald-500" />
            2. 符号件 (Symbology)
          </label>
          <select
            value={symbology}
            onChange={(e) => setSymbology(e.target.value)}
            className="w-full text-xs p-2 rounded-lg border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-900 text-slate-800 dark:text-slate-200"
          >
            {SYMBOLOGY_OPTIONS.map((o) => (
              <option key={o.id} value={o.id}>
                {o.name}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="text-xs font-medium text-slate-600 dark:text-slate-400 flex items-center gap-1 mb-1">
            <Palette className="w-3.5 h-3.5 text-rose-500" />
            3. 配色件 (Thematic)
          </label>
          <select
            value={thematic}
            onChange={(e) => setThematic(e.target.value)}
            className="w-full text-xs p-2 rounded-lg border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-900 text-slate-800 dark:text-slate-200"
          >
            {THEMATIC_OPTIONS.map((o) => (
              <option key={o.id} value={o.id}>
                {o.name}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="text-xs font-medium text-slate-600 dark:text-slate-400 flex items-center gap-1 mb-1">
            <Layout className="w-3.5 h-3.5 text-amber-500" />
            4. 版式件 (Layout)
          </label>
          <select
            value={layout}
            onChange={(e) => setLayout(e.target.value)}
            className="w-full text-xs p-2 rounded-lg border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-900 text-slate-800 dark:text-slate-200"
          >
            {LAYOUT_OPTIONS.map((o) => (
              <option key={o.id} value={o.id}>
                {o.name}
              </option>
            ))}
          </select>
        </div>

        <div className="sm:col-span-2">
          <label className="text-xs font-medium text-slate-600 dark:text-slate-400 flex items-center gap-1 mb-1">
            <Map className="w-3.5 h-3.5 text-sky-500" />
            5. 视口件 (Viewport)
          </label>
          <select
            value={viewport}
            onChange={(e) => setViewport(e.target.value)}
            className="w-full text-xs p-2 rounded-lg border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-900 text-slate-800 dark:text-slate-200"
          >
            {VIEWPORT_OPTIONS.map((o) => (
              <option key={o.id} value={o.id}>
                {o.name}
              </option>
            ))}
          </select>
        </div>
      </div>

      <button
        onClick={handleApplyCustom}
        className="w-full py-2 px-3 rounded-lg bg-blue-600 hover:bg-blue-700 text-white font-medium text-xs transition-colors flex items-center justify-center gap-1.5 shadow-sm"
      >
        <Sparkles className="w-3.5 h-3.5" />
        <span>一键生成与应用组合地图 (Assemble MapSpec)</span>
      </button>
    </div>
  );
};

